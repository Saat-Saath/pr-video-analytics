"""Sync the master list with the three source Google Sheets.

Usage: python sync_sheets.py [--dry-run]
Reads each sheet's public CSV export, rebuilds the occurrence list, keeps existing
VID-#### ids, adds new videos with the next ids, and flags videos no longer in any sheet.
Writes source_data.json (previous copy saved as source_data.prev.json) and sync_report.json.
"""
import csv, io, json, re, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "source_data.json"
def _env():
    """Sheet IDs live in .env (SHEET_KALPANA, SHEET_SANJAY, SHEET_INTERNAL_PR) so they never reach the public repo."""
    vals = {}
    p = ROOT.parent / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); vals[k.strip()] = v.strip()
    return vals


_E = _env()
SHEETS = {  # name: (spreadsheet id, tab gid)
    "kalpana": (_E.get("SHEET_KALPANA", ""), "0"),
    "sanjay": (_E.get("SHEET_SANJAY", ""), _E.get("SHEET_SANJAY_GID", "1765950165")),
    "internal_pr": (_E.get("SHEET_INTERNAL_PR", ""), ""),  # every tab is read; see internal_pr_tabs()
}
if not all(v[0] for v in SHEETS.values()):
    raise SystemExit("Add SHEET_KALPANA, SHEET_SANJAY and SHEET_INTERNAL_PR to .env (see .env.example)")
URL_RE = re.compile(r"https?://[^\s,;\"'<>]+", re.I)


def fetch(sid, gid):
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return list(csv.reader(io.StringIO(r.read().decode("utf8"))))


def sheet_tabs(sid):
    """[(tab name, gid)] of a link-shared spreadsheet, in the order the tabs appear."""
    with urllib.request.urlopen(f"https://docs.google.com/spreadsheets/d/{sid}/htmlview", timeout=60) as r:
        html = r.read().decode("utf8", "ignore")
    tabs = re.findall(r'\{name: "((?:[^"\\]|\\.)*)", pageUrl: "[^"]*", gid: "(\d+)"', html)
    return [(json.loads('"' + n + '"'), g) for n, g in tabs]


def internal_pr_tabs():
    """Influencer Daily posting Record: the "All links" overview tab (one column per influencer: name, platform,
    then links) and one tab per influencer (date, status, Drive file...). New influencer tabs are picked up by name."""
    sid = SHEETS["internal_pr"][0]
    try:
        tabs = sheet_tabs(sid)
    except Exception as e:  # fall back to the tabs known when this was written
        print("could not list Internal PR tabs:", e)
        tabs = [("All links", "1345412239"), ("Radhika Vlogs", "0"), ("Astha Gupta", "1107058259"), ("Jyoti", "1297562858"),
                ("Shivangi", "1151558042"), ("Renuka", "2052046322"), ("Vinita", "38318596")]
    overview = next(((n, g) for n, g in tabs if n.strip().lower() in ("all links", "all link", "links")), None)
    return sid, overview, [(n, g) for n, g in tabs if (n, g) != overview]


def col(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


ID_PARAMS = ("story_fbid", "fbid", "v", "id")


def key_of(url):
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u).replace("www.", "").replace("m.facebook.com", "facebook.com")
    path, _, query = u.split("#")[0].partition("?")
    path = path.rstrip("/")
    # Links like story.php?story_fbid=... or watch?v=... identify the video in the query string.
    if query and re.search(r"(story\.php|permalink\.php|video\.php|/watch|photo\.php)$", path):
        keep = [kv for kv in query.split("&") if kv.split("=")[0] in ID_PARAMS]
        if keep:
            return path + "?" + "&".join(keep)
    return path


def canonical(url):
    u = url.strip().rstrip(".,)")
    u = re.sub(r"^http://", "https://", u)
    if "drive.google" in u:
        return u
    m = re.match(r"(https://(?:www\.|m\.|web\.)?facebook\.com/share/(?:[rvp]/)?[\w-]+)", u)
    if m:  # text typed right after a share link ("…/1DH1gGJ57a/25-10-26") is not part of it
        return m.group(1) + "/"
    if "youtu" in u or "facebook.com/share" in u or "?" in u and re.search(r"(story|permalink|video|photo)\.php|/watch", u):
        return u.split("#")[0]
    return u.split("?")[0].split("#")[0]


