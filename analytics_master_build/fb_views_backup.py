"""Backup for Facebook view counts.

Bright Data stays the main source. For Facebook reels it returned *today* without a view count, this scans each
page's Reels tab with Apify (apify/facebook-reels-scraper, only reels newer than the oldest missing one) and fills in
the play count on that same snapshot. Likes, comments and shares from Bright Data are left untouched.

  python fb_views_backup.py start [--max-items N]   # start one Apify run per page (cost cap: N reels, ~$0.005 each)
  python fb_views_backup.py collect                  # fill views once the runs finish (exit 2 while still running)
Settings in .env: APIFY_FB_BACKUP=off disables it; APIFY_FB_BACKUP_MAX_ITEMS caps reels scanned per day (default 300).
"""
import json, os, re, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "snapshot_history.json"
STATE = ROOT.parent / "outputs" / "runs" / "fb_backup_state.json"
ACTOR = "apify~facebook-reels-scraper"
IST = timezone(timedelta(hours=5, minutes=30))


def env():
    for line in (ROOT.parent / ".env").read_text(encoding="utf8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())


def api(path, body=None):
    req = urllib.request.Request(f"https://api.apify.com/v2/{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}", "Content-Type": "application/json"},
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def reel_id(url):
    m = re.search(r"/(?:reel|reels|videos|watch/?\?v=)/?(\d{8,})", url or "") or re.search(r"[?&]v=(\d{8,})", url or "")
    return m.group(1) if m else None


def ist_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(IST).date()


def candidates(hist, today):
    out = defaultdict(list)
    for s in hist:
        if (s.get("source") == "Bright Data" and s.get("fetch_status") == "OK" and s.get("views") is None
                and s.get("page_url") and reel_id(s.get("post_url")) and s.get("published_at")
                and ist_date(s["captured_at"]) == today and "facebook.com" in (s.get("post_url") or "")):
            out[s["page_url"]].append(s)
    return out


MAX_PARALLEL = 4  # the free Apify plan allows 5 runs at once; keep one slot spare


def launch(job):
    run = api(f"acts/{ACTOR}/runs?memory=2048", {"startUrls": [{"url": job["page"]}], "onlyPostsNewerThan": job["since"], "resultsLimit": job["limit"]})["data"]
    job.update(run_id=run["id"], dataset=run["defaultDatasetId"])


def start(max_items, adopt=None):
    env()
    if os.environ.get("APIFY_FB_BACKUP", "on").lower() == "off":
        print("backup disabled"); return 0
    hist = json.loads(HISTORY.read_text(encoding="utf8"))
    today = datetime.now(IST).date()
    pages = sorted(candidates(hist, today).items(), key=lambda kv: -len(kv[1]))
    budget, jobs = max_items, []
    for page, snaps in pages:
        if budget <= 0:
            break
        oldest = min(ist_date(s["published_at"]) for s in snaps) - timedelta(days=1)
        limit = min(budget, 200, (today - oldest).days * 6 + 10)  # Apify bills only reels returned
        jobs.append({"page": page, "since": oldest.isoformat(), "limit": limit, "missing": len(snaps)})
        budget -= limit
    for job in jobs:  # runs already started for a page (adopt) are reused instead of started again
        if adopt and job["page"] in adopt:
            job.update(adopt[job["page"]])
    running = sum(1 for j in jobs if j.get("run_id"))
    for job in jobs:
        if not job.get("run_id") and running < MAX_PARALLEL:
            launch(job); running += 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": today.isoformat(), "runs": jobs}, indent=1))
    skipped = len(pages) - len(jobs)
    print(f"backup queued: {len(jobs)} pages ({running} running), {sum(j['missing'] for j in jobs)} reels missing views"
          + (f", {skipped} pages skipped (cost cap)" if skipped else ""))
    return len(jobs)


def collect():
    env()
    if not STATE.exists():
        print("no backup runs"); return 0
    state = json.loads(STATE.read_text())
    if not state.get("runs"):
        print("no backup runs"); return 0
    running = 0
    for r in state["runs"]:  # refresh statuses, then start queued pages while slots are free
        if r.get("run_id") and r.get("status") not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            run = api(f"actor-runs/{r['run_id']}")["data"]
            r["status"], r["cost"] = run["status"], run.get("usageTotalUsd") or 0
            running += run["status"] in ("READY", "RUNNING")
    for r in state["runs"]:
        if not r.get("run_id") and running < MAX_PARALLEL:
            launch(r); running += 1
    STATE.write_text(json.dumps(state, indent=1))
    if running or any(not r.get("run_id") for r in state["runs"]):
        done = sum(1 for r in state["runs"] if r.get("status") == "SUCCEEDED")
        print(f"backup still running: {done}/{len(state['runs'])} pages done"); sys.exit(2)
    plays, cost = {}, 0.0
    for r in state["runs"]:
        cost += r.get("cost") or 0
        if r.get("status") != "SUCCEEDED":
            continue
        for it in api(f"datasets/{r['dataset']}/items?clean=true&format=json"):
            rid = reel_id(it.get("topLevelReelUrl") or it.get("shareable_url") or it.get("url") or "")
            n = it.get("playCountRounded") or it.get("play_count_reduced")
            if rid and n is not None:
                plays[rid] = int(n)
    hist = json.loads(HISTORY.read_text(encoding="utf8"))
    day = date.fromisoformat(state["date"])
    filled = 0
    for s in hist:
        if s.get("views") is None and s.get("source") == "Bright Data" and ist_date(s["captured_at"]) == day:
            rid = reel_id(s.get("post_url"))
            if rid in plays:
                s["views"] = plays[rid]; s["views_source"] = "Apify reels tab (backup)"; filled += 1
    HISTORY.write_text(json.dumps(hist, indent=1, ensure_ascii=False), encoding="utf8")
    STATE.write_text(json.dumps({"date": state["date"], "runs": [], "last": {"filled": filled, "cost_usd": round(cost, 3)}}, indent=1))
    print(f"backup filled {filled} view counts (Apify cost ${cost:.2f})")
    return filled


if __name__ == "__main__":
    if sys.argv[1:2] == ["start"]:
        mx = int(sys.argv[sys.argv.index("--max-items") + 1]) if "--max-items" in sys.argv else None
        env(); start(mx or int(os.environ.get("APIFY_FB_BACKUP_MAX_ITEMS", "300")))
    elif sys.argv[1:2] == ["collect"]:
        collect()
    else:
        print(__doc__)
