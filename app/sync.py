"""Pull data from the Oura API into the local database."""
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app import config, oura
from app.db import HeartRate, OAuthToken, OuraDocument, SyncState

log = logging.getLogger(__name__)

# Oura keeps refining the last few days (late syncs, recomputed scores), so every
# sync re-fetches this many days before the last synced day.
OVERLAP_DAYS = 3
# The heartrate endpoint rejects ranges longer than 30 days.
HEARTRATE_CHUNK_DAYS = 30


def save_token(db: Session, tok: dict) -> OAuthToken:
    row = db.merge(OAuthToken(
        id=1,
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        expires_at=tok["expires_at"],
    ))
    db.commit()
    return row


def access_token(db: Session) -> str | None:
    row = db.get(OAuthToken, 1)
    if row is None:
        return None
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        row = save_token(db, oura.refresh(row.refresh_token))
    return row.access_token


def _start_day(db: Session, collection: str, today: date) -> date:
    state = db.get(SyncState, collection)
    if state is None:
        return today - timedelta(days=config.BACKFILL_DAYS)
    return state.synced_through - timedelta(days=OVERLAP_DAYS)


def _mark_synced(db: Session, collection: str, today: date):
    db.merge(SyncState(collection=collection, synced_through=today))


def sync_collection(db: Session, client: oura.OuraClient, collection: str, today: date) -> int:
    start = _start_day(db, collection, today)
    n = 0
    for doc in client.paginate(collection, start_date=start.isoformat(),
                               end_date=(today + timedelta(days=1)).isoformat()):
        day = doc.get("day")
        db.merge(OuraDocument(
            collection=collection,
            id=doc["id"],
            day=date.fromisoformat(day) if day else None,
            data=doc,
        ))
        n += 1
    _mark_synced(db, collection, today)
    db.commit()
    return n


def sync_heartrate(db: Session, client: oura.OuraClient, today: date) -> int:
    start = _start_day(db, "heartrate", today)
    n = 0
    while start <= today:
        end = min(start + timedelta(days=HEARTRATE_CHUNK_DAYS), today + timedelta(days=1))
        for s in client.paginate(
            "heartrate",
            start_datetime=datetime.combine(start, time.min, timezone.utc).isoformat(),
            end_datetime=datetime.combine(end, time.min, timezone.utc).isoformat(),
        ):
            db.merge(HeartRate(
                timestamp=datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")),
                source=s["source"],
                bpm=s["bpm"],
            ))
            n += 1
        db.commit()
        start = end
    _mark_synced(db, "heartrate", today)
    db.commit()
    return n


def sync_all(db: Session, client: oura.OuraClient | None = None, today: date | None = None) -> dict:
    if client is None:
        token = access_token(db)
        if token is None:
            raise RuntimeError("Not connected to Oura yet - open /auth/login first")
        client = oura.OuraClient(token)
    today = today or date.today()

    counts = {}
    info = client.personal_info()
    db.merge(OuraDocument(collection="personal_info", id=info.get("id", "me"), day=None, data=info))
    db.commit()

    for collection in oura.DATE_COLLECTIONS:
        try:
            counts[collection] = sync_collection(db, client, collection, today)
        except Exception as e:  # one missing scope/feature shouldn't stop the rest
            db.rollback()
            log.warning("sync %s failed: %s", collection, e)
            counts[collection] = f"error: {e}"
    try:
        counts["heartrate"] = sync_heartrate(db, client, today)
    except Exception as e:
        db.rollback()
        log.warning("sync heartrate failed: %s", e)
        counts["heartrate"] = f"error: {e}"
    return counts
