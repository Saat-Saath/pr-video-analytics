"""What each video cost, from the "PR Video Costs" sheet.

One row per payment:  Source | Account | Month | Amount (₹) | Basis
  Source   Inhouse ai videos, Sanjay Agency or Internal PR
  Account  channel / influencer name; blank = every video of that source
  Month    e.g. Oct 2026 (videos posted that month); blank = all months
  Amount   rupees
  Basis    "per month" (amount is shared equally by the matching videos posted that month) or "per video"

The sheet is read from SHEET_COSTS (+ SHEET_COSTS_GID) in .env, or from analytics_master_build/costs.csv.
A video's cost is the sum of every row that matches it. When more than one row of a source matches a video,
only the rows that match its account name win over rows that match its channel, and those over its influencer name.
"""
import csv
import io
import json
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = {"inhouseaivideos": "Kalpana - Disease Channels", "inhouse": "Kalpana - Disease Channels",
           "kalpana": "Kalpana - Disease Channels", "kalpanadiseasechannels": "Kalpana - Disease Channels",
           "sanjayagency": "Sanjay Agency", "sanjay": "Sanjay Agency", "internalpr": "Internal PR"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def env():
    vals = {}
    p = ROOT.parent / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    import os
    vals.update({k: v for k, v in os.environ.items() if k.startswith(("SHEET_", "COSTS_"))})
    return vals


def norm(s):
    """Lower case, letters and digits only (Hindi letters kept)."""
    return re.sub(r"[\W_]+", "", (s or "").lower())


def month_key(text):
    """'Oct 2026', 'October 2026', '2026-10', '10/2026', '10-2026' -> '2026-10'; blank -> None."""
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", t)  # a full date (Sheets turns "Oct 2026" into 1 Oct)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        mon = b if a == 1 and b != 1 else a if b == 1 else b if a > 12 else a
        return f"{m.group(3)}-{mon:02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{4})$", t)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.match(r"^([a-z]{3})[a-z]*[\s,'-]*(\d{2,4})$", t)
    if m and m.group(1) in MONTHS:
        y = int(m.group(2))
        return f"{y + 2000 if y < 100 else y}-{MONTHS[m.group(1)]:02d}"
    raise ValueError(f"month not understood: {text!r}")


def amount(text):
    t = re.sub(r"[^\d.]", "", (text or "").replace(",", ""))
    return float(t) if t else None


def read_rows(include_blank=False):
    """Cost rows from the Costs sheet when configured, else costs.csv, else []. Rows without an amount are left out
    unless include_blank (they still count as "this account already has a row" for missing_rows)."""
    e = env()
    sid = e.get("SHEET_COSTS", "")
    if sid:
        url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={e.get('SHEET_COSTS_GID', '0')}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                text = r.read().decode("utf8")
        except urllib.error.HTTPError as err:
            raise SystemExit(f"Costs sheet could not be read (HTTP {err.code}): set it to 'Anyone with the link - Viewer'")
        if text.lstrip().lower().startswith("<!doctype html"):
            raise SystemExit("Costs sheet is not shared: set it to 'Anyone with the link - Viewer'")
        rows = list(csv.reader(io.StringIO(text)))
    elif (ROOT / "costs.csv").exists():
        rows = list(csv.reader(io.StringIO((ROOT / "costs.csv").read_text(encoding="utf-8-sig"))))
    else:
        return []
    if not rows:
        return []
    head = [norm(h) for h in rows[0]]

    def find(*names):
        for i, h in enumerate(head):
            if any(h.startswith(n) for n in names):
                return i
        return None
    ix = dict(source=find("source"), account=find("account", "channel", "influencer", "name"), month=find("month"),
              amount=find("amount", "cost", "payment", "rs", "inr"), basis=find("basis", "type", "per"), note=find("note"))
    if ix["source"] is None or ix["amount"] is None:
        raise SystemExit("Costs sheet needs at least a Source and an Amount column")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        get = lambda k: (r[ix[k]].strip() if ix[k] is not None and ix[k] < len(r) else "")
        amt = amount(get("amount"))
        if not amt and not (include_blank and get("source")):
            continue  # rows without an amount are just placeholders
        out.append({"row": n, "source": get("source"), "account": get("account"), "month": get("month"),
                    "amount": amt, "basis": "per video" if "video" in get("basis").lower() else "per month"})
    return out


