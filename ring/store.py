"""Persist ring events and the sync cursor."""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import Device, RingEvent
from ring import events as ev
from ring.timeline import ANCHOR_TAGS, Timeline


def _insert_ignore(session: Session):
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(RingEvent).on_conflict_do_nothing()


def device(session: Session, serial: str) -> Device:
    d = session.get(Device, serial)
    if d is None:
        d = Device(serial=serial, cursor=0)
        session.add(d)
        session.flush()
    return d


def timeline(session: Session, serial: str) -> Timeline:
    rows = session.execute(select(RingEvent.ring_ts, RingEvent.decoded).where(
        RingEvent.serial == serial, RingEvent.tag.in_(ANCHOR_TAGS))).all()
    return Timeline([(ds, d["unix_time"]) for ds, d in rows if d and "unix_time" in d])


def commit_batch(session: Session, serial: str, events: list[ev.RingEvent], next_cursor: int) -> int:
    """Insert a batch (duplicates ignored) and advance the cursor in one transaction."""
    if events:
        session.execute(_insert_ignore(session), [{
            "serial": serial, "tag": e.tag, "name": e.name, "ring_ts": e.timestamp,
            "body_hex": e.body.hex(), "decoded": e.decoded,
        } for e in events])
    device(session, serial).cursor = next_cursor
    session.commit()
    return len(events)


def assign_timestamps(session: Session, serial: str, only_missing: bool = True) -> int:
    """Fill `ts` from the anchors stored so far. Returns rows updated."""
    tl = timeline(session, serial)
    if not tl:
        return 0
    q = select(RingEvent.id, RingEvent.ring_ts).where(RingEvent.serial == serial)
    if only_missing:
        q = q.where(RingEvent.ts.is_(None))
    rows = [{"id": i, "ts": tl.to_datetime(ds)} for i, ds in session.execute(q).all()]
    if rows:
        session.execute(update(RingEvent), rows)  # ORM bulk update by primary key
    session.commit()
    return len(rows)


def redecode(session: Session) -> int:
    """Re-run the decoders over stored raw bodies (after adding/fixing a decoder)."""
    n = 0
    for row in session.scalars(select(RingEvent)):
        decoded = ev.decode_body(row.tag, bytes.fromhex(row.body_hex))
        if decoded != row.decoded:
            row.decoded = decoded
            n += 1
    session.commit()
    return n


def mark_synced(session: Session, serial: str, **fields):
    d = device(session, serial)
    for k, v in fields.items():
        setattr(d, k, v)
    d.last_sync_at = datetime.now(timezone.utc)
    session.commit()
