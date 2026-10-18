"""Command-line tool for talking to your ring.

    python -m ring.cli scan                 # find nearby rings
    python -m ring.cli gatt                 # dump the GATT table (exploration)
    python -m ring.cli pair                 # ONLY on a factory-reset ring: install your own key
    python -m ring.cli info                 # firmware, serial, battery
    python -m ring.cli sync [--every 30]    # download history into the database
    python -m ring.cli sniff --seconds 60   # authenticate, then print every raw frame

Settings come from flags or env: OURA_KEY_FILE (default ~/.fipring/ring.key),
OURA_RING_ADDRESS (MAC on Linux/Windows, UUID on macOS), DATABASE_URL.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from ring import protocol as proto
from ring.auth import generate_key
from ring.client import BleTransport, OuraClient
from ring.events import ProtocolError

log = logging.getLogger("ring")
DEFAULT_KEY = Path(os.getenv("OURA_KEY_FILE", Path.home() / ".fipring" / "ring.key"))


def load_key(path: Path) -> bytes:
    if not path.exists():
        sys.exit(f"No key at {path}. Pair a factory-reset ring first (see docs/ring-setup.md).")
    key = bytes.fromhex(path.read_text().strip())
    if len(key) != 16:
        sys.exit(f"{path} must hold 32 hex characters")
    return key


def save_key(path: Path, key: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.hex() + "\n")
    path.chmod(0o600)


async def cmd_scan(args):
    rings = await BleTransport.scan(args.timeout)
    if not rings:
        print("No rings found. Bring it close, take it off the charger, and make sure no phone is connected.")
    for dev, adv in rings:
        print(f"{adv.rssi:>5} dBm  {dev.address}  {dev.name or adv.local_name or '(unnamed)'}")


async def cmd_gatt(args):
    t = await BleTransport.open(args.address, args.timeout)
    try:
        for s in t.client.services:
            print(f"[service] {s.uuid}")
            for c in s.characteristics:
                print(f"   [char] {c.uuid} handle=0x{c.handle:04x} {','.join(c.properties)}")
    finally:
        await t.close()


async def cmd_pair(args):
    if not args.yes:
        sys.exit("pair installs YOUR key on a factory-reset ring. After this the official Oura app can't "
                 "talk to the ring until it's reset again. Re-run with --yes to continue.")
    key = load_key(args.key_file) if args.key_file.exists() else generate_key()
    save_key(args.key_file, key)  # before installing, so the only copy is never lost
    t = await BleTransport.open(args.address, args.timeout, pair=True)
    try:
        c = OuraClient(t)
        print("Serial  :", await c.serial())
        await c.set_auth_key(key)
        await c.authenticate(key)
        print(f"Key installed and verified; saved to {args.key_file}")
        await c.sync_time()
        # The official app switches these on during onboarding; a fresh ring has them off.
        for feature in (proto.Feature.DAYTIME_HR, proto.Feature.SPO2):
            try:
                await c.set_feature_mode(feature, proto.FeatureMode.AUTOMATIC)
            except ProtocolError as e:
                print("warning:", e)
        print("Battery :", await c.battery(), "%")
    finally:
        await t.close()


async def cmd_info(args):
    key = load_key(args.key_file)
    t = await BleTransport.open(args.address, args.timeout)
    try:
        c = OuraClient(t)
        info = await c.firmware()
        print(f"Firmware: {info.firmware}  Bootloader: {info.bootloader}  API: {info.api}")
        print("Serial  :", await c.serial())
        await c.authenticate(key)
        print("Battery :", await c.battery(), "%")
    finally:
        await t.close()


async def sync_once(args) -> int:
    from app.db import SessionLocal, init_db
    from ring import store

    key = load_key(args.key_file)
    init_db()
    t = await BleTransport.open(args.address, args.timeout)
    try:
        c = OuraClient(t)
        await c.authenticate(key)
        serial = await c.serial()
        info = await c.firmware()
        await c.sync_time()            # writes the time anchor that dates this batch
        await c.check_sleep_analysis()  # ask the ring to finish last night's sleep analysis
        battery = await c.battery()
        with SessionLocal() as db:
            cursor = store.device(db, serial).cursor
            log.info("syncing %s from cursor %d", serial, cursor)
            total = 0

            async def on_batch(events, next_cursor, bytes_left):
                nonlocal total
                total += store.commit_batch(db, serial, events, next_cursor)
                log.info("  %d events so far, ~%.1f KB left on ring", total, bytes_left / 1024)

            await c.drain_events(cursor, on_batch)
            store.assign_timestamps(db, serial)
            store.mark_synced(db, serial, firmware=info.firmware, mac=info.mac, battery=battery)
        log.info("done: %d events, battery %d%%", total, battery)
        return total
    finally:
        await t.close()


async def cmd_sync(args):
    while True:
        try:
            await sync_once(args)
        except Exception as e:
            if not args.every:
                raise
            log.warning("sync skipped: %s", e)  # ring out of range, asleep on the charger, etc.
        if not args.every:
            return
        await asyncio.sleep(args.every * 60)


async def cmd_sniff(args):
    key = load_key(args.key_file)
    t = await BleTransport.open(args.address, args.timeout)
    try:
        await OuraClient(t).authenticate(key)
        print(f"authenticated; printing frames for {args.seconds}s")
        end = asyncio.get_running_loop().time() + args.seconds
        while (left := end - asyncio.get_running_loop().time()) > 0:
            try:
                frame = await asyncio.wait_for(t.frames.get(), left)
            except asyncio.TimeoutError:
                break
            for p in proto.Packet.parse_many(frame):
                print(f"tag=0x{p.tag:02x} len={len(p.payload):3d} {p.payload.hex()}")
    finally:
        await t.close()


def cmd_redecode(args):
    from app.db import SessionLocal, init_db
    from ring import store
    init_db()
    with SessionLocal() as db:
        print("re-decoded", store.redecode(db), "events")
        for (serial,) in db.query(store.Device.serial):
            print("timestamps updated:", store.assign_timestamps(db, serial, only_missing=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ring", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", default=os.getenv("OURA_RING_ADDRESS"))
    ap.add_argument("--key-file", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("-v", "--verbose", action="store_true", help="log every frame sent/received")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("gatt")
    sub.add_parser("pair").add_argument("--yes", action="store_true")
    sub.add_parser("info")
    sub.add_parser("sync").add_argument("--every", type=float, default=0, help="repeat every N minutes")
    sub.add_parser("sniff").add_argument("--seconds", type=float, default=60)
    sub.add_parser("redecode")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "redecode":
        return cmd_redecode(args)
    try:
        asyncio.run(globals()[f"cmd_{args.cmd}"](args))
    except ProtocolError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
