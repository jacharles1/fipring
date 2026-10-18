"""Async client: request/response over the ring's write + notify characteristics."""
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from ring import protocol as proto
from ring.auth import AuthResult, encrypt_nonce
from ring.events import ProtocolError, RingEvent, decode_batch, validate_batch
from ring.protocol import Packet

log = logging.getLogger(__name__)

QUIET = 1.5          # seconds to wait for more frames after a request
DRAIN_QUIET = 6.0    # history batches can pause longer between frames
EXT_BATCH_MAX_EVENTS = 4096
TAIL_BATCH_EVENTS = 32
MAX_TAIL_PASSES = 3


class Transport(Protocol):
    async def write(self, data: bytes) -> None: ...
    frames: asyncio.Queue


@dataclass
class DeviceInfo:
    firmware: str
    bootloader: str
    api: str
    mac: str


def parse_device_info(b: bytes) -> DeviceInfo:
    """Response 0x09: api, firmware, bootloader, bt-stack (3 bytes each), MAC reversed."""
    v = lambda s: ".".join(str(x) for x in s)
    return DeviceInfo(firmware=v(b[3:6]), bootloader=v(b[6:9]), api=v(b[0:3]),
                      mac=":".join(f"{x:02x}" for x in reversed(b[12:18])))


class OuraClient:
    def __init__(self, transport: Transport, quiet: float = QUIET):
        self.t = transport
        self.quiet = quiet

    async def request(self, data: bytes, terminal: Callable[[Packet], bool] = lambda p: False,
                      quiet: float | None = None) -> list[Packet]:
        """Send one request and collect response packets until `terminal` matches
        or the link goes quiet."""
        q = self.t.frames
        while not q.empty():
            q.get_nowait()
        log.debug("-> %s", data.hex())
        await self.t.write(data)
        packets: list[Packet] = []
        while True:
            try:
                frame = await asyncio.wait_for(q.get(), quiet or self.quiet)
            except asyncio.TimeoutError:
                return packets
            log.debug("<- %s", frame.hex())
            got = Packet.parse_many(frame)
            packets += got
            if any(terminal(p) for p in got):
                return packets

    async def _tag(self, data: bytes, tag: int) -> Packet | None:
        return next((p for p in await self.request(data, lambda p: p.tag == tag) if p.tag == tag), None)

    async def _ext(self, data: bytes, ext: int) -> Packet | None:
        return next((p for p in await self.request(data, lambda p: p.ext_tag == ext) if p.ext_tag == ext), None)

    # --- device info ---
    async def firmware(self) -> DeviceInfo:
        p = await self._tag(proto.req_firmware(), 0x09)
        if p is None or len(p.payload) < 18:
            raise ProtocolError("no firmware response")
        return parse_device_info(p.payload)

    async def serial(self) -> str:
        p = await self._tag(proto.REQ_SERIAL, 0x19)
        if p is None or p.payload[:1] != b"\x00":
            raise ProtocolError("no serial response")
        return p.payload[1:].decode("latin-1").rstrip("\0").strip()

    async def battery(self) -> int:
        p = await self._tag(proto.req_battery(), 0x0D)
        if p is None or not p.payload:
            raise ProtocolError("no battery response (not authenticated?)")
        return p.payload[0]

    # --- auth ---
    async def authenticate(self, key: bytes) -> None:
        p = await self._ext(proto.req_auth_nonce(), 0x2C)
        if p is None or len(p.payload) < 2:
            raise ProtocolError("ring sent no auth nonce")
        p = await self._ext(proto.req_authenticate(encrypt_nonce(key, p.payload[1:])), 0x2E)
        if p is None or len(p.payload) < 2:
            raise ProtocolError("ring sent no auth verdict")
        result = p.payload[1]
        if result != AuthResult.SUCCESS:
            hints = {
                AuthResult.AUTHENTICATION_ERROR: "wrong key for this ring",
                AuthResult.IN_FACTORY_RESET: "ring is factory-reset - run `pair` first",
                AuthResult.NOT_ORIGINAL_ONBOARDED_DEVICE: "ring belongs to another pairing",
            }
            raise ProtocolError(f"auth rejected (0x{result:02x}): {hints.get(result, 'unknown')}")

    async def set_auth_key(self, key: bytes) -> None:
        p = await self._tag(proto.req_set_auth_key(key), 0x25)
        if p is None:
            raise ProtocolError("no response to set_auth_key")
        if p.payload[:1] != b"\x00":
            raise ProtocolError(f"set_auth_key refused (status {p.payload[:1].hex()}) - is the ring factory-reset?")

    # --- setup ---
    async def sync_time(self) -> None:
        """Write host UTC to the ring. The ring records a time_sync event, which is
        the anchor used to put wall-clock times on history events."""
        now = int(time.time())
        await self.request(proto.req_sync_time_counter(now, (now * 37 + 0xA5) & 0xFF))
        await self.request(proto.req_sync_time(now))

    async def set_feature_mode(self, feature: int, mode: int) -> None:
        p = await self._ext(proto.req_set_feature_mode(feature, mode), 0x23)
        if p is None or len(p.payload) < 3 or p.payload[2] != 0:
            raise ProtocolError(f"set_feature_mode(0x{feature:02x}) failed: {p}")

    async def check_sleep_analysis(self) -> None:
        await self._tag(proto.req_check_sleep_analysis(False), 0x29)

    # --- history ---
    async def drain_events(self, cursor: int,
                           on_batch: Callable[[list[RingEvent], int, int], Awaitable[None]]) -> int:
        """Download history from `cursor` (ring deciseconds). `on_batch(events, next_cursor,
        bytes_left)` must persist events and cursor together. Returns the final cursor."""
        start, use_ext, tail_passes = cursor, True, 0
        done = lambda p: p.tag == 0x11 or (p.tag == 0x2F and p.payload[:1] in (b"\x42", b"\x00"))
        for _ in range(100_000):
            packets = await self.request(proto.req_data_flush(), lambda p: p.tag == 0x29)
            if use_ext:
                ext = await self.request(proto.req_ext_get_event(start * 100, EXT_BATCH_MAX_EVENTS), done, DRAIN_QUIET)
                if any(p.tag == 0x2F and p.payload[:1] == b"\x00" for p in ext):
                    log.info("ring does not support extended fetch; using legacy GetEvent")
                    use_ext = False
                    ext = await self.request(proto.req_get_event(start, 255), done, DRAIN_QUIET)
                packets += ext
            else:
                packets += await self.request(proto.req_get_event(start, 255), done, DRAIN_QUIET)

            events, bytes_left = decode_batch(packets)
            events, next_cursor = validate_batch(events, start)
            progressed = bool(events) and next_cursor > start
            await on_batch(events, next_cursor if progressed else start, bytes_left)
            if not progressed:
                if bytes_left == 0:
                    break
                raise ProtocolError(f"ring reports {bytes_left} bytes left but sent no events (cursor {start})")
            start = next_cursor
            # Some firmware keeps serving a tiny tail after reporting 0 bytes left.
            if bytes_left == 0 and len(events) < TAIL_BATCH_EVENTS:
                tail_passes += 1
                if tail_passes >= MAX_TAIL_PASSES:
                    break
            else:
                tail_passes = 0
        return start


