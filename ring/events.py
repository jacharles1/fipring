"""History events: envelope parsing and per-type body decoders.

A history event is a packet with tag >= 0x41 whose payload is
`u32 LE ring_timestamp (deciseconds since ring boot) | body`. Bodies are always
kept raw; `decode_body` adds a best-effort structured view for the types whose
layout is known (layouts recovered by open_oura from libringeventparser.so).
"""
import struct
from dataclasses import dataclass, field

from ring.protocol import HISTORY_EVENT_PREFIX, Packet

EVENT_NAMES = {
    0x41: "ring_start", 0x42: "time_sync", 0x43: "debug_event", 0x44: "ibi_event",
    0x45: "state_change", 0x46: "temp_event", 0x47: "motion_event",
    0x48: "sleep_period_information", 0x49: "sleep_summary_1", 0x4A: "ppg_amplitude",
    0x4B: "sleep_phase_information", 0x4C: "sleep_summary_2",
    0x4D: "ring_sleep_feature_information", 0x4E: "sleep_phase_details",
    0x4F: "sleep_summary_3", 0x50: "activity_information", 0x51: "activity_summary_1",
    0x52: "activity_summary_2", 0x53: "wear_event", 0x54: "recovery_summary",
    0x55: "sleep_heart_rate", 0x56: "alert_event", 0x57: "ring_sleep_feature_information_2",
    0x58: "sleep_summary_4", 0x59: "eda_event", 0x5A: "sleep_phase_data",
    0x5B: "ble_connection", 0x5C: "user_information", 0x5D: "hrv_event",
    0x5E: "self_test_event", 0x5F: "raw_acm_event", 0x60: "ibi_and_amplitude_event",
    0x61: "debug_data", 0x62: "on_demand_meas", 0x63: "ppg_peak_event",
    0x64: "raw_ppg_event", 0x65: "on_demand_session", 0x66: "on_demand_motion",
    0x67: "raw_ppg_summary", 0x68: "raw_ppg_data", 0x69: "temp_period",
    0x6A: "sleep_period_information_2", 0x6B: "motion_period", 0x6C: "feature_session",
    0x6D: "meas_quality_event", 0x6E: "spo2_ibi_and_amplitude_event", 0x6F: "spo2_event",
    0x70: "spo2_smoothed_event", 0x71: "green_ibi_and_amplitude_event",
    0x72: "sleep_acm_period", 0x73: "ehr_trace_event", 0x74: "ehr_acm_intensity_event",
    0x75: "sleep_temp_event", 0x76: "bedtime_period", 0x77: "spo2_dc_event",
    0x79: "self_test_data_event", 0x7A: "tag_event", 0x7E: "real_step_event_feature_1",
    0x7F: "real_step_event_feature_2", 0x80: "green_ibi_quality_event",
    0x81: "cva_raw_ppg_data", 0x82: "scan_start", 0x83: "scan_end", 0x85: "rtc_beacon",
    0x8B: "spo2_r_pi_event",
}


def event_name(tag: int) -> str:
    return EVENT_NAMES.get(tag, f"unknown_0x{tag:02x}")


@dataclass
class RingEvent:
    tag: int
    timestamp: int  # ring deciseconds
    body: bytes
    name: str = field(init=False)
    decoded: dict | None = field(init=False)

    def __post_init__(self):
        self.name = event_name(self.tag)
        self.decoded = decode_body(self.tag, self.body)

    @staticmethod
    def from_packet(p: Packet) -> "RingEvent":
        ts = int.from_bytes(p.payload[:4], "little") if len(p.payload) >= 4 else 0
        return RingEvent(p.tag, ts, bytes(p.payload[4:]))


# --- body decoders ---------------------------------------------------------

def _u32(b, i=0):
    return int.from_bytes(b[i:i + 4], "little")


def _time_sync(b):
    if len(b) < 4:
        return None
    return {"unix_time": _u32(b)}


def _rtc_beacon(b):
    if len(b) < 10:
        return None
    return {"unix_time": _u32(b)}


def _ring_start(b):
    if len(b) < 14:
        return None
    v = lambda s: ".".join(str(x) for x in s)
    return {"reason": _u32(b), "firmware_version": v(b[5:8]), "bootloader_version": v(b[8:11]),
            "api_version": v(b[11:14])}


def _ascii(b):
    text = b.decode("latin-1").rstrip("\0").strip()
    return {"ascii": text} if text else None


def _state_text(b):
    if not b:
        return None
    return {"state": b[0], "text": b[1:].decode("latin-1").rstrip("\0").strip()}


def _temperatures(b):
    if not b or len(b) % 2:
        return None
    temps = [round(t / 100, 2) for (t,) in struct.iter_unpack("<h", b)]
    if any(not -40 <= t <= 85 for t in temps):
        return None
    return {"temps_c": temps}


