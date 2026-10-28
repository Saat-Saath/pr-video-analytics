"""Backup for Facebook view counts.

Bright Data stays the main source. For Facebook reels with no view count, this asks Apify
(apify/facebook-reels-scraper) for each reel by its own link and fills in the play count. Asking by reel link works for
personal profiles too (their Reels tab cannot be scanned) and costs about $0.005 per reel.

Which reels: every Facebook reel whose latest capture has no view count. A reel captured today is asked right away;
any other is asked again once a week (fb_backup_tries.json keeps the last try), so a reel whose owner hides the count
does not cost money every day. A count found for today's capture fills that capture; otherwise it is saved as a new
capture taken now (so it counts as the reel's all-time views and is never written into an older Day 2 / Day 7 count).

  python fb_views_backup.py start [--max-items N]   # start the Apify run (cost cap: N reels)
  python fb_views_backup.py collect                  # fill views once the run finishes (exit 2 while still running)
Settings in .env: APIFY_FB_BACKUP=off disables it; APIFY_FB_BACKUP_MAX_ITEMS caps reels asked per run (default 150).
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


TRIES = ROOT / "fb_backup_tries.json"
RETRY_DAYS = 7


def candidates(hist, today):
    """{reel id: latest capture} for Facebook reels whose latest capture has no view count and are due a try."""
    tries = json.loads(TRIES.read_text()) if TRIES.exists() else {}
    latest, counted = {}, {s_["master_id"] for s_ in hist if s_.get("views") is not None}
    for s_ in hist:
        if s_.get("fetch_status") == "OK" and "facebook.com" in (s_.get("post_url") or "") and s_["master_id"] not in counted:
            if s_["master_id"] not in latest or s_["captured_at"] > latest[s_["master_id"]]["captured_at"]:
                latest[s_["master_id"]] = s_
    out = {}
    for s_ in latest.values():
        rid = reel_id(s_.get("post_url"))
        if not rid or s_.get("views") is not None:
            continue
        last = tries.get(rid)
        fresh = ist_date(s_["captured_at"]) == today
        if fresh or not last or (today - date.fromisoformat(last)).days >= RETRY_DAYS:
            out[rid] = s_
    return out


MAX_PARALLEL = 4  # the free Apify plan allows 5 runs at once; keep one slot spare
BATCH = 50        # reels per Apify run


def launch(job):
    run = api(f"acts/{ACTOR}/runs?memory=2048", {"startUrls": [{"url": f"https://www.facebook.com/reel/{r}"} for r in job["reels"]],
                                                 "resultsLimit": len(job["reels"])})["data"]
    job.update(run_id=run["id"], dataset=run["defaultDatasetId"])


def start(max_items, adopt=None):
    env()
    if os.environ.get("APIFY_FB_BACKUP", "on").lower() == "off":
        print("backup disabled"); return 0
    hist = json.loads(HISTORY.read_text(encoding="utf8"))
    today = datetime.now(IST).date()
    cand = candidates(hist, today)
    # today's captures first, then the reels that have waited longest
    order = sorted(cand, key=lambda r: (ist_date(cand[r]["captured_at"]) != today, cand[r]["captured_at"]))[:max_items]
    jobs = [{"reels": order[i:i + BATCH]} for i in range(0, len(order), BATCH)]
    running = 0
    for job in jobs:
        if running < MAX_PARALLEL:
            launch(job); running += 1
    tries = json.loads(TRIES.read_text()) if TRIES.exists() else {}
    tries.update({r: today.isoformat() for r in order})
    TRIES.write_text(json.dumps(tries, indent=1, sort_keys=True))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": today.isoformat(), "runs": jobs}, indent=1))
    print(f"backup queued: {len(order)} reels without a view count in {len(jobs)} runs"
          + (f", {len(cand) - len(order)} left for the next run (cost cap)" if len(cand) > len(order) else ""))
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
    now = datetime.now(timezone.utc)
    latest = {}
    for s in hist:
        if s.get("fetch_status") == "OK" and reel_id(s.get("post_url")) in plays:
            if s["master_id"] not in latest or s["captured_at"] > latest[s["master_id"]]["captured_at"]:
                latest[s["master_id"]] = s
    filled = 0
    for s in latest.values():
        n = plays[reel_id(s["post_url"])]
        if s.get("views") is not None:
            continue
        if ist_date(s["captured_at"]) == day:  # captured today: fill that capture
            s["views"] = n; s["views_source"] = "Apify reel (backup)"
        else:  # an older capture keeps its own (missing) count; the count found now is a new capture
            hist.append({**{k: s.get(k) for k in ("master_id", "published_at", "account", "page_url", "caption", "thumb_url", "post_url")},
                         "snapshot_id": f"SNAP-{len(hist) + 1:05d}", "captured_at": now.isoformat(), "views": n,
                         "likes": None, "comments": None, "shares": None, "saves": None, "source": "Apify reel (backup)",
                         "error": None, "fetch_status": "OK"})
        filled += 1
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
