from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import metrics
from app.metrics import utc
from app.db import Device, RingEvent, SessionLocal, init_db

DAILY_EVENTS = ["hrv_event", "sleep_temp_event", "temp_period", "temp_event", "bedtime_period", "activity_information"]
HR_EVENTS = ["hrv_event", "green_ibi_quality_event"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="fipring", lifespan=lifespan)


def _events(names: list[str], start: datetime, end: datetime) -> list[RingEvent]:
    with SessionLocal() as db:
        return list(db.scalars(select(RingEvent).where(
            RingEvent.name.in_(names),
            RingEvent.ts >= start.astimezone(timezone.utc), RingEvent.ts < end.astimezone(timezone.utc),
        ).order_by(RingEvent.ts)))


@app.get("/api/status")
def status():
    with SessionLocal() as db:
        return {
            "devices": [{
                "serial": d.serial, "firmware": d.firmware, "battery": d.battery,
                "last_sync_at": d.last_sync_at and utc(d.last_sync_at), "cursor": d.cursor,
            } for d in db.scalars(select(Device))],
            "events": db.scalar(select(func.count()).select_from(RingEvent)),
            "undated_events": db.scalar(select(func.count()).select_from(RingEvent).where(RingEvent.ts.is_(None))),
        }


@app.get("/api/daily")
def daily(days: int = Query(90, ge=1, le=3650)):
    end = datetime.now(timezone.utc) + timedelta(days=1)
    return metrics.daily(_events(DAILY_EVENTS, end - timedelta(days=days + 1), end))


@app.get("/api/heartrate")
def heartrate(day: date):
    start = datetime.combine(day, time.min).astimezone()
    return metrics.heart_rate(_events(HR_EVENTS, start, start + timedelta(days=1)))


@app.get("/api/event-types")
def event_types():
    """What the ring has sent so far, per event type - the reverse-engineering scoreboard."""
    with SessionLocal() as db:
        rows = db.execute(select(
            RingEvent.tag, RingEvent.name, func.count(), func.count(RingEvent.decoded), func.max(RingEvent.ts),
        ).group_by(RingEvent.tag, RingEvent.name).order_by(func.count().desc())).all()
    return [{"tag": f"0x{tag:02x}", "name": name, "count": n, "decoded": dec,
             "last_seen": utc(datetime.fromisoformat(str(last))) if last else None}
            for tag, name, n, dec, last in rows]


@app.get("/api/events")
def events(name: str | None = None, limit: int = Query(50, le=1000)):
    """Raw events, newest first, for poking at undecoded bodies."""
    q = select(RingEvent).order_by(RingEvent.ring_ts.desc()).limit(limit)
    if name:
        q = q.where(RingEvent.name == name)
    with SessionLocal() as db:
        return [{"tag": f"0x{e.tag:02x}", "name": e.name, "ring_ts": e.ring_ts, "ts": e.ts and utc(e.ts),
                 "body_hex": e.body_hex, "decoded": e.decoded} for e in db.scalars(q)]


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