def _hrv(b):
    """Pairs of (hr bpm, rmssd ms), one per 5-minute window."""
    if not b or len(b) % 2:
        return None
    return {"hr_bpm": list(b[0::2]), "rmssd_ms": list(b[1::2]), "interval_min": 5}


def _green_ibi_quality(b):
    if len(b) < 2:
        return None
    ibi, quality, hr = [], [], []
    for i in range(0, len(b) - 1, 2):
        v = (b[i + 1] & 0x07) | (b[i] << 3)
        q = (b[i + 1] >> 3) & 0x03
        if q == 1 and 300 <= v <= 2000:
            hr.append(60000 // v)
        ibi.append(v)
        quality.append(q)
    return {"ibi_ms": ibi, "quality": quality, "hr_bpm": hr}


def _ibi_amplitude(b):
    if len(b) != 14:
        return None
    ibi = [
        (b[6] & 1) | (b[0] << 3) | ((b[12] >> 5) & 6),
        (b[7] & 1) | (b[1] << 3) | ((b[12] >> 3) & 6),
        (b[8] & 1) | (b[2] << 3) | ((b[12] >> 1) & 6),
        (b[9] & 1) | (b[3] << 3) | ((b[12] & 3) << 1),
        (b[10] & 1) | (b[4] << 3) | ((b[13] >> 5) & 6),
        (b[11] & 1) | (b[5] << 3) | ((b[13] >> 3) & 6),
    ]
    shift = 0 if (b[13] & 0x0F) == 7 else (b[13] & 0x0F) + 1
    return {"ibi_ms": ibi, "amplitude": [(b[6 + k] >> 1) << shift for k in range(6)],
            "hr_bpm": [60000 // i for i in ibi if 300 <= i <= 2000]}


def _activity_info(b):
    if not b:
        return None
    met = [round(x * 0.1 if x < 0x80 else 12.8 + (x - 128) * 0.2, 2) for x in b[1:]]
    return {"state": b[0], "met": met}


def _spo2(b):
    if len(b) < 2:
        return None
    end = len(b) - 1 if b[-1] == 0xFF else len(b)
    return {"spo2_percent": list(b[1:end])} if end > 1 else None


def _spo2_r_pi(b):
    if len(b) < 4 or (len(b) - 1) % 3:
        return None
    r, pi = [], []
    for o in range(1, len(b) - 2, 3):
        r.append(round(int.from_bytes(b[o:o + 2], "big") / 16384, 3))
        pi.append(round(b[o + 2] / 255 * 0.05, 4))
    return {"r": r, "perfusion_index": pi}


def _sleep_phases(b):
    if len(b) < 2:
        return None
    names = ("deep", "light", "rem", "awake")
    return {"header": b[0], "phases": [names[(x >> s) & 3] for x in b[1:] for s in (6, 4, 2, 0)]}


def _motion(b):
    if len(b) < 4:
        return None
    s8 = lambda x: x - 256 if x > 127 else x
    v = {"orientation": b[0] >> 5, "motion_seconds": b[0] & 0x1F,
         "avg_x": s8(b[1]) * 8, "avg_y": s8(b[2]) * 8, "avg_z": s8(b[3]) * 8}
    for i, k in ((4, "low_intensity"), (5, "high_intensity")):
        if len(b) > i:
            if b[i] & 0x40:
                return None
            v[k] = b[i] & 0x3F
    return v


def _bedtime_period(b):
    if len(b) < 8:
        return None
    start, end = _u32(b), _u32(b, 4)
    return {"bedtime_start_ds": start, "bedtime_end_ds": end,
            "duration_hours": round(max(0, end - start) / 36000, 2)}


def _sleep_period_info(b):
    if len(b) < 10 or b[6] >= 120 or b[7] >= 3:
        return None
    s8 = lambda x: x - 256 if x > 127 else x
    return {"average_hr": b[0] * 0.5, "hr_trend": s8(b[1]) * 0.0625, "breath": b[4] / 8,
            "breath_v": b[5] / 8, "motion_count": b[6], "sleep_state": b[7],
            "cv": round(int.from_bytes(b[8:10], "little") / 65536, 4)}


def _user_information(b):
    if len(b) < 4:
        return None
    return {"age_years": b[0], "weight_kg": b[1], "sex_code": b[2], "height_cm": b[3], "_status": "inferred"}


DECODERS = {
    0x41: _ring_start, 0x42: _time_sync, 0x43: _ascii, 0x45: _state_text, 0x53: _state_text,
    0x46: _temperatures, 0x69: _temperatures, 0x75: _temperatures, 0x47: _motion,
    0x4B: _sleep_phases, 0x4E: _sleep_phases, 0x5A: _sleep_phases, 0x50: _activity_info,
    0x5C: _user_information, 0x5D: _hrv, 0x60: _ibi_amplitude, 0x6A: _sleep_period_info,
    0x6F: _spo2, 0x76: _bedtime_period, 0x80: _green_ibi_quality, 0x85: _rtc_beacon,
    0x8B: _spo2_r_pi,
}


def decode_body(tag: int, body: bytes) -> dict | None:
    fn = DECODERS.get(tag)
    try:
        return fn(body) if fn else None
    except (IndexError, ValueError, struct.error):
        return None


# --- batch framing -----------------------------------------------------------

@dataclass
class BatchSummary:
    bytes_left: int
    result_code: int = 0


def parse_summary(p: Packet) -> BatchSummary | None:
    """Terminator of a GetEvent batch: legacy tag 0x11, or extended 0x2f/0x42."""
    if p.tag == 0x11 and len(p.payload) >= 6:
        return BatchSummary(_u32(p.payload, 2))
    if p.ext_tag == 0x42 and len(p.payload) >= 10:
        return BatchSummary(_u32(p.payload, 4), result_code=p.payload[9])
    return None


def _read_varint(data: bytes) -> tuple[int, int] | None:
    value = 0
    for i, b in enumerate(data[:10]):
        value |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return value, i + 1
    return None


class ExtEnvelopeParser:
    """Reassembles extended-fetch chunks (0x2f/0x43) into ordinary event packets.

    Each envelope is `u16 LE length | bundle`; a bundle holds events encoded as
    `control | tag | len | varint timestamp_ms | body`, where the first timestamp is
    absolute and later ones are deltas."""

    def __init__(self):
        self.len_buf = b""
        self.expected: int | None = None
        self.payload = b""

    def push(self, p: Packet) -> list[Packet]:
        if p.ext_tag != 0x43:
            return []
        out, data = [], p.payload[1:]
        while data:
            if self.expected is None:
                take = 2 - len(self.len_buf)
                self.len_buf += data[:take]
                data = data[take:]
                if len(self.len_buf) < 2:
                    continue
                self.expected = int.from_bytes(self.len_buf, "little") or None
                self.len_buf, self.payload = b"", b""
                continue
            take = self.expected - len(self.payload)
            self.payload += data[:take]
            data = data[take:]
            if len(self.payload) == self.expected:
                out += self._bundle()
                self.expected, self.payload = None, b""
        return out

    def _bundle(self) -> list[Packet]:
        out, i, last_ms, last_ts = [], 0, None, None
        pl = self.payload
        while i + 3 <= len(pl):
            tag, n = pl[i + 1], pl[i + 2]
            start, end = i + 3, i + 3 + n
            if tag < HISTORY_EVENT_PREFIX or end > len(pl):
                break
            vi = _read_varint(pl[start:end])
            if vi is None:
                break
            encoded, used = vi
            ms = last_ms + encoded if last_ms is not None and encoded < 100_000_000 else encoded
            last_ms = ms
            ts = ms // 100
            if last_ts is not None and encoded > 0 and ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            out.append(Packet(tag, ts.to_bytes(4, "little") + pl[start + used:end]))
            i = end
        return out


class ProtocolError(Exception):
    pass


def decode_batch(packets: list[Packet]) -> tuple[list[RingEvent], int]:
    """Events and the ring's remaining-bytes count from one GetEvent response."""
    events, summary, env = [], None, ExtEnvelopeParser()
    for p in packets:
        if s := parse_summary(p):
            summary = s
        elif p.ext_tag == 0x43:
            events += [RingEvent.from_packet(e) for e in env.push(p)]
        elif p.tag >= HISTORY_EVENT_PREFIX:
            events.append(RingEvent.from_packet(p))
    if summary is None:
        raise ProtocolError(f"batch ended without a summary packet ({len(packets)} packets) - link lost?")
    if summary.result_code:
        raise ProtocolError(f"extended history request failed with result 0x{summary.result_code:02x}")
    return events, summary.bytes_left


# A batch can jump over quiet periods but never by months; larger jumps come from
# misaligned envelopes and would poison the stored cursor.
MAX_CURSOR_ADVANCE_DS = 180 * 24 * 3600 * 10


def validate_batch(events: list[RingEvent], start: int) -> tuple[list[RingEvent], int]:
    """Drop replayed (< start) and implausible events; return kept events and next cursor."""
    kept = [e for e in events if start <= e.timestamp <= start + MAX_CURSOR_ADVANCE_DS]
    next_cursor = max(e.timestamp for e in kept) + 1 if kept else start
    return kept, next_cursor