def match_rank(want, v):
    """How a cost row's (normalised) Account name matches a video: 0 its account, 1 its channel, 2 its influencer
    name, 3 a blank Account (the whole source); None when it does not match."""
    if not want:
        return 3
    fields = [v.get("account"), v.get("channel"), v.get("influencer")]
    return next((k for k, f in enumerate(fields) if norm(f) and want in norm(f)), None)


DEFAULT_BASIS = {"Kalpana - Disease Channels": "per video", "Sanjay Agency": "per month", "Internal PR": "per month"}
DISPLAY = {"Kalpana - Disease Channels": "Inhouse ai videos"}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def missing_rows(master, rows):
    """Rows the Costs sheet still needs: one per source / account / month whose videos no sheet row covers
    (a row covers a video when its source and account match and its month is blank or the video's month).
    New accounts and new months show up here; reuse the name an earlier sheet row already uses for that account."""
    known = []  # (source, normalised account, account as typed, month key)
    for c in rows:
        src = SOURCES.get(norm(c["source"]))
        if not src:
            continue
        try:
            mk = month_key(c["month"])
        except ValueError:
            mk = "?"
        known.append((src, norm(c["account"]), c["account"].strip(), mk))
    need = {}
    for v in master:
        src, vm = v["source_group"], (v.get("published_date") or "")[:7]
        if not vm:
            continue
        ranks = [(match_rank(w, v), name, mk) for s_, w, name, mk in known if s_ == src]
        ranks = [r for r in ranks if r[0] is not None]
        if any(mk in (None, vm) for _, _, mk in ranks):
            continue  # covered
        named = sorted((r for r in ranks if r[0] < 3), key=lambda r: r[0])
        if src == "Kalpana - Disease Channels":
            label, month = "", ""  # one per-video rate for all inhouse videos
        else:
            label = named[0][1] if named else (v.get("influencer") if src == "Internal PR" else None) or v.get("account") or v.get("channel") or v.get("influencer") or ""
            y, m = vm.split("-")
            month = f"{MON[int(m) - 1]} {y}"
        k = (src, norm(label), month)
        if k not in need:
            need[k] = {"Source": DISPLAY.get(src, src), "Account": label.strip(), "Month": month,
                       "Basis": DEFAULT_BASIS.get(src, "per month"), "Videos": 0}
        need[k]["Videos"] += 1
    return sorted(need.values(), key=lambda r: (r["Month"], r["Source"], r["Account"].lower()))


def clean_url(u):
    u = (u or "").strip().split("#")[0].replace("://web.facebook.com", "://www.facebook.com").replace("://m.facebook.com", "://www.facebook.com")
    if "profile.php?id=" in u:
        return u.split("&")[0]
    return u.split("?")[0].rstrip("/") + "/" if u else ""


def profile_links(data):
    """{master_id: the public profile / page link of the account that posted it}."""
    page = {}
    for snap in data.get("snapshots", []):
        if snap.get("page_url"):
            page[snap["master_id"]] = snap["page_url"]
    out = {}
    for v in data["master"]:
        acc = (v.get("account") or "").strip()
        u = page.get(v["master_id"]) or v.get("profile_url") or ""
        if not u and v["platform"] == "Instagram" and re.fullmatch(r"[\w.]+", acc):
            u = f"https://www.instagram.com/{acc}/"
        if not u and v["platform"] == "YouTube" and acc.startswith("@"):
            u = f"https://www.youtube.com/{acc}"
        if u.startswith("http"):
            out[v["master_id"]] = clean_url(u)
    return out


