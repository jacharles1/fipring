import struct

from ble.btsnoop import BTSNOOP_EPOCH_DELTA_US, parse


def record(payload: bytes, received: bool, ts_us: int) -> bytes:
    return struct.pack(">IIIIq", len(payload), len(payload), int(received), 0, ts_us) + payload


def acl_att(att: bytes, first=True) -> bytes:
    l2 = struct.pack("<HH", len(att), 0x0004) + att
    pb = 0b10 if first else 0b01
    return bytes([0x02]) + struct.pack("<HH", 0x0040 | (pb << 12), len(l2)) + l2


def test_parse_extracts_att_writes_and_notifications():
    ts = BTSNOOP_EPOCH_DELTA_US + 1_700_000_000_000_000
    data = b"btsnoop\0" + struct.pack(">II", 1, 1002)
    data += record(acl_att(bytes([0x12, 0x15, 0x00, 0x2f, 0x01])), False, ts)  # write req
    data += record(acl_att(bytes([0x1B, 0x18, 0x00, 0xde, 0xad])), True, ts)   # notification
    data += record(acl_att(bytes([0x1B, 0x18, 0x00, 0xff]), first=False), True, ts)  # fragment, skipped
    data += record(bytes([0x04, 0x0e, 0x00]), True, ts)  # HCI event, skipped

    pkts = list(parse(data))
    assert [(p.direction, p.kind, p.handle, p.value.hex()) for p in pkts] == [
        ("tx", "write_req", 0x15, "2f01"),
        ("rx", "notify", 0x18, "dead"),
    ]
    assert pkts[0].captured_at.year == 2023
