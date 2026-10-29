"""Apify backup for Facebook, used only where Bright Data (the main source) failed or came back incomplete.

  Views:   a Facebook reel whose captures have no view count is asked from Apify's Reels Scraper by its reel link
           (works for personal profiles too, about $0.005 per reel).
  Details: a Facebook video whose Day 2 / Day 7 / Day 30 Bright Data capture (last 30 hours) has no likes, comments
           or shares (asked once per milestone), or a
           link Bright Data could not open at all, is asked from Apify's Posts Scraper (likes, comments, shares, the
           real reel link, upload time and owner; about $0.005 per post). When it opens a link Bright Data could not,
           the reel's view count is then asked from the Reels Scraper in the same run.

Nothing Bright Data returned is ever replaced: only empty numbers are filled. A count for a capture taken in the last
30 hours fills that capture; otherwise it is saved as a new capture taken now (so it counts as the all-time number and
never lands in an older Day 2 / Day 7 count). Each link is asked at most once a week unless Bright Data captured it in
the last 30 hours (fb_backup_tries.json keeps the last try), so a post that hides its numbers does not cost every day.

  python fb_views_backup.py start [--max-items N]   # start the Apify runs (cost cap: N reels and N posts)
  python fb_views_backup.py collect                  # fill the numbers once the runs finish (exit 2 while running)
It runs once a day, in the first refresh after APIFY_FB_BACKUP_FROM (default 10:00 India time).
Settings in .env: APIFY_FB_BACKUP=off disables it; APIFY_FB_BACKUP_MAX_ITEMS caps reels asked per run (default 150);
APIFY_FB_DETAILS_MAX_ITEMS caps posts asked for likes / comments / shares per run (default 30, 0 turns it off).
"""
import json, os, re, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from milestones import hours_between, window_for  # noqa: E402

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "snapshot_history.json"
STATE = ROOT.parent / "outputs" / "runs" / "fb_backup_state.json"
ACTOR = "apify~facebook-reels-scraper"
POSTS_ACTOR = "apify~facebook-posts-scraper"
DATA = ROOT / "analytics_data.json"
FRESH_HOURS = 30
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
    if job.get("kind") == "posts":
        run = api(f"acts/{POSTS_ACTOR}/runs?memory=1024", {"startUrls": [{"url": u} for u in job["urls"]],
                                                          "resultsLimit": len(job["urls"])})["data"]
    else:
        run = api(f"acts/{ACTOR}/runs?memory=2048", {"startUrls": [{"url": f"https://www.facebook.com/reel/{r}"} for r in job["reels"]],
                                                     "resultsLimit": len(job["reels"])})["data"]
    job.update(run_id=run["id"], dataset=run["defaultDatasetId"])


def hours_ago(iso, now):
    return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def detail_candidates(hist, now, today):
    """{link: master_id} for Facebook videos that need likes / comments / shares from the backup: the latest Bright
    Data capture (last 30 hours) is missing one of them, or Bright Data has never opened the link."""
    if not DATA.exists():
        return {}
    master = [m for m in json.loads(DATA.read_text(encoding="utf8"))["master"]
              if m.get("platform") == "Facebook" and m.get("sheet_status") != "Removed from sheet" and m.get("content_type") != "Drive asset"]
    tries = json.loads(TRIES.read_text()) if TRIES.exists() else {}
    by = defaultdict(list)
    for s_ in hist:
        by[s_["master_id"]].append(s_)
    out = {}
    for m in master:
        ss = sorted(by.get(m["master_id"], []), key=lambda s_: s_["captured_at"])
        good = [s_ for s_ in ss if s_.get("fetch_status") == "OK"]
        last_try = tries.get("post:" + m["master_id"])
        weekly = not last_try or (today - date.fromisoformat(last_try)).days >= RETRY_DAYS
        if good:
            last = good[-1]
            if last.get("source") != "Bright Data" or hours_ago(last["captured_at"], now) > FRESH_HOURS:
                continue  # only a fresh, incomplete Bright Data capture is a backup case
            if all(last.get(k) is not None for k in ("likes", "comments", "shares")):
                continue
            # only the Day 2 / Day 7 / Day 30 capture (the first in its window), once per milestone; the weekly
            # all-time re-counts are not worth paying for
            w = window_for(hours_between(m.get("published_at") or last.get("published_at"), last["captured_at"]))
            if w not in ("d2", "d7", "d30") or tries.get(f"post:{m['master_id']}:{w}"):
                continue
            if any(window_for(hours_between(m.get("published_at") or last.get("published_at"), x["captured_at"])) == w
                   for x in good[:-1]):
                continue
            url = last.get("post_url") or m.get("canonical_url")
        elif ss and weekly:  # Bright Data could not open the link at all
            url = m.get("original_url") or m.get("canonical_url")
        else:
            continue
        if url and url.startswith("http"):
            out[url] = (m["master_id"], bool(good), w if good else None)
    return {u: (mid, w) for u, (mid, opened, w) in sorted(out.items(), key=lambda x: (x[1][1], x[1][0]))}  # unopened first


