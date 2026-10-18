"""Step 3: import an HCI snoop log captured on your phone while the *official* Oura app syncs.

This is the main reverse-engineering input: it shows exactly what the app writes to the
ring and what the ring answers, including the auth handshake and the history download.

    python -m ble.btsnoop path/to/btsnoop_hci.log [--session name]

Android: Developer options -> "Enable Bluetooth HCI snoop log", toggle Bluetooth, sync the
Oura app, then `adb bugreport` and pull FS/data/misc/bluetooth/logs/btsnoop_hci.log.
Only ATT writes/notifications/indications are kept; fragmented ACL packets are skipped.
"""
import argparse
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# btsnoop timestamps are microseconds since 0000-01-01; this is the offset to the Unix epoch.
BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000
DATALINK_H4 = 1002
HCI_ACL = 0x02
L2CAP_CID_ATT = 0x0004
ATT_OPCODES = {
    0x12: ("tx", "write_req"),
    0x52: ("tx", "write_cmd"),
    0x1B: ("rx", "notify"),
    0x1D: ("rx", "indicate"),
}


@dataclass
class AttPacket:
    captured_at: datetime
    direction: str
    kind: str
    handle: int
    value: bytes


def parse(data: bytes):
    if data[:8] != b"btsnoop\0":
        raise ValueError("not a btsnoop file (iOS PacketLogger .pklg files must be exported from Wireshark first)")
    _version, datalink = struct.unpack(">II", data[8:16])
    if datalink != DATALINK_H4:
        raise ValueError(f"unsupported datalink {datalink}, expected H4 (1002)")

    off = 16
    while off + 24 <= len(data):
        _orig, incl, flags, _drops, ts = struct.unpack(">IIIIq", data[off:off + 24])
        rec = data[off + 24:off + 24 + incl]
        off += 24 + incl
        if len(rec) < 10 or rec[0] != HCI_ACL:
            continue
        handle_flags, _acl_len = struct.unpack("<HH", rec[1:5])
        if (handle_flags >> 12) & 0b11 == 0b01:  # continuation fragment
            continue
        l2_len, cid = struct.unpack("<HH", rec[5:9])
        if cid != L2CAP_CID_ATT:
            continue
        att = rec[9:9 + l2_len]
        if len(att) < 3 or att[0] not in ATT_OPCODES:
            continue
        direction, kind = ATT_OPCODES[att[0]]
        yield AttPacket(
            captured_at=datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ts - BTSNOOP_EPOCH_DELTA_US),
            direction=direction,
            kind=kind,
            handle=struct.unpack("<H", att[1:3])[0],
            value=bytes(att[3:]),
        )


def main():
    from ble.common import PacketSink

    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--session")
    args = ap.parse_args()
    session = args.session or "btsnoop-" + datetime.now().strftime("%Y%m%d-%H%M%S")

    sink = PacketSink(session, "btsnoop")
    n = 0
    with open(args.path, "rb") as f:
        for p in parse(f.read()):
            sink.write(p.direction, f"0x{p.handle:04x}", p.value, note=p.kind, captured_at=p.captured_at)
            n += 1
    sink.close()
    print(f"imported {n} ATT packets into session {session}")


if __name__ == "__main__":
    main()