def account_links(data, rows, missing=()):
    """One line per Source + Account name used in the Costs sheet (and each new one in `missing`): the profile links
    of the accounts its row covers, so the sheet shows whose payment it is."""
    links = profile_links(data)
    names = {}
    for c in list(rows) + [{"source": m["Source"], "account": m["Account"]} for m in missing]:
        src = SOURCES.get(norm(c["source"]))
        if src:
            names.setdefault((src, norm(c["account"])), (c["source"].strip(), c["account"].strip()))
    by_src = defaultdict(list)
    for (src, want) in names:
        by_src[src].append(want)
    found = defaultdict(set)
    posts = defaultdict(set)  # when no profile link is known, show one of the account's posts instead
    for v in data["master"]:
        u = links.get(v["master_id"])
        ranked = [(match_rank(w, v), w) for w in by_src[v["source_group"]]]
        ranked = [r for r in ranked if r[0] is not None]
        if not ranked:
            continue
        best = min(r for r, _ in ranked if r < 3) if any(r < 3 for r, _ in ranked) else None
        for r, w in ranked:
            if r == 3 or r == best:
                if u:
                    found[(v["source_group"], w)].add(u)
                elif (v.get("post_url") or v.get("canonical_url") or "").startswith("http"):
                    posts[(v["source_group"], w)].add(v.get("post_url") or v["canonical_url"])
    out = []
    for k in sorted(names, key=lambda k: names[k]):
        if found.get(k):
            out.append({"Source": names[k][0], "Account": names[k][1], "Links": "\n".join(sorted(found[k]))})
        elif posts.get(k):
            out.append({"Source": names[k][0], "Account": names[k][1], "Links": "Post: " + sorted(posts[k])[0]})
    return out


def allocate(master, rows=None):
    """Return ({master_id: rupees}, report, {master_id: payee}). report lists each cost row with the videos it matched;
    payee is the sheet's Account name the video's cost came from (the source name for whole-source rows)."""
    rows = read_rows() if rows is None else [c for c in rows if c.get("amount")]
    cost = defaultdict(float)
    payee = defaultdict(list)
    report = []
    by_source = defaultdict(list)
    for v in master:
        by_source[v["source_group"]].append(v)
    # which rows match which videos (account > channel > influencer precedence within a source)
    matches = defaultdict(list)  # row index -> videos
    best = {}  # master_id -> (rank, [row idx])
    parsed = []
    for i, c in enumerate(rows):
        src = SOURCES.get(norm(c["source"]))
        try:
            mk = month_key(c["month"])
        except ValueError as e:
            report.append({**c, "videos": 0, "problem": str(e)})
            parsed.append(None)
            continue
        if not src:
            report.append({**c, "videos": 0, "problem": "unknown source"})
            parsed.append(None)
            continue
        parsed.append((src, mk))
        want = norm(c["account"])
        for v in by_source[src]:
            if mk and (v.get("published_date") or "")[:7] != mk:
                continue
            rank = match_rank(want, v)
            if rank is None:
                continue
            matches[i].append((v, rank))
    # keep only the best-ranked named rows per video; whole-source rows always apply
    for i, vs in matches.items():
        for v, rank in vs:
            if rank == 3:
                continue
            b = best.get(v["master_id"])
            if b is None or rank < b[0]:
                best[v["master_id"]] = (rank, [i])
            elif rank == b[0]:
                b[1].append(i)
    for i, c in enumerate(rows):
        if parsed[i] is None:
            continue
        vids = [v for v, rank in matches.get(i, []) if rank == 3 or i in best.get(v["master_id"], (0, []))[1]]
        if vids:
            share = c["amount"] / len(vids) if c["basis"] == "per month" else c["amount"]
            label = c["account"].strip() or (c["source"].strip() + " (all)")
            for v in vids:
                cost[v["master_id"]] += share
                if label not in payee[v["master_id"]]:
                    payee[v["master_id"]].append(label)
        report.append({**c, "videos": len(vids), "problem": "" if vids else "no videos match this account and month"})
    return {k: round(v, 2) for k, v in cost.items()}, report, {k: " + ".join(v) for k, v in payee.items()}


if __name__ == "__main__":
    data = json.loads((ROOT / "analytics_data.json").read_text(encoding="utf8"))
    costs, rep, _ = allocate(data["master"])
    for r in rep:
        print(f"row {r['row']}: {r['source']} / {r['account'] or '(all)'} / {r['month'] or '(all months)'} ₹{r['amount']:,.0f} "
              f"{r['basis']} -> {r['videos']} videos {r['problem']}")
    spent = sum(costs.values())
    views = sum((m.get("latest_views") or 0) for m in data["master"] if m["master_id"] in costs)
    print(f"{len(costs)} videos have a cost, ₹{spent:,.0f} in all, ₹{spent / views * 1000 if views else 0:,.2f} per 1,000 views")
