"""Known-answer vectors from open_oura's captures and unit tests."""
from ring import protocol as proto
from ring.auth import encrypt_nonce
from ring.client import parse_device_info
from ring.events import RingEvent, decode_batch, decode_body, validate_batch, ProtocolError
from ring.protocol import Packet

import pytest


def pkt(h):
    return Packet.parse(bytes.fromhex(h))


def test_auth_known_answer():
    key = bytes.fromhex("4431967d8bacc2659743142b68391d9a")
    nonce = bytes.fromhex("0e2d6a0a08c99b4365f458e6e97382")
    assert encrypt_nonce(key, nonce).hex() == "a38a8772d3acb6db5c2b516dd56987c8"


def test_request_builders():
    assert proto.req_firmware().hex() == "0803000000"
    assert proto.req_auth_nonce().hex() == "2f012b"
    assert proto.req_get_event(0, 8).hex() == "10090000000008ffffffff"
    assert proto.req_ext_get_event(123_400, 0xFFFF).hex() == "2f0c410008e2010000000000ffff"
    assert proto.req_sync_time_counter(0, 0).hex() == "12090000000000000000f6"
    assert proto.req_data_flush().hex() == "280100"
    assert proto.req_set_feature_mode(0x02, 0x01).hex() == "2f03220201"


def test_parse_many_and_padding():
    frames = Packet.parse_many(bytes.fromhex("0d0639000000360f290100"))
    assert [p.tag for p in frames] == [0x0D, 0x29]
    assert Packet.parse(bytes([0x25, 0x01, 0x00, 0xAA])).payload == b"\x00"


def test_device_info():
    p = pkt("091202000003040301000105000cffeeddccbbaa")
    info = parse_device_info(p.payload)
    assert (info.api, info.firmware, info.mac) == ("2.0.0", "3.4.3", "aa:bb:cc:dd:ee:ff")


def test_decode_legacy_batch():
    events, left = decode_batch([pkt("43086400000074657374"), pkt("1106000000000000")])
    assert left == 0
    assert events[0].timestamp == 100 and events[0].decoded == {"ascii": "test"}


def test_decode_extended_batch():
    events, left = decode_batch([pkt("2f09430600aa430364bbcc"), pkt("2f0a42010000000000000000")])
    assert (len(events), events[0].timestamp, events[0].body, left) == (1, 1, b"\xbb\xcc", 0)


def test_batch_errors():
    with pytest.raises(ProtocolError, match="without a summary"):
        decode_batch([pkt("43086400000074657374")])
    with pytest.raises(ProtocolError, match="0xff"):
        decode_batch([pkt("2f0a420000000000000000ff")])


def test_validate_drops_replays_and_cursor_poison():
    start = 6_906_561
    events = [RingEvent(0x43, ts, b"") for ts in (6_906_500, 6_906_620, 3_970_646_416)]
    kept, cursor = validate_batch(events, start)
    assert [e.timestamp for e in kept] == [6_906_620]
    assert cursor == 6_906_621
    assert validate_batch([], start) == ([], start)


def test_body_decoders():
    assert decode_body(0x5D, bytes([40, 101, 42, 95])) == {"hr_bpm": [40, 42], "rmssd_ms": [101, 95], "interval_min": 5}
    assert decode_body(0x46, (3312).to_bytes(2, "little", signed=True)) == {"temps_c": [33.12]}
    assert decode_body(0x46, (20000).to_bytes(2, "little")) is None  # implausible -> stay raw
    assert decode_body(0x42, (1_787_733_221).to_bytes(4, "little"))["unix_time"] == 1_787_733_221
    assert decode_body(0x50, bytes([1, 10, 0x81]))["met"] == [1.0, 13.0]
    assert decode_body(0x4B, bytes([0, 0b00011011]))["phases"] == ["deep", "light", "rem", "awake"]
    assert decode_body(0x76, (1000).to_bytes(4, "little") + (37000).to_bytes(4, "little"))["duration_hours"] == 1.0
    # green IBI: b0=125, b1=quality 1 (0b01000), low bits 0 -> 1000 ms -> 60 bpm
    assert decode_body(0x80, bytes([125, 0b00001000]))["hr_bpm"] == [60]
