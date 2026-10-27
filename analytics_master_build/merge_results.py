"""Merge fetched metrics (Apify / Bright Data result files) into the master list.

Usage: python merge_results.py <result.json> [<result.json> ...]
Reads  source_data.json (master list) and snapshot_history.json (all past fetches).
Writes snapshot_history.json (appended) and analytics_data.json (master + latest metrics).
"""
import json, re, sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

from milestones import MILESTONES, milestone_status, window_for

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source_data.json"
HISTORY = ROOT / "snapshot_history.json"
OUT = ROOT / "analytics_data.json"


def platform_of(url):
    u = (url or "").lower()
    if "drive.google" in u: return "Google Drive Asset"
    if "instagram.com" in u: return "Instagram"
    if "facebook.com" in u or "fb.watch" in u: return "Facebook"
    if "youtu" in u: return "YouTube"
    return "Other"


def norm(url):
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u).replace("www.", "").replace("m.facebook.com", "facebook.com")
    path, _, query = u.split("#")[0].partition("?")
    ids = [kv for kv in query.split("&") if kv.split("=")[0] in ("story_fbid", "fbid", "v", "id")]
    u = path.rstrip("/") + ("?" + "&".join(ids) if ids and re.search(r"(story|permalink|video|photo)\.php$|/watch$", path.rstrip("/")) else "")
    u = u.replace("youtube.com/shorts/", "yt/").replace("youtu.be/", "yt/").replace("youtube.com/watch?v=", "yt/")
    u = re.sub(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/", "instagram.com/p/", u)
    return u


def num(v):
    if v is None or v == "" or isinstance(v, (list, dict)): return None
    try: return int(float(v))
    except (TypeError, ValueError): return None


def parse_dt(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError: return None


IST = timezone(timedelta(hours=5, minutes=30))


def ist_day(dt):
    """Calendar day in India time; ages are counted in calendar days (posted on the 1st -> Day 2 on the 3rd)."""
    return dt.astimezone(IST).date() if dt.tzinfo else dt.date()


def snapshot_type(age):
    return {"d2": "DAY_2", "d7": "DAY_7", "d30": "DAY_30", "early": "EARLY", None: "UNDATED"}[window_for(age)]


def load_records(path):
    d = json.loads(Path(path).read_text(encoding="utf8"))
    source = d.get("source") or "Bright Data"
    out = []
    for r in d.get("records", []):
        raw = r.get("raw") or {}
        input_url = (raw.get("input") or {}).get("url") or r.get("url")
        err = r.get("error") or raw.get("error")
        out.append({
            "input_url": input_url,
            "resolved_url": r.get("url"),
            "published_at": r.get("published_at"),
            "views": num(r.get("views")),
            "likes": num(r.get("likes")),
            "comments": num(r.get("comments")),
            "shares": num(r.get("shares")),
            "saves": num(r.get("saves")),
            "account": r.get("account") or raw.get("page_name") or raw.get("user_username_raw") or raw.get("youtuber") or raw.get("user_posted"),
            "source": source,
            "fetched_at": r.get("fetched_at"),
            "ok": not err and r.get("fetch_status", "OK") == "OK",
            "error": err,
        })
    return out


def main(result_files):
    src = json.loads(SOURCE.read_text(encoding="utf8"))
    master = src["master"]
    history = json.loads(HISTORY.read_text(encoding="utf8")) if HISTORY.exists() else []
    by_key = {norm(m["canonical_url"]): m for m in master}
    for m in master:  # also match on the original (pre-canonical) URL
        by_key.setdefault(norm(m.get("original_url")), m)

    unmatched = []
    for f in result_files:
        for rec in load_records(f):
            m = by_key.get(norm(rec["input_url"])) or by_key.get(norm(rec["resolved_url"]))
            if not m:
                unmatched.append(rec["input_url"]); continue
            pub = parse_dt(rec["published_at"])
            fetched = parse_dt(rec["fetched_at"]) or datetime.now(timezone.utc)
            age = (ist_day(fetched) - ist_day(pub)).days if pub else None
            history.append({
                "snapshot_id": f"SNAP-{len(history) + 1:05d}",
                "master_id": m["master_id"],
                "snapshot_type": snapshot_type(age),
                "captured_at": fetched.isoformat(),
                "age_days": age,
                **{k: rec[k] for k in ("views", "likes", "comments", "shares", "saves", "published_at", "account", "source", "error")},
                "fetch_status": "OK" if rec["ok"] else "Failed",
            })

    HISTORY.write_text(json.dumps(history, indent=1, ensure_ascii=False), encoding="utf8")

    for s in history:  # recount ages in calendar days and label with the current milestone rules
        pub, cap = parse_dt(s.get("published_at")), parse_dt(s.get("captured_at"))
        if pub and cap:
            s["age_days"] = (ist_day(cap) - ist_day(pub)).days
        s["snapshot_type"] = snapshot_type(s.get("age_days"))
    snaps = {}
    for s in history:
        snaps.setdefault(s["master_id"], []).append(s)
    today = datetime.now(timezone.utc)
    for m in master:
        m["platform"] = platform_of(m["canonical_url"])
        ss = sorted(snaps.get(m["master_id"], []), key=lambda s: s["captured_at"])
        good = [s for s in ss if s["fetch_status"] == "OK"]
        last = good[-1] if good else None
        pub = parse_dt(last["published_at"]) if last and last.get("published_at") else None
        if pub:
            m["published_date"] = ist_day(pub).isoformat()
        if m.get("published_date"):
            d0 = date.fromisoformat(m["published_date"][:10])
            m["day_7_due"] = date.fromordinal(d0.toordinal() + 7).isoformat()
            m["day_30_due"] = date.fromordinal(d0.toordinal() + 30).isoformat()
        m["account"] = (last or {}).get("account") or ""
        for k in ("views", "likes", "comments", "shares"):
            m[f"latest_{k}"] = last.get(k) if last else None
        v = m["latest_views"]
        inter = sum(x or 0 for x in (m["latest_likes"], m["latest_comments"], m["latest_shares"]))
        m["latest_engagement_rate"] = round(inter / v, 4) if v else None
        m["last_analytics_at"] = last["captured_at"] if last else ""
        m["data_source"] = last["source"] if last else ""
        for s_ in good:  # re-age snapshots when the publish date became known later
            if m.get("published_date") and s_.get("age_days") is None:
                s_["age_days"] = (ist_day(parse_dt(s_["captured_at"])) - date.fromisoformat(m["published_date"][:10])).days
                s_["snapshot_type"] = snapshot_type(s_["age_days"])
        ms = milestone_status(m.get("published_date"), good, today.astimezone(IST).date())
        for label, _, _, _ in MILESTONES:
            hit = ms[label]["snap"]
            m[f"{label}_status"] = "No public link" if m["content_type"] == "Drive asset" else ms[label]["status"]
            m[f"{label}_due"] = ms[label]["due"]
            m[f"{label}_age"] = hit["age_days"] if hit else None
            for k in ("views", "likes", "comments", "shares"):
                m[f"{label}_{k}"] = hit.get(k) if hit else None
            ints = sum(x or 0 for x in ((hit or {}).get("likes"), (hit or {}).get("comments"), (hit or {}).get("shares")))
            m[f"{label}_engagement_rate"] = round(ints / hit["views"], 4) if hit and hit.get("views") else None
        for old_key in ("day_7_due", "day_30_due", "views_7d", "likes_7d", "comments_7d", "engagement_rate_7d",
                        "views_30d", "likes_30d", "comments_30d", "engagement_rate_30d"):
            m.pop(old_key, None)
        if m.get("sheet_status") == "Removed from sheet":
            m["fetch_status"] = "Removed from sheet"
        elif m["content_type"] == "Drive asset":
            m["fetch_status"] = "No public link"
        elif not ss:
            m["fetch_status"] = "Not fetched"
        elif not good:
            m["fetch_status"] = "Fetch failed"
        elif m["latest_views"] is None:
            m["fetch_status"] = "No view count"
        else:
            m["fetch_status"] = "OK"

    OUT.write_text(json.dumps({"generated_at": today.isoformat(), "config": src["config"], "occurrences": src["occurrences"],
                               "master": master, "snapshots": history}, indent=1, ensure_ascii=False), encoding="utf8")
    from collections import Counter
    print(json.dumps({"snapshots_total": len(history), "unmatched": len(unmatched), "unmatched_sample": unmatched[:5],
                      "fetch_status": Counter(m["fetch_status"] for m in master),
                      "platform": Counter(m["platform"] for m in master)}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
