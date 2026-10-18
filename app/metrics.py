"""Turn raw ring events into the numbers the dashboard shows.

Nights are labelled by the morning they end on: an event belongs to the "sleep day"
of its local time + 6 h, so 23:30 on the 16th and 06:00 on the 17th both count
toward the 17th.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

from app.db import RingEvent

NIGHT_SHIFT = timedelta(hours=6)
HRV_INTERVAL = timedelta(minutes=5)


def utc(ts: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything stored is UTC."""
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def local(ts: datetime) -> datetime:
    return utc(ts).astimezone()  # container/host timezone (set TZ in .env)


def sleep_day(ts: datetime) -> date:
    return (local(ts) + NIGHT_SHIFT).date()


def is_night(ts: datetime) -> bool:
    h = local(ts).hour
    return h >= 21 or h < 9


def daily(events: list[RingEvent]) -> list[dict]:
    nightly = defaultdict(lambda: {"rmssd": [], "hr": [], "temp": [], "sleep_h": []})
    days = defaultdict(lambda: {"met": []})
    for e in events:
        d = e.decoded or {}
        if e.ts is None:
            continue
        if e.name == "hrv_event":
            n = nightly[sleep_day(e.ts)]
            n["rmssd"] += [v for v in d.get("rmssd_ms", []) if v]
            n["hr"] += [v for v in d.get("hr_bpm", []) if v]
        elif e.name in ("sleep_temp_event", "temp_period", "temp_event") and is_night(e.ts):
            nightly[sleep_day(e.ts)]["temp"] += d.get("temps_c", [])
        elif e.name == "bedtime_period" and d.get("duration_hours"):
            nightly[sleep_day(e.ts)]["sleep_h"].append(d["duration_hours"])
        elif e.name == "activity_information":
            days[local(e.ts).date()]["met"] += d.get("met", [])

    out = []
    for day in sorted(set(nightly) | set(days)):
        n, a = nightly.get(day), days.get(day)
        row = {"day": day.isoformat()}
        if n:
            row["avg_rmssd"] = round(mean(n["rmssd"])) if n["rmssd"] else None
            row["lowest_hr"] = min(n["hr"]) if n["hr"] else None
            row["avg_night_hr"] = round(mean(n["hr"])) if n["hr"] else None
            row["skin_temp_c"] = round(median(n["temp"]), 2) if n["temp"] else None
            row["sleep_h"] = round(max(n["sleep_h"]), 2) if n["sleep_h"] else None
        if a and a["met"]:
            row["avg_met"] = round(mean(a["met"]), 2)
        out.append(row)
    return out


def heart_rate(events: list[RingEvent]) -> list[dict]:
    """Heart-rate points: 5-minute overnight values from hrv_event (assumed to start at the
    event time) and the median beat-to-beat rate of each daytime green_ibi_quality_event."""
    pts = []
    for e in events:
        d = e.decoded or {}
        if e.ts is None:
            continue
        if e.name == "hrv_event":
            pts += [{"t": (utc(e.ts) + i * HRV_INTERVAL).isoformat(), "bpm": hr, "source": "hrv_event"}
                    for i, hr in enumerate(d.get("hr_bpm", [])) if hr]
        elif e.name == "green_ibi_quality_event" and d.get("hr_bpm"):
            pts.append({"t": utc(e.ts).isoformat(), "bpm": round(median(d["hr_bpm"])), "source": "green_ibi"})
    return sorted(pts, key=lambda p: p["t"])
