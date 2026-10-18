"""Ring time -> wall-clock time.

Events are stamped with the ring's own counter (deciseconds since an arbitrary
start). Whenever a client sets the ring clock, the ring logs a `time_sync` event
(and periodically an `rtc_beacon`) carrying the Unix time at that counter value.
Those (ring_ts, unix) pairs are anchors; an event's time is extrapolated from
the nearest one at 10 ticks per second.
"""
import bisect
from datetime import datetime, timezone

ANCHOR_TAGS = (0x42, 0x85)
MIN_PLAUSIBLE_UNIX = 1_577_836_800  # 2020-01-01; ignores anchors from an unset clock


class Timeline:
    def __init__(self, anchors: list[tuple[int, int]]):
        pts = sorted((ds, unix) for ds, unix in anchors if unix >= MIN_PLAUSIBLE_UNIX)
        self.ds = [p[0] for p in pts]
        self.unix = [p[1] for p in pts]

    def __bool__(self):
        return bool(self.ds)

    def to_datetime(self, ring_ts: int) -> datetime | None:
        if not self.ds:
            return None
        i = bisect.bisect_left(self.ds, ring_ts)
        best = min((j for j in (i - 1, i) if 0 <= j < len(self.ds)), key=lambda j: abs(self.ds[j] - ring_ts))
        return datetime.fromtimestamp(self.unix[best] + (ring_ts - self.ds[best]) / 10, timezone.utc)
