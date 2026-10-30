"""Daily refresh: sync the sheets, fetch every video that is due for a Day 2 / 7 / 30 capture or for its
all-time re-count (every ALL_TIME_REFRESH_DAYS days, default 7), rebuild outputs.

  python refresh.py start    # sync sheets, work out what is due today, start the scraper runs
  python refresh.py finish   # collect finished runs, merge, run the Apify backup for missing Facebook views,
                             # rebuild workbook and dashboard data
  python refresh.py status   # show what is due without starting anything

`finish` exits with code 2 while runs are still collecting; run it again a few minutes later.
"""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "analytics_master_build"
RUNS = ROOT / "outputs" / "runs"
sys.path.insert(0, str(BUILD))
from milestones import is_due  # noqa: E402

BD_DATASETS = {"Facebook": "gd_lyclm1571iy3mv57zw", "YouTube": "gd_lk56epmy2i5g7lzu0k"}


def env():
    if not (ROOT / ".env").exists():
        return
    for line in (ROOT / ".env").read_text(encoding="utf8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def py(script, *args):
    return subprocess.run([sys.executable, str(BUILD / script), *args], check=True, capture_output=True, text=True).stdout


def node(*args):
    return subprocess.run(["node", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def bd(path, body=None):
    req = urllib.request.Request(f"https://api.brightdata.com/datasets/v3/{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}", "Content-Type": "application/json"},
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def due_today():
    env()
    every = int(os.environ.get("ALL_TIME_REFRESH_DAYS", "7") or 0)  # re-fetch all-time counts every N days (0 = off)
    data = json.loads((BUILD / "analytics_data.json").read_text(encoding="utf8"))
    snaps = {}
    for s in data["snapshots"]:
        snaps.setdefault(s["master_id"], []).append(s)
    now = datetime.now(timezone.utc)  # milestones are counted in hours from the exact upload time
    due = {"Instagram": [], "Facebook": [], "YouTube": []}
    for m in data["master"]:
        if m["content_type"] != "Public post" or m.get("sheet_status") == "Removed from sheet" or m["platform"] not in due:
            continue
        ss = snaps.get(m["master_id"], [])
        good = [s for s in ss if s["fetch_status"] == "OK"]
        since = m.get("link_replaced_on") or ""  # failures of an old link do not count against a new one
        failed = [s for s in ss if s["fetch_status"] != "OK" and s["captured_at"][:10] > since]
        if is_due(m.get("published_at") or m.get("published_date"), good, now, bool(ss), every, failed):
            due[m["platform"]].append(m["canonical_url"])
    return due


def start():
    env()
    print(py("sync_sheets.py").strip().splitlines()[0:1] and "sheets synced")
    py("merge_results.py")  # refresh milestone fields for any new videos
    due = due_today()
    print("due today:", {k: len(v) for k, v in due.items()})
    # once a day: one recent video of every Facebook / YouTube account, so each account gets a follower reading
    try:
        sys.path.insert(0, str(BUILD))
        import followers
        data = json.loads((BUILD / "analytics_data.json").read_text(encoding="utf8"))
        extra = followers.account_checks(data)
        for plat, urls in extra.items():
            due[plat] += [u for u in urls if u not in due[plat]]
        print("follower checks:", {k: len(v) for k, v in extra.items()})
        r = subprocess.run([sys.executable, str(BUILD / "followers.py"), "instagram"], capture_output=True, text=True, timeout=330)
        print((r.stdout or r.stderr).strip()[-200:])
    except Exception as e:  # never stop the refresh over follower counts
        print("follower checks skipped:", e)
    run_dir = RUNS / datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"started_at": datetime.now(timezone.utc).isoformat(), "runs": []}
    if due["Instagram"]:
        (run_dir / "ig-urls.json").write_text(json.dumps(due["Instagram"]))
        out = json.loads(node("apify_instagram.mjs", "--urls-file", str(run_dir / "ig-urls.json"), "--start-only").strip().splitlines()[-1])
        state["runs"].append({"platform": "instagram", "service": "apify", "id": out["run_id"], "result": str(run_dir / "ig-result.json"), "n": len(due["Instagram"])})
    for plat, ds in BD_DATASETS.items():
        if due[plat]:
            sid = bd(f"trigger?dataset_id={ds}&include_errors=true&format=json", {"input": [{"url": u} for u in due[plat]]})["snapshot_id"]
            state["runs"].append({"platform": plat.lower(), "service": "brightdata", "id": sid, "result": str(run_dir / f"{plat.lower()}-result.json"), "n": len(due[plat])})
    (run_dir / "state.json").write_text(json.dumps(state, indent=1))
    (RUNS / "pending.json").write_text(json.dumps({"run_dir": str(run_dir)}))
    print("started:", [(r["platform"], r["n"]) for r in state["runs"]] or "nothing due")


def finish():
    env()
    pend = RUNS / "pending.json"
    stage = json.loads(pend.read_text() or "{}").get("stage") if pend.exists() else None
    if stage == "backup":  # step 2: Apify backup for Facebook view counts Bright Data could not get
        r = subprocess.run([sys.executable, str(BUILD / "fb_views_backup.py"), "collect"], capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode == 2:
            sys.exit(2)
        pend.write_text("{}")
        print(py("merge_results.py").strip().splitlines()[-1])
        py("build_workbook.py")
        print(py("export_dashboard.py").strip())
        return
    if not pend.exists() or not json.loads(pend.read_text() or "{}"):
        print("no pending runs; rebuilding outputs only")
        files = []
    else:
        run_dir = Path(json.loads(pend.read_text())["run_dir"])
        state = json.loads((run_dir / "state.json").read_text())
        waiting = []
        for r in state["runs"]:
            if Path(r["result"]).exists():
                continue
            if r["service"] == "brightdata":
                st = bd(f"progress/{r['id']}").get("status")
                if st == "ready":
                    node("brightdata_api_pipeline.mjs", "snapshot", "--platform", r["platform"], "--snapshot-id", r["id"], "--output", r["result"])
                elif st in ("failed", "canceled"):
                    print(f"{r['platform']} run {r['id']} {st}"); Path(r["result"]).write_text(json.dumps({"records": []}))
                else:
                    waiting.append(r["platform"])
            else:
                try:
                    node("apify_instagram.mjs", "--urls-file", str(run_dir / "ig-urls.json"), "--run-id", r["id"], "--output", r["result"])
                except subprocess.CalledProcessError as e:
                    if "RUNNING" in (e.stderr or "") or "READY" in (e.stderr or ""):
                        waiting.append(r["platform"])
                    else:
                        raise
        if waiting:
            print("still collecting:", waiting); sys.exit(2)
        files = [r["result"] for r in state["runs"]]
        pend.write_text("{}")  # cleared (this folder does not allow deleting files)
    print(py("merge_results.py", *files).strip())
    if os.environ.get("APIFY_FB_BACKUP", "on").lower() != "off":  # Facebook reels still without a view count
        out = subprocess.run([sys.executable, str(BUILD / "fb_views_backup.py"), "start"], capture_output=True, text=True)
        print(out.stdout.strip() or out.stderr.strip()[-300:])
        if out.returncode == 0 and "backup queued" in out.stdout and " in 0 runs" not in out.stdout:
            pend.write_text(json.dumps({"stage": "backup"}))
            print("Facebook view backup running; run finish again"); sys.exit(2)
    py("build_workbook.py")
    print(py("export_dashboard.py").strip())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start": start()
    elif cmd == "finish": finish()
    else:
        print({k: len(v) for k, v in due_today().items()})
