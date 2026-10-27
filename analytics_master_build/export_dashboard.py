"""Write the public dashboard (docs/) from analytics_data.json.
Usage: python analytics_master_build/export_dashboard.py
docs/data.json  - one row per video (public post links and public counts only)
docs/index.html - standalone page: dashboard_page.html + a small loader, hostable on GitHub Pages
"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT.parent / "docs"
SRC = {"Kalpana - Disease Channels": "Kalpana channels", "Sanjay Agency": "Sanjay Agency", "Internal PR": "Internal PR"}
PLAT = {"Google Drive Asset": "Drive file"}


def rows():
    d = json.loads((ROOT / "analytics_data.json").read_text(encoding="utf8"))
    out = []
    for m in d["master"]:
        out.append({"video": m["master_id"], "platform": PLAT.get(m["platform"], m["platform"]),
                    "source": SRC.get(m["source_group"], m["source_group"]),
                    "account": m.get("account") or m.get("channel") or m.get("influencer") or "",
                    "url": m["canonical_url"], "published": (m.get("published_date") or "")[:10] or None,
                    "views": m.get("latest_views"), "likes": m.get("latest_likes"), "comments": m.get("latest_comments"),
                    "shares": m.get("latest_shares"), "status": m.get("fetch_status"), "fetched_by": m.get("data_source") or None,
                    **{f"{l}_{k}": m.get(f"{l}_{k}") for l in ("d2", "d7", "d30") for k in ("views", "likes", "status", "due", "age")}})
    return out, d["generated_at"]


SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PR video analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400&display=swap" rel="stylesheet">
<style>
:root{--font-anthropic-sans:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;--font-anthropic-serif:"Source Serif 4",Georgia,serif;
--color-bg:#f7f7f5;--color-panel:#ffffff;--color-fg:#1f1f1d;--color-fg-muted:#6b6a65;--color-border-line:#e4e2dc;
--color-ok:#2f7d32;--color-warn:#9a6200;--color-bad:#b3261e;--cds-chart-grid:#ecebe6;--cds-surface-popover:#ffffff;--cds-border:#dddbd4;
--cds-shadow-popover:0 8px 24px rgba(0,0,0,.12),0 2px 6px rgba(0,0,0,.08);--cds-text-secondary:#6b6a65;
--cds-font-size-caption:.8125rem;--cds-font-size-body:.9375rem;--cds-font-size-heading:1rem;--cds-font-size-title:1.75rem;--cds-font-weight-medium:500;
--cds-gap-xs:.25rem;--cds-gap-sm:.5rem;--cds-gap-md:.75rem;--cds-gap-lg:1.5rem;--cds-pad-xs:.25rem;--cds-pad-sm:.5rem;--cds-pad-md:.75rem;--cds-pad-lg:1rem;
--cds-radius:10px;--cds-dur-slow:.3s;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--color-bg:#171716;--color-panel:#1f1f1e;--color-fg:#ecebe6;--color-fg-muted:#a3a19a;--color-border-line:#33322f;
--color-ok:#7cc47f;--color-warn:#e0a84a;--color-bad:#f08a80;--cds-chart-grid:#2c2b29;--cds-surface-popover:#262624;--cds-border:#3a3936;--cds-text-secondary:#a3a19a;color-scheme:dark}}
body{margin:0;background:var(--color-bg);color:var(--color-fg);font-family:var(--font-anthropic-sans)}
#dash-root{max-width:1200px;margin:0 auto;padding-block:2rem 3rem;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.dash-skeleton{color:transparent!important;background:var(--color-border-line);border-radius:6px;animation:pvsk 1.2s ease-in-out infinite alternate}
@keyframes pvsk{from{opacity:.5}to{opacity:1}}
.pv-foot{margin-top:2rem;font-size:.8125rem;color:var(--color-fg-muted)}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
window.dash = (function () {
  const sets = { videos: { status: 'loading', data: [] } }, calcs = {}, subs = [];
  const api = {
    colors: ['#3266ad', '#c2185b', '#2e7d6b', '#b26a00', '#6a4c93', '#5f6b7a', '#9c3d2b', '#3d7a3d'],
    calc(id, o) { calcs[id] = o; },
    data(id) {
      if (calcs[id]) {
        const ins = calcs[id].inputs.map(api.data);
        const bad = ins.find(x => x.status !== 'ok'); if (bad) return { status: bad.status, data: [] };
        if (!calcs[id].cache) calcs[id].cache = calcs[id].fn(...ins.map(x => x.data));
        return { status: 'ok', data: calcs[id].cache };
      }
      return sets[id] || { status: 'missing', data: [] };
    },
    onData(fn) { subs.push(fn); fn(); },
    setLink(n) { try { history.replaceState(null, '', '#' + n); } catch (e) {} },
    refresh() {}
  };
  const run = () => subs.forEach(f => f());
  fetch('data.json', { cache: 'no-cache' }).then(r => r.json())
    .then(rows => { sets.videos = { status: 'ok', data: rows }; run(); })
    .catch(() => { sets.videos = { status: 'error', data: [] }; run(); });
  let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(run, 150); });
  return api;
})();
</script>
</head><body><div id="dash-root">
__PAGE__
<p class="pv-foot">Data updated __UPDATED__. Public counts as shown on each platform; Instagram via Apify, Facebook and YouTube via Bright Data.</p>
</div></body></html>
"""


def main():
    data, generated = rows()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    page = (ROOT / "dashboard_page.html").read_text(encoding="utf8")
    updated = datetime.fromisoformat(generated).strftime("%d %b %Y")
    (DOCS / "index.html").write_text(SHELL.replace("__PAGE__", page).replace("__UPDATED__", updated), encoding="utf8")
    (DOCS / ".nojekyll").write_text("")
    print(f"docs/ written: {len(data)} videos, updated {updated}")


if __name__ == "__main__":
    main()