def platform(url):
    u = url.lower()
    if "drive.google" in u: return "Google Drive Asset"
    if "instagram.com" in u: return "Instagram"
    if "facebook.com" in u or "fb.watch" in u: return "Facebook"
    if "youtu" in u: return "YouTube"
    return "Other"


def parse_date(text, fallback=""):
    """Parse the free-text dates people type ('15 October', '15 th Oct 2026', '14/10/26', '1. 13-10-2026').
    A date without a year takes the year of `fallback` (the form submission time); unreadable text uses `fallback`."""
    fb = ""
    if fallback:
        try:
            fb = datetime.strptime(fallback.split()[0], "%m/%d/%Y").date()
        except ValueError:
            fb = ""
    t = (text or "").strip().lower()
    t = re.sub(r"^\d+\.\s*", "", t)                 # "1. 13-10-2026"
    t = re.sub(r"(\d)\s*(st|nd|rd|th)\b", r"\1", t)    # "15 th October"
    t = re.sub(r"[,]", " ", t)
    t = re.sub(r"\s+", " ", t).strip().title()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%d %b", "%d %B"):
        try:
            d = datetime.strptime(t, fmt).date()
            return d.replace(year=fb.year if fb else datetime.now().year).isoformat()
        except ValueError:
            pass
    return fb.isoformat() if fb else ""


