"""Step 1: find the ring and dump its GATT table (services, characteristics, properties).

    python -m ble.explore            # scan for anything named Oura
    python -m ble.explore --mac A0:38:...   # connect to a specific ring

The ring only talks to one central at a time. Turn Bluetooth OFF on your phone first
(do NOT use "Ring airplane mode" in the app - that switches the ring's radio off).
"""
import argparse
import asyncio
import os

from bleak import BleakClient, BleakScanner


async def find_ring(mac: str | None, timeout: float):
    if mac:
        return await BleakScanner.find_device_by_address(mac, timeout=timeout)
    print(f"Scanning {timeout:.0f}s for Oura devices...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rings = []
    for dev, adv in found.values():
        name = dev.name or adv.local_name or ""
        if "oura" in name.lower():
            print(f"  {dev.address}  rssi={adv.rssi}  name={name}  mfr={ {k: v.hex() for k, v in adv.manufacturer_data.items()} }"
                  f"  services={adv.service_uuids}")
            rings.append(dev)
    return rings[0] if rings else None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default=os.getenv("OURA_RING_MAC"))
    ap.add_argument("--timeout", type=float, default=15)
    args = ap.parse_args()

    dev = await find_ring(args.mac, args.timeout)
    if dev is None:
        print("No ring found. Is phone Bluetooth off and the ring out of the charger/awake?")
        return
    print(f"\nConnecting to {dev.address}...")
    async with BleakClient(dev) as client:
        for service in client.services:
            print(f"\n[service] {service.uuid}  {service.description}")
            for ch in service.characteristics:
                line = f"  [char] {ch.uuid}  handle=0x{ch.handle:04x}  props={','.join(ch.properties)}"
                if "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch)
                        line += f"  value={val.hex()}  ascii={val.decode('ascii', 'replace')!r}"
                    except Exception as e:
                        line += f"  (read failed: {e})"
                print(line)
                for desc in ch.descriptors:
                    print(f"      [desc] {desc.uuid}  handle=0x{desc.handle:04x}")


if __name__ == "__main__":
    asyncio.run(main())