def start(max_items, adopt=None):
    env()
    if os.environ.get("APIFY_FB_BACKUP", "on").lower() == "off":
        print("backup disabled"); return 0
    hist = json.loads(HISTORY.read_text(encoding="utf8"))
    now_ist = datetime.now(IST)
    today = now_ist.date()
    tries = json.loads(TRIES.read_text()) if TRIES.exists() else {}
    if tries.get("_last_run") == today.isoformat():
        print("backup already ran today"); return 0
    h, m = (int(x) for x in os.environ.get("APIFY_FB_BACKUP_FROM", "10:00").split(":"))
    if (now_ist.hour, now_ist.minute) < (h, m):
        print(f"backup waits for the first run after {h:02d}:{m:02d}"); return 0
    cand = candidates(hist, today)
    # today's captures first, then the reels that have waited longest
    order = sorted(cand, key=lambda r: (ist_date(cand[r]["captured_at"]) != today, cand[r]["captured_at"]))[:max_items]
    jobs = [{"kind": "reels", "reels": order[i:i + BATCH]} for i in range(0, len(order), BATCH)]
    now = datetime.now(timezone.utc)
    dmax = int(os.environ.get("APIFY_FB_DETAILS_MAX_ITEMS", "30") or 0)
    dcand = detail_candidates(hist, now, today) if dmax > 0 else {}
    links = list(dcand)[:dmax]
    jobs += [{"kind": "posts", "urls": links[i:i + BATCH], "masters": {u: dcand[u][0] for u in links[i:i + BATCH]}}
             for i in range(0, len(links), BATCH)]
    running = 0
    for job in jobs:
        if running < MAX_PARALLEL:
            launch(job); running += 1
    tries["_last_run"] = today.isoformat()
    tries.update({r: today.isoformat() for r in order})
    tries.update({f"post:{dcand[u][0]}" + (f":{dcand[u][1]}" if dcand[u][1] else ""): today.isoformat() for u in links})
    TRIES.write_text(json.dumps(tries, indent=1, sort_keys=True))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": today.isoformat(), "runs": jobs}, indent=1))
    print(f"backup queued: {len(order)} reels without a view count and {len(links)} posts missing likes / comments /"
          f" shares in {len(jobs)} runs"
          + (f", {len(cand) - len(order) + len(dcand) - len(links)} left for the next run (cost cap)"
             if len(cand) > len(order) or len(dcand) > len(links) else ""))
    return len(jobs)