def occurrences():
    out = []
    # Kalpana: channel matrix. Row 1 = channel names (every 2 cols), row 2 = platform, rows 3+ = links.
    rows = fetch(*SHEETS["kalpana"])
    names, plats = rows[0], rows[1]
    for r, row in enumerate(rows[2:], start=3):
        for c, cell in enumerate(row):
            for u in URL_RE.findall(cell):
                ch = (names[c] or names[c - 1] if c else names[c]).strip()
                # Internal key stays "Kalpana - Disease Channels" so existing videos keep matching;
                # it is shown as "Inhouse ai videos" (DISPLAY_NAMES in export_dashboard.py / build_workbook.py).
                out.append(dict(source_group="Kalpana - Disease Channels", source_workbook="AMJ CHANNELS AI REELS - KALPANA",
                                source_tab="Sheet1", source_row=r, source_cell=f"{col(c)}{r}", source_date_text="", published_date="",
                                influencer="", channel=ch, profile_url="", declared_platform=(plats[c] if c < len(plats) else "").strip(),
                                posting_status="Published link", url=u))
    # Sanjay: Google Form responses. Column D may hold several links.
    rows = fetch(*SHEETS["sanjay"])
    for r, row in enumerate(rows[1:], start=2):
        row = row + [""] * 6
        for i, u in enumerate(URL_RE.findall(row[3]), start=1):
            out.append(dict(source_group="Sanjay Agency", source_workbook="video upload link (Responses)", source_tab="Form Responses 1",
                            source_row=r, source_cell=f"D{r}", source_date_text=row[4].strip(), published_date=parse_date(row[4], row[0]),
                            influencer=row[1].strip(), channel=row[2].strip(), profile_url="", declared_platform="",
                            posting_status="Published link", url=u, cell_url_sequence=i, form_timestamp=row[0].strip()))
    # Internal PR. "All links" lists every influencer's posts (row 1 = influencer, row 2 = platform, rows 3+ = links);
    # its column header is the influencer's name. Each influencer tab adds the date, status and Drive file of a post.
    sid, overview, tabs = internal_pr_tabs()
    who = {}  # post key -> (influencer, platform, cell) from the All links tab
    if overview:
        rows = fetch(sid, overview[1])
        names = rows[0] if rows else []
        plats = rows[1] if len(rows) > 1 else []
        for r, row in enumerate(rows[2:], start=3):
            for c, cell in enumerate(row):
                for u in URL_RE.findall(cell):
                    if "drive.google" in u.lower():
                        continue
                    who.setdefault(key_of(canonical(u)), ((names[c] if c < len(names) else "").strip(),
                                                           (plats[c] if c < len(plats) else "").strip(), f"{col(c)}{r}"))
    seen = set()
    for tab, gid in tabs:
        rows = fetch(sid, gid)
        if not rows:
            continue
        head = [h.strip().lower() for h in rows[0]]
        find = lambda *names: next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
        c_date, c_name, c_prof, c_plat, c_grp, c_stat = find("date"), find("influencer"), find("profile"), find("platform"), find("telegram"), find("posting status")
        if c_plat is not None and "link" in head[c_plat]:
            c_plat = next((i for i, h in enumerate(head) if h == "platform"), None)
        for r, row in enumerate(rows[1:], start=2):
            row = row + [""] * (len(head) + 2)
            get = lambda c: row[c].strip() if c is not None else ""
            post = drive = None
            for c, cell in enumerate(row):
                if c == c_prof:
                    continue
                for u in URL_RE.findall(cell):
                    if "drive.google" in u.lower():
                        drive = drive or (u, c)
                    else:
                        post = post or (u, c)
            pick = post or drive
            if not pick:
                continue
            k = key_of(canonical(pick[0]))
            name, plat, _ = who.get(k, ("", "", ""))
            name = name or tab.strip() or get(c_name)  # the All links header, else the tab name (the Name column can be a copy-paste)
            seen.add(k)
            out.append(dict(source_group="Internal PR", source_workbook="Influencer Daily posting Record", source_tab=tab,
                            source_row=r, source_cell=f"{col(pick[1])}{r}", source_date_text=get(c_date), published_date=parse_date(get(c_date)),
                            influencer=name, channel=name, profile_url="", profile_label=get(c_prof),
                            declared_platform=plat or get(c_plat), telegram_group=get(c_grp), posting_status=get(c_stat) or ("Posted" if post else "Unknown"),
                            url=pick[0], drive_url=drive[0] if drive else ""))
    # a post that is only in All links (no row in an influencer tab yet)
    if overview:
        rows = fetch(sid, overview[1])
        for r, row in enumerate(rows[2:], start=3):
            for c, cell in enumerate(row):
                for u in URL_RE.findall(cell):
                    k = key_of(canonical(u))
                    if "drive.google" in u.lower() or k in seen:
                        continue
                    seen.add(k)
                    name, plat, cellref = who[k]
                    out.append(dict(source_group="Internal PR", source_workbook="Influencer Daily posting Record", source_tab=overview[0],
                                    source_row=r, source_cell=cellref, source_date_text="", published_date="",
                                    influencer=name, channel=name, profile_url="", profile_label="", declared_platform=plat,
                                    telegram_group="", posting_status="Posted", url=u, drive_url=""))
    for o in out:
        o["canonical_url"] = canonical(o["url"])
        o["key"] = key_of(o["canonical_url"])
        o["detected_platform"] = platform(o["canonical_url"])
    return out


