import os
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import oura, sync
from app.db import Base, HeartRate, OuraDocument, SyncState
from app.main import build_daily


def fake_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    if path == "personal_info":
        return httpx.Response(200, json={"id": "me", "age": 25})
    if path == "heartrate":
        return httpx.Response(200, json={"data": [
            {"bpm": 55, "source": "rest", "timestamp": params["start_datetime"]},
        ], "next_token": None})
    if path == "daily_readiness":
        if "next_token" not in params:
            return httpx.Response(200, json={"data": [
                {"id": "r1", "day": "2026-10-16", "score": 80, "temperature_deviation": 0.1},
            ], "next_token": "page2"})
        return httpx.Response(200, json={"data": [
            {"id": "r2", "day": "2026-10-17", "score": 85, "temperature_deviation": -0.2},
        ], "next_token": None})
    if path == "sleep":
        return httpx.Response(200, json={"data": [
            {"id": "s1", "day": "2026-10-17", "type": "long_sleep", "total_sleep_duration": 27000,
             "deep_sleep_duration": 5400, "rem_sleep_duration": 7200, "light_sleep_duration": 14400,
             "awake_time": 1800, "average_hrv": 62, "lowest_heart_rate": 48},
            {"id": "s2", "day": "2026-10-17", "type": "sleep", "total_sleep_duration": 1200},
        ], "next_token": None})
    if path == "daily_spo2":
        return httpx.Response(401, json={"detail": "missing scope"})
    return httpx.Response(200, json={"data": [], "next_token": None})


@pytest.fixture
def db():
    # Set TEST_DATABASE_URL to run against Postgres instead of in-memory SQLite.
    engine = create_engine(os.getenv("TEST_DATABASE_URL", "sqlite://"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def run(db):
    client = oura.OuraClient("token", transport=httpx.MockTransport(fake_api))
    return sync.sync_all(db, client, today=date(2026, 10, 18))


def test_sync_follows_pages_and_survives_failing_collection(db):
    counts = run(db)
    assert counts["daily_readiness"] == 2
    assert counts["sleep"] == 2
    assert counts["daily_spo2"].startswith("error")
    # 365-day backfill split into 30-day chunks, one fake sample per chunk
    assert counts["heartrate"] == 13
    assert db.scalar(select(func.count()).select_from(HeartRate)) == 13
    assert db.get(SyncState, "daily_readiness").synced_through == date(2026, 10, 18)


def test_resync_is_idempotent(db):
    run(db)
    run(db)
    assert db.scalar(select(func.count()).where(OuraDocument.collection == "daily_readiness")) == 2


def test_build_daily_picks_main_sleep(db):
    run(db)
    rows = build_daily(list(db.scalars(select(OuraDocument).where(OuraDocument.day.is_not(None)))))
    last = rows[-1]
    assert last["day"] == "2026-10-17"
    assert last["readiness"] == 85
    assert last["deep_h"] == 1.5
    assert last["avg_hrv"] == 62