def count(v):
    """Facebook's short counts: 12, "12", "1.2K", "3M" -> int; anything else -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.,]+)\s*([KkMm]?)\s*", str(v))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(round(n * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]))


def post_details(it):
    """What the Posts Scraper found for one link (None when it could not open it)."""
    if it.get("error"):
        return None
    sf = it.get("short_form_video_context") or {}
    owner = (sf.get("fb_shorts_reshare_context") or {}).get("reshare_creator") or {}
    reel = sf.get("shareable_url") or ((sf.get("playback_video") or {}).get("permalink_url")) or it.get("url") or ""
    ct = it.get("creation_time")
    thumb = (((sf.get("playback_video") or {}).get("preferred_thumbnail") or {}).get("image") or {}).get("uri")
    return {"likes": count(it.get("likes") if it.get("likes") is not None else (it.get("unified_reactors") or {}).get("count")),
            "comments": count(it.get("total_comment_count") if it.get("total_comment_count") is not None else it.get("comments")),
            "shares": count(it.get("share_count_reduced")),
            "post_url": reel.rstrip("/") if reel else "",
            "published_at": datetime.fromtimestamp(ct, timezone.utc).isoformat().replace("+00:00", ".000Z") if isinstance(ct, (int, float)) else None,
            "account": (sf.get("video_owner") or {}).get("name") or owner.get("name") or it.get("pageName") or "",
            "page_url": owner.get("url") or "", "thumb_url": thumb or "",
            "caption": ((it.get("message") or {}).get("text") if isinstance(it.get("message"), dict) else it.get("message")) or ""}


def collect():
    env()
    if not STATE.exists():
        print("no backup runs"); return 0
    state = json.loads(STATE.read_text())
    if not state.get("runs"):
        print("no backup runs"); return 0
    running = 0
    for r in state["runs"]:  # refresh statuses, then start queued runs while slots are free
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
        print(f"backup still running: {done}/{len(state['runs'])} runs done"); sys.exit(2)
    plays, details, cost = {}, {}, 0.0
    for r in state["runs"]:
        cost += r.get("cost") or 0
        if r.get("status") != "SUCCEEDED":
            continue
        items = api(f"datasets/{r['dataset']}/items?clean=true&format=json")
        if r.get("kind") == "posts":
            for it in items:
                link = it.get("facebookUrl") or it.get("inputUrl") or it.get("url")
                mid = r["masters"].get(link)
                got = post_details(it)
                if mid and got:
                    details[mid] = got
            continue
        for it in items:
            rid = reel_id(it.get("topLevelReelUrl") or it.get("shareable_url") or it.get("url") or "")
            n = it.get("playCountRounded") or it.get("play_count_reduced")
            if rid and n is not None:
                plays[rid] = count(n)
    hist = json.loads(HISTORY.read_text(encoding="utf8"))
    good = defaultdict(list)
    for s in hist:
        if s.get("fetch_status") == "OK":
            good[s["master_id"]].append(s)
    # a link Bright Data could not open, now opened by the Posts Scraper: ask its view count from the Reels Scraper
    # in this same run (once)
    if not state.get("followup"):
        new = sorted({reel_id(d["post_url"]) for mid, d in details.items() if not good.get(mid) and reel_id(d["post_url"])}
                     - set(plays))
        if new:
            state["followup"] = True
            state["details"], state["plays"] = details, plays
            job = {"kind": "reels", "reels": new}
            launch(job)
            state["runs"].append(job)
            STATE.write_text(json.dumps(state, indent=1))
            print(f"backup: {len(new)} links opened by Apify, asking their view counts"); sys.exit(2)
    else:
        details = {**state.get("details", {}), **details}
        plays = {**state.get("plays", {}), **plays}
    now = datetime.now(timezone.utc)
    added = lambda base, **kw: hist.append({**base, "snapshot_id": f"SNAP-{len(hist) + 1:05d}", "captured_at": now.isoformat(),
                                            "saves": None, "error": None, "fetch_status": "OK", **kw})
    filled_views = filled_details = opened = 0
    # 1. view counts
    latest = {}
    for s in hist:
        if s.get("fetch_status") == "OK" and reel_id(s.get("post_url")) in plays:
            if s["master_id"] not in latest or s["captured_at"] > latest[s["master_id"]]["captured_at"]:
                latest[s["master_id"]] = s
    for s in latest.values():
        n = plays[reel_id(s["post_url"])]
        if s.get("views") is not None or n is None or any(x.get("views") is not None for x in good[s["master_id"]]):
            continue
        if hours_ago(s["captured_at"], now) <= FRESH_HOURS:  # a fresh capture: fill it
            s["views"] = n; s["views_source"] = "Apify reel (backup)"
        else:  # an older capture keeps its own (missing) count; the count found now is a new capture
            added({k: s.get(k) for k in ("master_id", "published_at", "account", "page_url", "caption", "thumb_url", "post_url")},
                  views=n, likes=None, comments=None, shares=None, source="Apify reel (backup)")
        filled_views += 1
    # 2. likes / comments / shares, and links Bright Data could not open
    for mid, d in details.items():
        gs = sorted(good.get(mid, []), key=lambda s_: s_["captured_at"])
        if gs:
            last = gs[-1]
            if hours_ago(last["captured_at"], now) > FRESH_HOURS + 6:
                continue
            got = [k for k in ("likes", "comments", "shares") if last.get(k) is None and d.get(k) is not None]
            for k in got:
                last[k] = d[k]
            if got:
                last["details_source"] = "Apify posts (backup): " + ", ".join(got)
                filled_details += 1
        else:
            rid = reel_id(d["post_url"])
            added({"master_id": mid, "published_at": d["published_at"], "account": d["account"], "page_url": d["page_url"],
                   "caption": d["caption"], "thumb_url": d["thumb_url"], "post_url": d["post_url"]},
                  views=plays.get(rid) if rid else None, likes=d["likes"], comments=d["comments"], shares=d["shares"],
                  source="Apify (backup)")
            opened += 1
    HISTORY.write_text(json.dumps(hist, indent=1, ensure_ascii=False), encoding="utf8")
    STATE.write_text(json.dumps({"date": state["date"], "runs": [], "last": {
        "views": filled_views, "details": filled_details, "opened": opened, "cost_usd": round(cost, 3)}}, indent=1))
    print(f"backup filled {filled_views} view counts, likes / comments / shares on {filled_details} videos, opened "
          f"{opened} links Bright Data could not (Apify cost ${cost:.2f})")
    return filled_views + filled_details + opened


if __name__ == "__main__":
    if sys.argv[1:2] == ["start"]:
        mx = int(sys.argv[sys.argv.index("--max-items") + 1]) if "--max-items" in sys.argv else None
        env(); start(mx or int(os.environ.get("APIFY_FB_BACKUP_MAX_ITEMS", "300")))
    elif sys.argv[1:2] == ["collect"]:
        collect()
    else:
        print(__doc__)
