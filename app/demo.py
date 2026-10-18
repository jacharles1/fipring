"""Fill the database with synthetic ring events (encoded exactly as the ring sends
them) so the dashboard can be tried before pairing a ring:  python -m app.demo"""
import math
import random
import time
from datetime import datetime, timedelta

from app.db import SessionLocal, init_db
from ring import store
from ring.events import RingEvent

SERIAL = "DEMO0000"


def main(days: int = 60):
    init_db()
    rng = random.Random(7)
    now = int(time.time())
    base = now - days * 86400
    ds = lambda unix: (unix - base) * 10 + 5_000  # ring counter started a little before `base`
    events = []

    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days):
        day = today - timedelta(days=days - i)
        wave = math.sin(i / 6)
        noon = int((day + timedelta(hours=12)).timestamp())
        events.append(RingEvent(0x42, ds(noon), noon.to_bytes(4, "little")))  # daily time_sync anchor

        bed = int((day + timedelta(hours=23, minutes=rng.randint(-40, 50))).timestamp())
        wake = bed + int(rng.uniform(6.2, 8.4) * 3600)
        events.append(RingEvent(0x76, ds(wake), ds(bed).to_bytes(4, "little") + ds(wake).to_bytes(4, "little")))
        rmssd0, hr0 = 55 + 10 * wave, 50 - 3 * wave
        for t in range(bed, wake, 3600):  # one hrv_event per hour holding 12 five-minute pairs
            body = bytearray()
            for k in range(12):
                frac = (t - bed + k * 300) / (wake - bed)
                body += bytes([round(hr0 + 6 * (frac - 0.4) ** 2 * 4 + rng.gauss(0, 1.5)),
                               max(5, round(rmssd0 + rng.gauss(0, 8)))])
            events.append(RingEvent(0x5D, ds(t), bytes(body)))
            temp = round((35.2 + 0.25 * wave + rng.gauss(0, 0.1)) * 100)
            events.append(RingEvent(0x75, ds(t), temp.to_bytes(2, "little", signed=True)))
        for t in range(wake + 1800, bed + 86400 - 3600, 1800):  # daytime HR and activity
            ibi = round(60000 / (68 + 12 * math.sin((t - wake) / 20000) + rng.gauss(0, 4)))
            events.append(RingEvent(0x80, ds(t), bytes([ibi >> 3, (ibi & 7) | 0b00001000]) * 4))
            mets = [min(127, max(9, round(rng.gauss(16, 6)))) for _ in range(30)]
            events.append(RingEvent(0x50, ds(t), bytes([1] + mets)))
        events.append(RingEvent(0x43, ds(noon + 60), b"demo debug line"))
        events.append(RingEvent(0x61, ds(noon + 120), bytes(rng.randrange(256) for _ in range(12))))

    events = [e for e in events if e.timestamp <= ds(now)]
    with SessionLocal() as db:
        store.commit_batch(db, SERIAL, events, max(e.timestamp for e in events) + 1)
        store.assign_timestamps(db, SERIAL)
        store.mark_synced(db, SERIAL, firmware="2.12.3", battery=77)
    print(f"inserted {len(events)} synthetic ring events over {days} days")


if __name__ == "__main__":
    main()
