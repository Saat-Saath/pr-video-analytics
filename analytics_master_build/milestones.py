"""Milestone rules, counted in hours from the exact upload time:

  Day 2  = the counts once 48 hours have passed since upload
  Day 7  = the counts once 7 days (168 hours) have passed
  Day 30 = the counts once 30 days (720 hours) have passed
  All time = the counts today (re-checked every week)

The refresh runs several times a day and fetches a video at the first run after it crosses 48 h / 7 d / 30 d, so a
capture is normally a few hours after the mark. A capture more than a day after the mark is kept but flagged late,
with the day it was taken. A mark that had already passed when the video was first tracked is "Added late".
"""
from datetime import date, datetime, timedelta, timezone

H = 3600
MILESTONES = [  # (label, start hour, end hour exclusive, hours after the start before a capture counts as late)
    ("d2", 48, 168, 24),
    ("d7", 168, 720, 24),
    ("d30", 720, 10_000_000, 72),
]
LABEL = {"d2": "Day 2", "d7": "Day 7", "d30": "Day 30"}
IST = timezone(timedelta(hours=5, minutes=30))


def to_dt(v):
    """An aware datetime from an ISO string ('2026-10-15T13:30:17.000Z'); a bare date counts as midnight India time."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=IST)
    s = str(v).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else (d.replace(tzinfo=IST) if len(s) <= 10 else d.replace(tzinfo=timezone.utc))


def to_date(v):
    d = to_dt(v)
    return d.astimezone(IST).date() if d else None


def hours_between(published, captured):
    p, c = to_dt(published), to_dt(captured)
    return (c - p).total_seconds() / H if p and c else None


def window_for(hours):
    if hours is None:
        return None
    for label, lo, hi, _ in MILESTONES:
        if lo <= hours < hi:
            return label
    return "early"  # under 48 hours


def milestone_status(published, good_snaps, now, first_try_hours=None):
    """published: exact upload time; good_snaps: OK captures with 'age_hours'; now: current time;
    first_try_hours: hours since upload at the first fetch of any kind. Returns {label: {...}} for d2/d7/d30."""
    out = {}
    pub, now = to_dt(published), to_dt(now)
    age_now = (now - pub).total_seconds() / H if pub and now else None
    for label, lo, hi, grace in MILESTONES:
        in_win = [s for s in sorted(good_snaps, key=lambda s: s["captured_at"])
                  if s.get("age_hours") is not None and lo <= s["age_hours"] < hi]
        # the first capture after the mark that has a view count (Facebook sometimes hides it on one fetch)
        hit = next((s for s in in_win if s.get("views") is not None), in_win[0] if in_win else None)
        due = (pub + timedelta(hours=lo)) if pub else None
        late = bool(hit and hit["age_hours"] > lo + grace)
        if hit:
            st = f"Captured late (day {int(hit['age_hours'] // 24)})" if late else "Captured"
        elif age_now is None:
            st = "Waiting for publish date"
        elif age_now < lo:
            st = f"Due {due.astimezone(IST).date().isoformat()}"
        elif age_now < hi:
            st = "Due now"
        elif first_try_hours is None or first_try_hours >= hi:
            st = "Added late"
        else:
            st = "Missed"
        out[label] = {"status": st, "due": due.astimezone(IST).isoformat(timespec="minutes") if due else None,
                      "snap": hit, "late": late}
    return out


def is_due(published, good_snaps, now, has_any_snapshot, alltime_every_days=7, failed_snaps=()):
    """True when a video should be fetched in this run: it has crossed 48 h / 7 d / 30 d and that capture is
    missing, or its all-time count is older than `alltime_every_days` (0 turns the weekly re-count off).
    A link that has never worked is tried at most 3 times, then once a week (it is listed under "To fix")."""
    now = to_dt(now)
    if not good_snaps and len(failed_snaps) >= 3:
        last = max(to_dt(s["captured_at"]) for s in failed_snaps)
        return (now - last) >= timedelta(days=7)
    if not has_any_snapshot or not published:
        return True  # never fetched: fetch once to learn its exact upload time and a first count
    w = window_for(hours_between(published, now))
    if w not in (None, "early") and not any(window_for(s.get("age_hours")) == w for s in good_snaps):
        return True
    if alltime_every_days and good_snaps:
        last = max(to_dt(s["captured_at"]) for s in good_snaps if s.get("captured_at"))
        return (now - last) >= timedelta(days=alltime_every_days) - timedelta(hours=6)
    return False
