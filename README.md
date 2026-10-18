# fipring

A personal data stack for an Oura Ring 4: pull everything the ring measures into a database
you own, explore the ring's Bluetooth protocol, and visualize it all on a self-hosted dashboard.

```
            ┌──────────── Oura Cloud API v2 (OAuth) ─────────────┐
 Oura Ring ─┤                                                    ├─► app/ (FastAPI) ─► Postgres ─► dashboard
            └── Bluetooth LE ─► ble/ tools (laptop / Raspberry Pi) ┘                      ▲
                                 explore · listen · btsnoop import ───────────────────────┘
```

Two ways data gets in:

1. **Cloud sync (works today).** The `app` service uses Oura's official API to sync scores, sleep stages,
   HRV, heart rate, SpO2, stress, workouts and more every hour. Every response is stored verbatim as JSON,
   so nothing is lost even if it isn't charted yet.
2. **Direct from the ring (the reverse-engineering part).** The `ble/` tools dump the ring's GATT table,
   record what it sends, and import Bluetooth captures from your phone into the `ble_packets` table so
   you can decode the protocol. See [docs/reverse-engineering.md](docs/reverse-engineering.md).

## Quick start on a homelab

Requires Docker + Docker Compose on the homelab box.

1. **Create an Oura API application** at <https://developer.ouraring.com> (you need an active Oura Membership).
   Set its redirect URI to the address you'll open the dashboard on, e.g.
   `http://192.168.1.50:8000/auth/callback`. If Oura insists on HTTPS, put the app behind your reverse
   proxy (Caddy/Traefik/Nginx Proxy Manager) or use `http://localhost:8000/auth/callback` with an SSH tunnel
   (`ssh -L 8000:localhost:8000 homelab`) for the one-time login.
2. `cp .env.example .env` and fill in `OURA_CLIENT_ID`, `OURA_CLIENT_SECRET`, `OURA_REDIRECT_URI`,
   and a real `POSTGRES_PASSWORD` (use the same password inside `DATABASE_URL`).
3. `docker compose up -d --build`
4. Open `http://<homelab>:8000`, click **Connect Oura**, approve. The first sync backfills
   `BACKFILL_DAYS` (default 365) and then re-syncs every `SYNC_INTERVAL_MINUTES`.

The dashboard has no login of its own. Keep it on your LAN or behind Tailscale or your reverse proxy's auth.
Don't port-forward it to the internet.

## Run locally without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.demo                 # optional: 90 days of fake data so the dashboard has something to show
uvicorn app.main:app --reload      # http://localhost:8000  (uses SQLite in ./data by default)
pytest
```

## Database

| table | what's in it |
|---|---|
| `oura_documents` | every document from every API collection, keyed by (collection, id), raw JSON in `data` |
| `heartrate` | 5-minute heart-rate samples (timestamp, source, bpm) |
| `ble_packets` | raw BLE traffic from the `ble/` tools: direction, characteristic/handle, hex payload |
| `sync_state`, `oauth_token` | bookkeeping |

Postgres lets you query JSON directly, e.g.
`select day, data->>'average_hrv' from oura_documents where collection = 'sleep' order by day;`

## API

| endpoint | |
|---|---|
| `GET /api/status` | connection state, row counts |
| `POST /api/sync` | sync now |
| `GET /api/daily?days=90` | one row per day: scores, sleep stages, HRV, lowest HR, steps |
| `GET /api/heartrate?day=YYYY-MM-DD` | heart-rate samples for a day |
| `GET /api/documents/{collection}` | raw JSON (e.g. `sleep`, `daily_readiness`, `workout`) |
| `GET /api/ble?session=...` | captured BLE packets |

## Bluetooth tools

These run on a machine with Bluetooth near the ring (laptop or Raspberry Pi), not in Docker:

```bash
pip install -r ble/requirements.txt
python -m ble.explore                         # find the ring, dump services/characteristics
python -m ble.listen --mac <ring MAC>         # subscribe to notifications and record them
python -m ble.btsnoop btsnoop_hci.log         # import a phone capture of the official app syncing
```

Set `DATABASE_URL=postgresql+psycopg://oura:<pw>@<homelab>:5432/oura` to send captures straight to the
homelab database. Captures always go to `captures/*.jsonl` too, and that folder is git-ignored.
Keep your ring's MAC and serial number out of git: put them in `.env`.
