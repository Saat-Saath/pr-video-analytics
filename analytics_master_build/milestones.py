"""Milestone rules: analytics are captured 2, 7 and 30 days after upload.

A video is fetched once inside each window, as close to its start as possible:
  Day 2  -> ages 2-6, Day 7 -> ages 7-29, Day 30 -> age 30+ (age = whole days since upload).
A capture later than the grace days is kept but flagged late, with the day it was captured.
"""
from datetime import date, datetime, timedelta

MILESTONES = [  # (label, start age, end age exclusive, grace days before "late")
    ("d2", 2, 7, 1),
    ("d7", 7, 30, 2),
    ("d30", 30, 10_000, 5),
]
LABEL = {"d2": "Day 2", "d7": "Day 7", "d30": "Day 30"}


def to_date(v):
    if not v: return None
    if isinstance(v, date) and not isinstance(v, datetime): return v
    try: return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError: return None


def window_for(age):
    if age is None: return None
    for label, lo, hi, _ in MILESTONES:
        if lo <= age < hi: return label
    return "early"  # age 0-1


def milestone_status(published, good_snaps, today):
    """good_snaps: OK snapshots with 'age_days'. Returns {label: {...}} for d2/d7/d30."""
    out = {}
    pub = to_date(published)
    age_today = (today - pub).days if pub else None
    for label, lo, hi, grace in MILESTONES:
        hit = next((s for s in sorted(good_snaps, key=lambda s: s["captured_at"])
                    if s.get("age_days") is not None and lo <= s["age_days"] < hi), None)
        due = (pub + timedelta(days=lo)) if pub else None
        if hit:
            late = hit["age_days"] > lo + grace
            st = f"Captured late (day {hit['age_days']})" if late else "Captured"
        elif age_today is None:
            st = "Waiting for publish date"
        elif age_today < lo:
            st = f"Due {due.isoformat()}"
        elif age_today < hi:
            st = "Due now"
        else:
            st = "Missed"
        out[label] = {"status": st, "due": due.isoformat() if due else None, "snap": hit,
                      "late": bool(hit and hit["age_days"] > lo + grace)}
    return out


def is_due(published, good_snaps, today, has_any_snapshot, alltime_every_days=7):
    """True when a public video should be fetched today: a Day 2 / 7 / 30 capture is due, or its
    all-time count is older than `alltime_every_days` (0 turns the all-time re-fetch off)."""
    if not has_any_snapshot or not published:
        return True  # never fetched: fetch once to learn its publish date and baseline
    age = (today - to_date(published)).days
    w = window_for(age)
    if w not in (None, "early") and not any(window_for(s.get("age_days")) == w for s in good_snaps):
        return True
    if alltime_every_days and good_snaps:
        last = max(to_date(s["captured_at"]) for s in good_snaps if s.get("captured_at"))
        return (today - last).days >= alltime_every_days
    return False
