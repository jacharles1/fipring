import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import config, oura, sync
from app.db import BlePacket, HeartRate, OAuthToken, OuraDocument, SessionLocal, SyncState, init_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_sync_lock = threading.Lock()
_oauth_states: set[str] = set()


def run_sync():
    if not _sync_lock.acquire(blocking=False):
        log.info("sync already running")
        return
    try:
        with SessionLocal() as db:
            if db.get(OAuthToken, 1) is None:
                return
            log.info("sync finished: %s", sync.sync_all(db))
    except Exception:
        log.exception("sync failed")
    finally:
        _sync_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_sync, "interval", minutes=config.SYNC_INTERVAL_MINUTES,
                      next_run_time=datetime.now() + timedelta(seconds=10))
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="fipring", lifespan=lifespan)


@app.get("/auth/login")
def login():
    if not config.OURA_CLIENT_ID:
        raise HTTPException(500, "OURA_CLIENT_ID is not set - see .env.example")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
    return RedirectResponse(oura.authorize_url(state))


@app.get("/auth/callback")
def callback(code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, f"Oura returned an error: {error}")
    if state not in _oauth_states:
        raise HTTPException(400, "Unknown OAuth state - start again from /auth/login")
    _oauth_states.discard(state)
    with SessionLocal() as db:
        sync.save_token(db, oura.exchange_code(code))
    threading.Thread(target=run_sync, daemon=True).start()
    return RedirectResponse("/")


@app.post("/api/sync")
def trigger_sync():
    threading.Thread(target=run_sync, daemon=True).start()
    return {"started": True}


@app.get("/api/status")
def status():
    with SessionLocal() as db:
        return {
            "connected": db.get(OAuthToken, 1) is not None,
            "syncing": _sync_lock.locked(),
            "synced_through": {s.collection: s.synced_through for s in db.scalars(select(SyncState))},
            "documents": dict(db.execute(
                select(OuraDocument.collection, func.count()).group_by(OuraDocument.collection)
            ).all()),
            "heartrate_samples": db.scalar(select(func.count()).select_from(HeartRate)),
            "ble_packets": db.scalar(select(func.count()).select_from(BlePacket)),
        }


def _main_sleep(periods: list[dict]) -> dict | None:
    """Oura can log several sleep periods per day (naps); pick the main one."""
    if not periods:
        return None
    long = [p for p in periods if p.get("type") == "long_sleep"] or periods
    return max(long, key=lambda p: p.get("total_sleep_duration") or 0)


def build_daily(docs: list[OuraDocument]) -> list[dict]:
    by_day: dict[date, dict] = {}
    sleeps: dict[date, list[dict]] = {}
    for d in docs:
        row = by_day.setdefault(d.day, {"day": d.day.isoformat()})
        data = d.data
        if d.collection == "daily_readiness":
            row["readiness"] = data.get("score")
            row["temp_deviation"] = data.get("temperature_deviation")
        elif d.collection == "daily_sleep":
            row["sleep_score"] = data.get("score")
        elif d.collection == "daily_activity":
            row["activity_score"] = data.get("score")
            row["steps"] = data.get("steps")
            row["active_calories"] = data.get("active_calories")
        elif d.collection == "daily_stress":
            row["stress_high_min"] = round((data.get("stress_high") or 0) / 60)
        elif d.collection == "sleep":
            sleeps.setdefault(d.day, []).append(data)
    for day, periods in sleeps.items():
        s = _main_sleep(periods)
        hours = lambda k: round((s.get(k) or 0) / 3600, 2)
        by_day[day].update({
            "deep_h": hours("deep_sleep_duration"),
            "rem_h": hours("rem_sleep_duration"),
            "light_h": hours("light_sleep_duration"),
            "awake_h": hours("awake_time"),
            "avg_hrv": s.get("average_hrv"),
            "lowest_hr": s.get("lowest_heart_rate"),
            "bedtime_start": s.get("bedtime_start"),
            "bedtime_end": s.get("bedtime_end"),
        })
    return [by_day[k] for k in sorted(by_day)]


@app.get("/api/daily")
def daily(days: int = Query(90, ge=1, le=3650)):
    since = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        docs = list(db.scalars(select(OuraDocument).where(
            OuraDocument.collection.in_(["daily_readiness", "daily_sleep", "daily_activity", "daily_stress", "sleep"]),
            OuraDocument.day >= since,
        )))
    return build_daily(docs)


@app.get("/api/heartrate")
def heartrate(day: date):
    start = datetime.combine(day, time.min, timezone.utc)
    with SessionLocal() as db:
        rows = db.scalars(select(HeartRate).where(
            HeartRate.timestamp >= start, HeartRate.timestamp < start + timedelta(days=1)
        ).order_by(HeartRate.timestamp))
        return [{"t": r.timestamp.isoformat(), "bpm": r.bpm, "source": r.source} for r in rows]


@app.get("/api/documents/{collection}")
def documents(collection: str, limit: int = Query(20, le=500)):
    """Raw JSON as Oura returned it - handy for exploring fields that aren't charted yet."""
    with SessionLocal() as db:
        rows = db.scalars(select(OuraDocument).where(OuraDocument.collection == collection)
                          .order_by(OuraDocument.day.desc()).limit(limit))
        return [r.data for r in rows]


@app.get("/api/ble")
def ble_packets(session: str | None = None, limit: int = Query(200, le=5000)):
    with SessionLocal() as db:
        q = select(BlePacket).order_by(BlePacket.captured_at.desc()).limit(limit)
        if session:
            q = q.where(BlePacket.session == session)
        return [{
            "captured_at": p.captured_at.isoformat(), "session": p.session, "source": p.source,
            "direction": p.direction, "handle": p.handle, "payload_hex": p.payload_hex, "note": p.note,
        } for p in db.scalars(q)]


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
