# Setting up your ring (no Oura app, no membership)

You only do this once. After it, the ring talks to your collector instead of the Oura app.

## Before you start

- **The reset erases what's on the ring.** If you want your current data, open the Oura app
  and let it sync first.
- **Pick one collector.** After the ring is paired, it refuses any Bluetooth device that
  wasn't bonded during setup. If you want both your laptop and a homelab dongle to work, pair
  both while the ring is freshly reset. Otherwise, choose one now. To switch later, repeat this
  whole page on the new machine.
- **The collector needs Bluetooth LE.** Any recent laptop has it (macOS, Linux, or Windows via
  [bleak](https://github.com/hbldh/bleak)). A Raspberry Pi works too, or a USB Bluetooth dongle on
  the homelab.

## 1. Factory-reset the ring

**Option A, from the Oura app:** Ring details → **Factory reset** (the screen in your
screenshot). Don't set the ring up again afterwards.

**Option B, no app, using the charging dock.** Verified by open_oura on a Ring 4 with firmware
2.12.3:

1. Take the ring off the dock, put it back, and wait ~2 seconds.
2. Flip the dock (with the ring on it) upside down → wait for **blue**.
3. Upright → wait for **red**.
4. Upside down → wait for **magenta**.
5. Upright → wait for **yellow**. The reset has started; a few minutes later the LED **blinks blue**.

## 2. Keep the iPhone out of the way

The ring accepts one connection at a time, and a freshly reset ring accepts a bond from
anything nearby.

- iPhone Settings → Bluetooth → tap ⓘ next to the Oura ring → **Forget This Device**.
- Delete the Oura app, or turn Bluetooth off on the phone while you pair.

## 3. Pair from the collector

```bash
pip install -r requirements.txt
python -m ring.cli scan          # should list the ring (a reset ring may show "(unnamed)")
python -m ring.cli pair --yes
```

`pair` does the following:
1. generates a random 16-byte key and saves it to `~/.fipring/ring.key`, **before** sending it
2. installs the key on the ring (command `0x24`), then proves it works with the challenge/response handshake
3. sets the ring's clock
4. turns on automatic daytime heart rate and SpO2, which a fresh ring has switched off

**Back up `ring.key`.** It's the only way into the ring short of another factory reset.
Never commit it to git.

On macOS, give your terminal app Bluetooth permission (System Settings → Privacy & Security →
Bluetooth). macOS shows the ring's address as a UUID instead of a MAC address. Both work with
`--address`.

## 4. Check it

```bash
python -m ring.cli info          # firmware 2.12.3, serial, battery
python -m ring.cli sync          # first download; small until you've worn it a while
```

Wear the ring. Heart rate and temperature events start within minutes. Sleep events
(`hrv_event`, `bedtime_period`, `sleep_temp_event`) appear after your first night.

## 5. Sync automatically

**Laptop or Pi:**

```bash
export DATABASE_URL=postgresql+psycopg://oura:<password>@<homelab-ip>:5432/oura
python -m ring.cli sync --every 30
```

Run it as a service: a systemd unit on Linux/Pi, launchd on macOS, or just a terminal you
leave open. When the ring is out of range, the sync is skipped and retried next time.

**Homelab with a Bluetooth dongle:** put `ring.key` in the folder `OURA_KEY_DIR` points to,
then run `docker compose --profile collector up -d`. The container uses the host's BlueZ
through D-Bus. If BlueZ in Docker gives you trouble, run the command above directly on the host.

Bluetooth range is roughly one room. The ring stores about a week of data, so a collector
that sees you every night (bedside Pi, or a laptop on your desk) is enough.

## Going back to the Oura app

Factory-reset the ring again (option B above), then set it up in the Oura app as a new ring.
