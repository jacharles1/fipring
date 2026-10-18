# Reverse-engineering the Oura Ring 4 over Bluetooth

This is the lab notebook for the class project. The goal is to understand what the ring itself
sends over Bluetooth Low Energy (BLE), separately from what Oura's cloud computes from it.

## What to expect

- **The ring stores raw-ish sensor data and syncs it in batches.** It logs PPG (optical heart signal),
  accelerometer, skin temperature and SpO2 samples. When the phone app connects, it downloads the
  history. Scores like readiness and sleep stages are computed later in the app and cloud from that
  data, so you won't find a "readiness score" packet. You'll find events and samples you have to decode.
- **The connection is authenticated, and probably encrypted at the application layer.** Community work
  ([open_oura](https://github.com/Th0rgal/open_oura)) describes an auth handshake with a per-ring key
  that the official app sets up during pairing. Until you have that key, the ring will likely answer
  basic GATT reads but refuse history commands. Expect this to be the hardest step.
- **One central at a time.** While your phone is connected, your laptop can't connect. Turn phone
  Bluetooth off for direct experiments. Don't use the app's "Ring airplane mode", which switches the
  ring's radio off.

## Phase 1: Map the device (`ble/explore.py`)

Record in this file:
- advertised name, manufacturer data, advertised service UUIDs
- every service and characteristic UUID, its handle, and its properties (read/write/notify)
- values of readable characteristics (Device Information service: model, firmware `2.12.3`, etc.)

Standard UUIDs (0x180A Device Information, 0x180F Battery) are documented by the Bluetooth SIG.
A custom 128-bit UUID with a write characteristic and a notify characteristic is almost certainly the
command/response channel.

## Phase 2: Capture the official app (`ble/btsnoop.py`)

This gives you ground truth: real commands and real responses, which you can line up with numbers
the app displays.

**Android (easiest):**
1. Settings → About → tap Build number 7× → Developer options → **Enable Bluetooth HCI snoop log**.
2. Toggle Bluetooth off/on, open the Oura app, pull to sync. Note the time and what the app shows
   (e.g. last-night HR graph).
3. `adb bugreport bug.zip`, unzip, find `FS/data/misc/bluetooth/logs/btsnoop_hci.log`.
4. `python -m ble.btsnoop btsnoop_hci.log --session android-sync-1`
5. Also open it in **Wireshark** (filter `btatt`) to see the full picture, including reassembled
   fragments that the importer skips.

**iPhone:** install Apple's Bluetooth logging profile from developer.apple.com, then capture with
PacketLogger on a Mac. Export from Wireshark as btsnoop before importing.

## Phase 3: Decode

Work from the captured sessions in `ble_packets` (`/api/ble?session=...` or SQL):

1. **Framing.** Group packets by handle and direction. Look for a fixed first byte (command ID), a
   length byte, and counters that increase per packet.
2. **Handshake.** The first exchanges after connecting are usually auth: a random challenge from
   the ring, then a response from the app. Compare two sessions; bytes that change each time are
   nonces.
3. **History download.** Look for long runs of notifications right after a command from the app.
   Try decoding fields as little-endian uint16/uint32 and timestamps (seconds since 2000 or since
   1970), and compare candidate values with the heart-rate samples the cloud API stored for the same
   night (`heartrate` table). Having the cloud's numbers next to the raw bytes is what makes this
   tractable.
4. Write each confirmed field into a table below. Once a record type is understood, add a parser
   that turns `ble_packets` rows into proper tables.

### Findings

| handle / UUID | direction | bytes | meaning | evidence |
|---|---|---|---|---|
| | | | | |

## Ground rules

- It's your own ring and your own data, used for an educational project. Don't publish per-device
  keys, your MAC address or serial number, or Oura's proprietary app code.
- Read-only first. Avoid probing write commands at random. A malformed command could trigger a
  reset or firmware update path. The Soft reset button in the app is the recovery option.
