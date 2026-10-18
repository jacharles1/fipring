"""End-to-end sync against a simulated ring: auth, drain, storage, timestamps."""
import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base, Device, RingEvent
from ring import store
from ring.auth import encrypt_nonce
from ring.client import OuraClient
from ring.events import ProtocolError

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
NONCE = bytes(range(15))
ANCHOR_UNIX = 1_790_000_000


def ev(tag, ts, body=b""):
    payload = ts.to_bytes(4, "little") + body
    return bytes([tag, len(payload)]) + payload


class FakeRing:
    """Answers requests the way the ring does. Supports legacy GetEvent only, like
    rings that reject the extended fetch."""

    def __init__(self, events, key=KEY):
        self.frames = asyncio.Queue()
        self.events = sorted(events, key=lambda e: int.from_bytes(e[2:6], "little"))
        self.key = key
        self.writes = []

    def push(self, *frames):
        for f in frames:
            self.frames.put_nowait(f)

    async def write(self, data: bytes):
        self.writes.append(data)
        tag = data[0]
        if data[:3] == bytes([0x2F, 0x01, 0x2B]):
            self.push(bytes([0x2F, 16, 0x2C]) + NONCE)
        elif data[:3] == bytes([0x2F, 0x11, 0x2D]):
            ok = data[3:] == encrypt_nonce(self.key, NONCE)
            self.push(bytes([0x2F, 2, 0x2E, 0 if ok else 1]))
        elif data[:3] == bytes([0x2F, 0x0C, 0x41]):
            self.push(bytes([0x2F, 1, 0x00]))  # extended fetch unsupported
        elif tag == 0x28:
            self.push(bytes([0x29, 1, 0]))
        elif tag == 0x10:
            start = int.from_bytes(data[2:6], "little")
            batch = [e for e in self.events if int.from_bytes(e[2:6], "little") >= start][:3]
            remaining = len([e for e in self.events if int.from_bytes(e[2:6], "little") >= start]) - len(batch)
            self.push(*batch, bytes([0x11, 6, len(batch), 0]) + (remaining * 10).to_bytes(4, "little"))


@pytest.fixture
def db():
    # Set TEST_DATABASE_URL to run against Postgres instead of in-memory SQLite.
    engine = create_engine(os.getenv("TEST_DATABASE_URL", "sqlite://"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def run(coro):
    return asyncio.run(coro)


def test_authenticate():
    run(OuraClient(FakeRing([]), quiet=0.05).authenticate(KEY))
    with pytest.raises(ProtocolError, match="wrong key"):
        run(OuraClient(FakeRing([]), quiet=0.05).authenticate(bytes(16)))


def test_drain_stores_events_and_dates_them(db):
    events = [
        ev(0x42, 1000, ANCHOR_UNIX.to_bytes(4, "little")),  # time_sync anchor
        ev(0x5D, 1100, bytes([50, 60])),                     # hrv: 10 s later
        ev(0x46, 1200, (3350).to_bytes(2, "little")),
        ev(0x43, 1300, b"hello"),
        ev(0x5D, 1400, bytes([48, 70])),
    ]
    ring = FakeRing(events)
    client = OuraClient(ring, quiet=0.05)

    async def go():
        async def on_batch(evs, cursor, left):
            store.commit_batch(db, "S1", evs, cursor)
        return await client.drain_events(0, on_batch)

    cursor = run(go())
    assert cursor == 1401
    assert db.scalar(select(func.count()).select_from(RingEvent)) == 5
    assert db.get(Device, "S1").cursor == 1401

    store.assign_timestamps(db, "S1")
    hrv = db.scalars(select(RingEvent).where(RingEvent.name == "hrv_event").order_by(RingEvent.ring_ts)).first()
    assert hrv.ts.replace(tzinfo=timezone.utc) == datetime.fromtimestamp(ANCHOR_UNIX + 10, timezone.utc)
    assert hrv.decoded["rmssd_ms"] == [60]

    # Re-syncing from the start is harmless: duplicates are ignored.
    async def again():
        async def on_batch(evs, cursor, left):
            store.commit_batch(db, "S1", evs, cursor)
        await OuraClient(FakeRing(events), quiet=0.05).drain_events(0, on_batch)
    run(again())
    assert db.scalar(select(func.count()).select_from(RingEvent)) == 5
