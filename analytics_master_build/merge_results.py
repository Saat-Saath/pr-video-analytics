"""Merge fetched metrics (Apify / Bright Data result files) into the master list.

Usage: python merge_results.py <result.json> [<result.json> ...]
Reads  source_data.json (master list) and snapshot_history.json (all past fetches).
Writes snapshot_history.json (appended) and analytics_data.json (master + latest metrics).
"""
import json, re, sys
from datetime import datetime, timezone, date
from pathlib import Path

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
    u = u.split("?")[0].split("#")[0].rstrip("/")
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


def snapshot_type(age):
    if age is None: return "UNDATED"
    if age < 7: return "BASELINE"
    if age < 30: return "7_DAY"
    return "30_DAY"


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
            age = (fetched - pub).days if pub else None
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
            m["published_date"] = pub.date().isoformat()
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
        for label, lo, hi in (("7d", 7, 30), ("30d", 30, 10_000)):
            hit = next((s for s in good if s["age_days"] is not None and lo <= s["age_days"] < hi), None)
            m[f"views_{label}"] = hit["views"] if hit else None
            m[f"likes_{label}"] = hit["likes"] if hit else None
            m[f"comments_{label}"] = hit["comments"] if hit else None
            ints = sum(x or 0 for x in ((hit or {}).get("likes"), (hit or {}).get("comments"), (hit or {}).get("shares")))
            m[f"engagement_rate_{label}"] = round(ints / hit["views"], 4) if hit and hit["views"] else None
        if m["content_type"] == "Drive asset":
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
