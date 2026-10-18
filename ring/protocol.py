"""Wire format: GATT UUIDs, `tag | length | payload` framing, request builders.

All multi-byte integers are little-endian. Extended operations use outer tag 0x2f
with the extended op as the first payload byte.
"""
from dataclasses import dataclass

OURA_SERVICE = "98ed0001-a541-11e4-b6a0-0002a5d5c51b"
OURA_WRITE = "98ed0002-a541-11e4-b6a0-0002a5d5c51b"
OURA_NOTIFY = "98ed0003-a541-11e4-b6a0-0002a5d5c51b"

# Tags >= 0x41 are history events rather than command responses.
HISTORY_EVENT_PREFIX = 0x41


class Feature:
    DAYTIME_HR = 0x02
    EXERCISE_HR = 0x03
    SPO2 = 0x04
    RESTING_HR = 0x08


class FeatureMode:
    OFF = 0x00
    AUTOMATIC = 0x01


@dataclass(frozen=True)
class Packet:
    tag: int
    payload: bytes

    def encode(self) -> bytes:
        return bytes([self.tag, len(self.payload)]) + self.payload

    @property
    def ext_tag(self) -> int | None:
        return self.payload[0] if self.tag == 0x2F and self.payload else None

    @staticmethod
    def parse(frame: bytes) -> "Packet | None":
        """Lenient parse: if the declared length overruns, take what's there."""
        if len(frame) < 2:
            return None
        return Packet(frame[0], bytes(frame[2:2 + frame[1]]))

    @staticmethod
    def parse_many(frame: bytes) -> list["Packet"]:
        """Every complete packet in one notification (rings concatenate them)."""
        out, i = [], 0
        while i + 2 <= len(frame):
            end = i + 2 + frame[i + 1]
            if end > len(frame):
                break
            out.append(Packet(frame[i], bytes(frame[i + 2:end])))
            i = end
        if not out and (p := Packet.parse(frame)):
            out.append(p)
        return out


def _pkt(tag: int, payload: bytes = b"") -> bytes:
    return Packet(tag, payload).encode()


# --- device info (no auth needed) ---
def req_firmware() -> bytes:
    return bytes.fromhex("0803000000")


REQ_SERIAL = bytes.fromhex("1803080010")
REQ_HARDWARE = bytes.fromhex("1803180010")


def req_battery() -> bytes:
    return _pkt(0x0C)


# --- auth ---
def req_auth_nonce() -> bytes:
    return bytes([0x2F, 0x01, 0x2B])


def req_authenticate(encrypted: bytes) -> bytes:
    return _pkt(0x2F, b"\x2d" + encrypted)


def req_set_auth_key(key: bytes) -> bytes:
    """Install a key. Only accepted by a factory-reset ring."""
    return _pkt(0x24, key)


# --- clock ---
def req_sync_time(unix_secs: int, timezone_half_hours: int = 0) -> bytes:
    return _pkt(0x12, unix_secs.to_bytes(8, "little") + bytes([timezone_half_hours]))


def req_sync_time_counter(unix_secs: int, token: int) -> bytes:
    """Official-app form seen on Ring 4/5: `12 09 <token> <unix/256 u24> 00000000 f6`."""
    counter = unix_secs // 256
    return bytes([0x12, 0x09, token & 0xFF]) + (counter & 0xFFFFFF).to_bytes(3, "little") + bytes(4) + b"\xf6"


# --- features ---
def req_feature_status(feature: int) -> bytes:
    return bytes([0x2F, 0x02, 0x20, feature])


def req_set_feature_mode(feature: int, mode: int) -> bytes:
    return bytes([0x2F, 0x03, 0x22, feature, mode])


def req_check_sleep_analysis(force: bool) -> bytes:
    return bytes([0x28, 0x01, int(force)])


def req_data_flush() -> bytes:
    return bytes([0x28, 0x01, 0x00])


# --- history ---
def req_get_event(start_ds: int, max_events: int, flags: int = -1) -> bytes:
    return _pkt(0x10, start_ds.to_bytes(4, "little") + bytes([max_events]) + flags.to_bytes(4, "little", signed=True))


def req_ext_get_event(start_ms: int, max_events: int, buffer_id: int = 0) -> bytes:
    return _pkt(0x2F, bytes([0x41, buffer_id]) + start_ms.to_bytes(8, "little") + max_events.to_bytes(2, "little"))