class BleTransport:
    """bleak-backed transport. Use as `async with BleTransport.connect(...) as t:`."""

    def __init__(self, client):
        self.client = client
        self.frames: asyncio.Queue = asyncio.Queue()

    async def write(self, data: bytes) -> None:
        await self.client.write_gatt_char(proto.OURA_WRITE, data, response=True)

    @staticmethod
    async def scan(timeout: float = 20.0, all_devices: bool = False):
        from bleak import BleakScanner
        kwargs = {} if all_devices else {"service_uuids": [proto.OURA_SERVICE]}
        found = await BleakScanner.discover(timeout=timeout, return_adv=True, **kwargs)
        return sorted(found.values(), key=lambda da: -da[1].rssi)

    @classmethod
    async def open(cls, address: str | None, timeout: float = 30.0, pair: bool = False) -> "BleTransport":
        from bleak import BleakClient, BleakScanner
        if address:
            dev = await BleakScanner.find_device_by_address(address, timeout=timeout)
        else:
            rings = await cls.scan(timeout)
            dev = rings[0][0] if rings else None
        if dev is None:
            raise ProtocolError("no ring found - is it nearby, charged, and not connected to a phone?")
        client = BleakClient(dev, timeout=timeout)
        await client.connect()
        # macOS bonds automatically when the ring asks; BlueZ/Windows need an explicit pair.
        if pair and sys.platform != "darwin":
            try:
                await client.pair()
            except Exception as e:  # already bonded, or the stack doesn't support it
                log.info("pair(): %s", e)
        self = cls(client)
        for service in client.services:
            if service.uuid.lower() != proto.OURA_SERVICE:
                continue
            for ch in service.characteristics:
                if {"notify", "indicate"} & set(ch.properties):
                    await client.start_notify(ch, lambda _c, data: self.frames.put_nowait(bytes(data)))
        return self

    async def close(self):
        await self.client.disconnect()
