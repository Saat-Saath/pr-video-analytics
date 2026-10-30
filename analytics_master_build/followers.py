"""Follower counts of every account our videos are posted on, one reading a day.

Facebook and YouTube: Bright Data returns the page's followers / the channel's subscribers with every post it
fetches (page_followers, subscribers), so each morning refresh re-fetches one recent video of every account that has
no reading yet that day (refresh.py, account_checks). Facebook shows big pages rounded (51K), so their growth moves in
steps of about 1,000.
Instagram: Apify's profile scraper gives the exact follower count; `python followers.py instagram` asks for every
Instagram account once a day (after FOLLOWERS_FROM, default 10:00 India time) and keeps it in followers_ig.json.

`history(data)` turns all of it into {account key: {"platform", "name", "url", "history": [[date, followers], ...]}}
for the dashboard (one reading per India date, the last of the day).
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IG_FILE = ROOT / "followers_ig.json"
IST = timezone(timedelta(hours=5, minutes=30))
sys.path.insert(0, str(ROOT))
from costs import link_key  # noqa: E402


def ist_date(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(IST).date().isoformat()


def due_time():
    h, m = (int(x) for x in os.environ.get("FOLLOWERS_FROM", "10:00").split(":"))
    now = datetime.now(IST)
    return (now.hour, now.minute) >= (h, m), now.date().isoformat()


def account_checks(data, limit=80):
    """Canonical links of one recent Facebook / YouTube video per account that has no follower reading today."""
    ok, today = due_time()
    if not ok:
        return {"Facebook": [], "YouTube": []}
    master = {m["master_id"]: m for m in data["master"] if m.get("sheet_status") != "Removed from sheet"}
    read_today, latest = set(), {}
    for s in data["snapshots"]:
        m = master.get(s["master_id"])
        if not m or s.get("fetch_status") != "OK" or m["platform"] not in ("Facebook", "YouTube"):
            continue
        k = link_key(s.get("page_url"))
        if not k:
            continue
        if s.get("followers") is not None and ist_date(s["captured_at"]) == today:
            read_today.add(k)
        pub = m.get("published_at") or m.get("published_date") or ""
        if k not in latest or pub > latest[k][0]:
            latest[k] = (pub, m["platform"], m["canonical_url"])
    out = {"Facebook": [], "YouTube": []}
    for k, (_, plat, url) in sorted(latest.items(), key=lambda kv: kv[1][0], reverse=True):
        if k not in read_today and sum(len(v) for v in out.values()) < limit:
            out[plat].append(url)
    return out


def ig_handles(data):
    hs = set()
    for m in data["master"]:
        if m.get("platform") == "Instagram" and m.get("sheet_status") != "Removed from sheet":
            a = (m.get("account") or "").strip().lstrip("@")
            if a and " " not in a:
                hs.add(a.lower())
    return sorted(hs)


def instagram():
    """Read every Instagram account's follower count once a day (Apify profile scraper, about $0.003 per account)."""
    ok, today = due_time()
    store = json.loads(IG_FILE.read_text()) if IG_FILE.exists() else {}
    if not ok or store.get("_last") == today:
        print("instagram followers: not due"); return
    for line in (ROOT.parent / ".env").read_text(encoding="utf8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
    data = json.loads((ROOT / "analytics_data.json").read_text(encoding="utf8"))
    handles = ig_handles(data)
    if not handles:
        return
    req = urllib.request.Request("https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items?timeout=240",
                                 data=json.dumps({"usernames": handles}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        items = json.loads(r.read())
    n = 0
    for it in items:
        u, f = (it.get("username") or "").lower(), it.get("followersCount")
        if u and isinstance(f, int):
            hist = [p for p in store.get(u, []) if p[0] != today]
            store[u] = hist + [[today, f]]
            n += 1
    store["_last"] = today
    IG_FILE.write_text(json.dumps(store, indent=0, sort_keys=True))
    print(f"instagram followers: {n} of {len(handles)} accounts read")


def history(data):
    """{account key: {"platform", "name", "url", "history": [[India date, followers], ...]}} from all readings."""
    out = {}
    master = {m["master_id"]: m for m in data["master"]}
    for s in sorted(data["snapshots"], key=lambda s: s["captured_at"]):
        f, k = s.get("followers"), link_key(s.get("page_url"))
        if f is None or not k:
            continue
        m = master.get(s["master_id"], {})
        a = out.setdefault(k, {"platform": m.get("platform"), "name": s.get("account") or m.get("account"), "url": s.get("page_url"), "points": {}})
        a["points"][ist_date(s["captured_at"])] = f
    store = json.loads(IG_FILE.read_text()) if IG_FILE.exists() else {}
    for u, pts in store.items():
        if u.startswith("_"):
            continue
        a = out.setdefault("ig:" + u, {"platform": "Instagram", "name": u, "url": f"https://www.instagram.com/{u}/", "points": {}})
        for d, f in pts:
            a["points"][d] = f
    for a in out.values():
        a["history"] = sorted(a.pop("points").items())
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["instagram"]:
        instagram()
    else:
        print(__doc__)
