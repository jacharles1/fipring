# fipring

Read an Oura Ring 4 directly over Bluetooth, with no Oura app, account or membership.
Store everything it records in your own database and view it on a dashboard you host.

```
 Oura Ring ──BLE──► collector (laptop / Raspberry Pi / homelab + USB dongle)
  records 24/7,        python -m ring.cli sync --every 30
  buffers ~7 days      │  auth handshake → set clock → download history events
                       ▼
                    Postgres (homelab, Docker) ──► dashboard (homelab, Docker) :8000
```

- **`ring/`** is the Bluetooth client: packet framing, the AES auth handshake, history download,
  and decoders for each event type. It's a Python port of the protocol reverse-engineered by
  [open_oura](https://github.com/Th0rgal/open_oura) (MIT, see `THIRD_PARTY_NOTICES`).
- **`app/`** is the dashboard and API. It shows sleep duration, overnight HRV (RMSSD), lowest
  heart rate, skin temperature, activity (MET), heart rate across a day, and a table of every
  event type the ring has sent, including the ones nobody has decoded yet.

## How it works

The ring measures and stores data on its own, whether or not anything is connected.
Oura says a Ring 4 holds about a week of data. When a collector comes within Bluetooth
range, it connects, authenticates, sets the ring's clock, and downloads everything recorded
since the last sync. You don't need the laptop with you during the day. Leave the collector
somewhere the ring is every day, like your bedside or desk, and it catches up.

Your ring is protected by a 16-byte key. The Oura app creates that key when it pairs, and an
iPhone gives you no way to extract it. So you **factory-reset the ring and install your own
key**. After that the ring talks only to this project, not to the Oura app, until you reset it
again. See [docs/ring-setup.md](docs/ring-setup.md).

## Quick start

**Homelab (database + dashboard):**

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and TZ
docker compose up -d --build  # dashboard on http://<homelab>:8000
```

**Collector (laptop, Raspberry Pi, or the homelab itself):**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m ring.cli scan                       # is the ring visible?
python -m ring.cli pair --yes                 # once, on a factory-reset ring
export DATABASE_URL=postgresql+psycopg://oura:<password>@<homelab>:5432/oura
python -m ring.cli sync --every 30            # sync every 30 min while in range
```

If the homelab has Bluetooth (a $10–15 USB dongle works), run the collector there instead
with `docker compose --profile collector up -d`. Copy `~/.fipring/ring.key` from the machine
you paired on.

**Just want to see the dashboard?** Run `python -m app.demo && uvicorn app.main:app` to load
60 days of synthetic events, encoded byte-for-byte the way the ring sends them.

## Commands

| | |
|---|---|
| `ring.cli scan` | list nearby rings |
| `ring.cli gatt` | dump the ring's Bluetooth services and characteristics |
| `ring.cli pair --yes` | install your own key on a factory-reset ring, set clock, turn on HR + SpO2 |
| `ring.cli info` | firmware, serial, battery |
| `ring.cli sync [--every N]` | download history into the database |
| `ring.cli sniff --seconds 60` | authenticate, then print every raw frame (for reverse-engineering) |
| `ring.cli redecode` | re-run decoders over stored raw events after you improve one |

Add `-v` to log every frame sent and received.

## Database

`ring_events` has one row per history event: `tag`, `name`, `ring_ts` (the ring's own
decisecond counter), `ts` (wall-clock time, derived from `time_sync` anchors), `body_hex` (raw
bytes, never thrown away), and `decoded` (JSON, when the layout is known). `devices` holds the
sync cursor, firmware and battery.

```sql
select name, count(*), count(decoded) from ring_events group by name order by 2 desc;
```

## API

`/api/status`, `/api/daily?days=30`, `/api/heartrate?day=YYYY-MM-DD`, `/api/event-types`,
`/api/events?name=debug_data&limit=50`

## Tests

```bash
pip install -r requirements-dev.txt && pytest
```

The tests cover the protocol (open_oura's known-answer vectors), the decoders, and a full sync
against a simulated ring: auth, batched download, duplicate handling, and timestamping.