def main(dry):
    data = json.loads(SRC.read_text(encoding="utf8"))
    for m in data["master"]:  # re-key existing videos with the current matching rule
        m["canonical_key"] = key_of(m["canonical_url"])
    old_master = {m["canonical_key"]: m for m in data["master"]}
    old_occ_keys = Counter(o["key"] for o in data["occurrences"])
    occ = occurrences()
    by_key = defaultdict(list)
    for o in occ:
        by_key[o["key"]].append(o)

    new_keys = [k for k in by_key if k not in old_master]
    gone_keys = [k for k in old_master if k not in by_key]
    # A link replaced in the same sheet cell (e.g. a Drive file swapped for the live post link) keeps its video id.
    cell_of_new = {}
    for k in new_keys:
        for o in by_key[k]:
            cell_of_new[f"{o['source_tab']}!{o['source_row']}"] = k
    replaced = []
    for k in list(gone_keys):
        m = old_master[k]
        cells = [c.strip() for c in (m.get("source_rows") or "").split(",") if c.strip()]
        nk = cell_of_new.get(cells[0]) if len(cells) == 1 else None
        if nk and nk in new_keys:
            o = by_key[nk][0]
            replaced.append({"id": m["master_id"], "cell": cells[0], "old": m["canonical_url"], "new": o["canonical_url"]})
            m.update(canonical_key=nk, canonical_url=o["canonical_url"], original_url=o["url"], platform=o["detected_platform"],
                     content_type="Drive asset" if o["detected_platform"] == "Google Drive Asset" else "Public post",
                     analytics_readiness="Awaiting public URL" if o["detected_platform"] == "Google Drive Asset" else "Ready for analytics",
                     link_replaced_on=datetime.now(timezone.utc).date().isoformat(), previous_url=m["canonical_url"])
            old_master.pop(k); old_master[nk] = m
            gone_keys.remove(k); new_keys.remove(nk)
    next_id = max(int(m["master_id"].split("-")[1]) for m in data["master"]) + 1
    master = []
    for m in data["master"]:
        k = m["canonical_key"]
        if k in by_key:
            os_ = by_key[k]
            m["source_rows"] = ", ".join(f"{o['source_tab']}!{o['source_row']}" for o in os_)
            m["occurrence_count"] = len(os_)
            m["sheet_status"] = "In sheet"
            dates = [o["published_date"] for o in os_ if o["published_date"]]
            if dates and not m.get("published_date"):
                m["published_date"] = min(dates)
            m["posting_status"] = os_[0]["posting_status"]
        else:
            m["sheet_status"] = "Removed from sheet"
        master.append(m)
    added = []
    for k in new_keys:
        os_ = by_key[k]; o = os_[0]
        drive = o["detected_platform"] == "Google Drive Asset"
        m = dict(master_id=f"VID-{next_id:04d}", content_type="Drive asset" if drive else "Public post",
                 platform=o["detected_platform"], canonical_key=k, canonical_url=o["canonical_url"], original_url=o["url"],
                 source_group=o["source_group"], source_workbook=o["source_workbook"], source_tab=o["source_tab"],
                 source_rows=", ".join(f"{x['source_tab']}!{x['source_row']}" for x in os_), influencer=o.get("influencer", ""),
                 channel=o.get("channel", ""), profile_url="", published_date=min([x["published_date"] for x in os_ if x["published_date"]] or [""]),
                 source_date_text=o.get("source_date_text", ""), analytics_readiness="Awaiting public URL" if drive else "Ready for analytics",
                 source_status=o["posting_status"], posting_status=o["posting_status"], occurrence_count=len(os_),
                 fetch_status="Not fetched", sheet_status="In sheet", added_on=datetime.now(timezone.utc).date().isoformat(), notes="")
        next_id += 1
        master.append(m); added.append(m)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sheet_rows_with_links": len(occ), "previous_rows_with_links": sum(old_occ_keys.values()),
        "unique_videos_in_sheets": len(by_key), "previous_master": len(old_master),
        "new_videos": [{"id": m["master_id"], "source": m["source_group"], "row": m["source_rows"], "url": m["canonical_url"], "date": m["published_date"]} for m in added],
        "links_replaced": replaced,
        "removed_from_sheets": [{"id": old_master[k]["master_id"], "url": old_master[k]["canonical_url"], "was": old_master[k]["source_rows"]} for k in gone_keys],
        "by_source_now": Counter(o["source_group"] for o in occ), "by_source_before": Counter(o["source_group"] for o in data["occurrences"]),
        "duplicates_in_sheets": {k: [f"{o['source_tab']}!{o['source_row']}" for o in v] for k, v in by_key.items() if len(v) > 1},
        "missing_dates": sum(1 for k, v in by_key.items() if not any(o["published_date"] for o in v) and v[0]["source_group"] != "Kalpana - Disease Channels"),
        "internal_pr_post_links": sum(1 for o in occ if o["source_group"] == "Internal PR" and o["detected_platform"] != "Google Drive Asset"),
    }
    (ROOT / "sync_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf8")
    if not dry:
        (ROOT / "source_data.prev.json").write_text(SRC.read_text(encoding="utf8"), encoding="utf8")
        data.update(generated_at=report["checked_at"], occurrences=occ, master=master)
        SRC.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf8")
    summary = {k: (len(v) if isinstance(v, (list, dict)) and k not in ("by_source_now", "by_source_before") else v) for k, v in report.items()}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
