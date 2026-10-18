"""Step 2: subscribe to every notify/indicate characteristic and record what the ring sends.

    python -m ble.listen --mac A0:38:...
    python -m ble.listen --mac A0:38:... --write <char-uuid> <hex>   # send a probe, log the reply

Packets go to captures/<session>.jsonl and, if DATABASE_URL is set, the ble_packets table.
"""
import argparse
import asyncio
import os
from datetime import datetime

from bleak import BleakClient, BleakScanner

from ble.common import PacketSink


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default=os.getenv("OURA_RING_MAC"), required=not os.getenv("OURA_RING_MAC"))
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--write", nargs=2, action="append", metavar=("CHAR_UUID", "HEX"), default=[])
    ap.add_argument("--session", default=datetime.now().strftime("bleak-%Y%m%d-%H%M%S"))
    args = ap.parse_args()

    sink = PacketSink(args.session, "bleak")
    dev = await BleakScanner.find_device_by_address(args.mac, timeout=20)
    if dev is None:
        raise SystemExit("Ring not found - turn phone Bluetooth off and try again")

    async with BleakClient(dev) as client:
        def on_notify(char, data: bytearray):
            print(f"rx {char.uuid}  {data.hex()}")
            sink.write("rx", char.uuid, bytes(data))

        for service in client.services:
            for ch in service.characteristics:
                if {"notify", "indicate"} & set(ch.properties):
                    try:
                        await client.start_notify(ch, on_notify)
                        print(f"subscribed {ch.uuid}")
                    except Exception as e:
                        print(f"could not subscribe {ch.uuid}: {e}")

        for uuid, hexstr in args.write:
            payload = bytes.fromhex(hexstr)
            print(f"tx {uuid}  {payload.hex()}")
            sink.write("tx", uuid, payload, note="probe")
            await client.write_gatt_char(uuid, payload, response=True)
            await asyncio.sleep(1)

        print(f"listening {args.seconds:.0f}s (session {args.session})...")
        await asyncio.sleep(args.seconds)
    sink.close()


if __name__ == "__main__":
    asyncio.run(main())
