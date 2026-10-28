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


def read_rows():
    """Cost rows as text tables: from the Costs sheet when configured, else costs.csv, else []."""
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
        if not amt:
            continue  # rows without an amount are just placeholders
        out.append({"row": n, "source": get("source"), "account": get("account"), "month": get("month"),
                    "amount": amt, "basis": "per video" if "video" in get("basis").lower() else "per month"})
    return out


def allocate(master, rows=None):
    """Return ({master_id: rupees}, report, {master_id: payee}). report lists each cost row with the videos it matched;
    payee is the sheet's Account name the video's cost came from (the source name for whole-source rows)."""
    rows = read_rows() if rows is None else rows
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
            if not want:
                rank = 3  # whole source
            else:
                fields = [v.get("account"), v.get("channel"), v.get("influencer")]
                rank = next((k for k, f in enumerate(fields) if norm(f) and (want in norm(f) or norm(f) == want)), None)
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
