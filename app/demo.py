"""Fill the database with ~90 days of synthetic data so the dashboard can be tried
before connecting a real ring:  python -m app.demo"""
import math
import random
from datetime import date, datetime, timedelta, timezone

from app.db import HeartRate, OuraDocument, SessionLocal, init_db


def main(days: int = 90):
    init_db()
    rng = random.Random(42)
    today = date.today()
    with SessionLocal() as db:
        for i in range(days):
            day = today - timedelta(days=days - i)
            wave = math.sin(i / 7)
            docs = {
                "daily_readiness": {"score": round(78 + 8 * wave + rng.gauss(0, 4)),
                                    "temperature_deviation": round(rng.gauss(0, 0.2), 2)},
                "daily_sleep": {"score": round(80 + 6 * wave + rng.gauss(0, 5))},
                "daily_activity": {"score": round(75 + rng.gauss(0, 8)), "steps": round(8000 + rng.gauss(0, 2500)),
                                   "active_calories": round(400 + rng.gauss(0, 120))},
                "sleep": {"type": "long_sleep", "total_sleep_duration": 0,
                          "deep_sleep_duration": round(rng.uniform(1.0, 1.8) * 3600),
                          "rem_sleep_duration": round(rng.uniform(1.3, 2.1) * 3600),
                          "light_sleep_duration": round(rng.uniform(3.2, 4.2) * 3600),
                          "awake_time": round(rng.uniform(0.3, 0.9) * 3600),
                          "average_hrv": round(55 + 10 * wave + rng.gauss(0, 5)),
                          "lowest_heart_rate": round(50 - 3 * wave + rng.gauss(0, 2))},
            }
            for collection, data in docs.items():
                doc_id = f"demo-{collection}-{day}"
                db.merge(OuraDocument(collection=collection, id=doc_id, day=day,
                                      data={"id": doc_id, "day": day.isoformat(), **data}))
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            for m in range(0, 24 * 60, 5):
                hour = m / 60
                bpm = 52 + 25 * max(0, math.sin((hour - 7) / 16 * math.pi)) + rng.gauss(0, 4)
                db.merge(HeartRate(timestamp=start + timedelta(minutes=m),
                                   source="rest" if hour < 7 else "awake", bpm=round(bpm)))
        db.commit()
    print(f"inserted {days} days of demo data")


if __name__ == "__main__":
    main()
