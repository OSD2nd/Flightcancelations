"""Local time for the Kootenays.

BC moved to permanent Pacific time at UTC-7 (no more clock changes). Many
computers and servers still have the old rule and show UTC-8 in winter, so we
never ask the computer for "America/Vancouver": we use a fixed offset from
config.json ("utc_offset", default -7).
"""
from datetime import datetime, timedelta, timezone

OFFSET = -7


def setup(cfg):
    global OFFSET
    OFFSET = int(cfg.get("utc_offset", -7))


def tz():
    return timezone(timedelta(hours=OFFSET))


def now():
    """Current local time, naive, to the minute."""
    return datetime.now(tz()).replace(tzinfo=None, second=0, microsecond=0)


def to_utc(local_naive):
    return local_naive.replace(tzinfo=tz()).astimezone(timezone.utc)


def from_utc(dt_utc):
    """Aware UTC (or epoch seconds) -> naive local."""
    if isinstance(dt_utc, (int, float)):
        dt_utc = datetime.fromtimestamp(dt_utc, timezone.utc)
    return dt_utc.astimezone(tz()).replace(tzinfo=None)
