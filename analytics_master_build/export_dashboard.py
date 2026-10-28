"""Write the public dashboard (docs/) from analytics_data.json.
Usage: python analytics_master_build/export_dashboard.py
docs/data.json  - one row per video (public post links and public counts only)
docs/index.html - standalone page: dashboard_page.html + a small loader, hostable on GitHub Pages
"""
import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT.parent / "docs"
SRC = {"Kalpana - Disease Channels": "Inhouse ai videos", "Inhouse ai videos": "Inhouse ai videos",  # display names
       "Sanjay Agency": "Sanjay Agency", "Internal PR": "Internal PR"}
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
                    "title": (m.get("caption") or "").strip().split("\n")[0][:90] or None,
                    "published_at": m.get("published_at") or None,
                    "post_url": m.get("post_url") or None,
                    "alltime_at": (m.get("alltime_at") or "")[:10] or None,
                    "alltime_age": m.get("alltime_age"),
                    "thumb": save_thumb(m),
                    **{f"{l}_{k}": m.get(f"{l}_{k}") for l in ("d2", "d7", "d30")
                       for k in ("views", "likes", "comments", "shares", "status", "due", "age")}})
    return out, d["generated_at"]


def save_thumb(m):
    """Keep a small copy of each video's thumbnail in docs/thumbs/ (platform thumbnail links expire after a few days).
    Returns its path relative to docs/, or None. Needs Pillow; without it thumbnails are skipped."""
    vid, src = m["master_id"], m.get("thumb_url")
    out = DOCS / "thumbs" / f"{vid}.jpg"
    if out.exists():
        return f"thumbs/{vid}.jpg"
    # Download at most ~90 s per run and never retry a link that already failed; the rest come next run.
    if time.time() > _DEADLINE:
        return None
    if not src:  # no thumbnail from the scraper: use the post's public preview image (og:image)
        page = m.get("post_url") or m.get("canonical_url") or ""
        if not page or page in _FAILED or "drive.google" in page:
            return None
        src = og_image(page)
        if not src:
            _FAILED.add(page)
            return None
    if src in _FAILED:
        return None
    try:
        import io, urllib.request
        from PIL import Image
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGB")
        img.thumbnail((320, 320))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=72, optimize=True)
        return f"thumbs/{vid}.jpg"
    except Exception:
        _FAILED.add(src)
        return None


def og_image(url):
    """The preview image a public post shows when its link is shared (og:image), or None."""
    import html, re, urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facebookexternalhit/1.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read(600_000).decode("utf8", "ignore")
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', text)
        return html.unescape(m.group(1)) if m else None
    except Exception:
        return None


_DEADLINE = time.time() + 90
_FAILED_FILE = ROOT / "thumb_failed.json"
_FAILED = set(json.loads(_FAILED_FILE.read_text())) if _FAILED_FILE.exists() else set()


SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PR video analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--font-anthropic-sans:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;--font-anthropic-serif:Georgia,serif;--cds-dur-slow:.3s;color-scheme:light}
body{margin:0;background:#F3F4FA;color:#14163A;font-family:Inter,system-ui,sans-serif}
#dash-root{padding-left:env(safe-area-inset-left,0px);padding-right:env(safe-area-inset-right,0px)}
.pv-foot{max-width:1360px;margin:0 auto;padding:0 24px 32px;font-size:.8125rem;color:#5F6480}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
window.PV_EMBED = true; // this standalone page may embed players and remote thumbnails; the Claude dashboard cannot
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
</div>
<p class="pv-foot">Data updated __UPDATED__. Public counts as shown on each platform; Instagram via Apify, Facebook and YouTube via Bright Data.</p>
</body></html>
"""


def main():
    data, generated = rows()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    page = (ROOT / "dashboard_page.html").read_text(encoding="utf8")
    updated = datetime.fromisoformat(generated).strftime("%d %b %Y")
    (DOCS / "index.html").write_text(SHELL.replace("__PAGE__", page).replace("__UPDATED__", updated), encoding="utf8")
    (DOCS / ".nojekyll").write_text("")
    _FAILED_FILE.write_text(json.dumps(sorted(_FAILED)))
    print(f"docs/ written: {len(data)} videos, updated {updated}")


if __name__ == "__main__":
    main()
