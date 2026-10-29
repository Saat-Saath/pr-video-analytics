"""Which videos are the same content, and what each video is about.

same content: two videos whose thumbnails look the same (difference hash of docs/thumbs/<id>.jpg within 10 of
256 bits) or whose captions start with the same words. Linked videos get one content id ("C001"), so the dashboard
can compare channels on identical videos.

topic: the first topic whose words appear in a video's caption (Hindi or English); a video without a caption
takes the topic of the other videos with the same content.
"""
import re
from pathlib import Path

THUMBS = Path(__file__).resolve().parent.parent / "docs" / "thumbs"

TOPICS = [  # (topic, words) — checked in this order, so "chemo ... periods" counts as Cancer
    ("Cancer", ["cancer", "कैंसर", "कीमो", "chemo", "tumor", "ट्यूमर", "स्तन में गांठ", "breast", "रेडिएशन"]),
    ("Kidney", ["kidney", "किडनी", "dialysis", "डायलिसिस", "creatinine", "क्रिएटिनिन"]),
    ("Liver", ["liver", "लिवर", "लीवर", "पीलिया", "jaundice", "हेपेटाइटिस"]),
    ("Paralysis & nerves", ["पक्षाघात", "लकवा", "paralysis", "stroke", "नस", "neuro"]),
    ("BP & heart", ["blood pressure", "ब्लड प्रेशर", "बीपी", " bp", "bp ", "heart", "हृदय", "दिल", "हार्ट", "cholesterol", "कोलेस्ट्रॉल"]),
    ("Diabetes", ["diabetes", "डायबिटीज", "शुगर", "sugar", "मधुमेह", "insulin", "इंसुलिन"]),
    ("Joints & arthritis", ["घुटन", "गठिया", "joint", "arthritis", "गांठ", "knee", "जोड़", "यूरिक", "uric", "कमर दर्द", "back pain"]),
    ("Stones", ["पथरी", "stone", "पित्ताशय", "gallbladder"]),
    ("Stomach & digestion", ["पेट", "कब्ज", "constipation", "गैस", "digestion", "पाचन", "एसिडिटी", "acidity", "बवासीर", "piles"]),
    ("Breathing & asthma", ["asthma", "अस्थमा", "सांस", "खांसी", "lung", "फेफड़", "टीबी"]),
    ("Weight", ["weight", "वजन", "fat loss", "मोटापा", "obesity"]),
    ("Women's health", ["पीरियड", "period", "pcod", "pcos", "ल्यूकोरिया", "leucorrhoea", "बच्चेदानी", "uterus", "गर्भाशय"]),
    ("Thyroid", ["thyroid", "थायराइड"]),
    ("Skin", ["skin", "त्वचा", "psoriasis", "सोरायसिस", "eczema", "एक्जिमा", "सफेद दाग"]),
]


def topic_of(text):
    t = " " + (text or "").lower() + " "
    for name, words in TOPICS:
        if any(w in t for w in words):
            return name
    return None


def norm_title(caption):
    t = (caption or "").split("\n")[0].lower()
    t = re.sub(r"#\S+|https?://\S+", "", t)
    t = re.sub(r"[^\wऀ-ॿ]+", " ", t)
    t = " ".join(t.split())[:45]
    return t if len(t) >= 12 else None


def dhash(path, n=16):
    from PIL import Image
    im = Image.open(path).convert("L").resize((n + 1, n), Image.LANCZOS)
    px = list(im.tobytes())
    bits = 0
    for r in range(n):
        for c in range(n):
            bits = (bits << 1) | (px[r * (n + 1) + c] > px[r * (n + 1) + c + 1])
    return bits


def analyse(master, texts=None):
    """{master_id: {"content": "C001" or None, "topic": str or None}} for the given master rows. texts: words read
    from each video's thumbnail (thumb_texts), used for the topic when the caption does not name one."""
    texts = texts or {}
    ids = [m["master_id"] for m in master]
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_title = {}
    for m in master:
        k = norm_title(m.get("caption"))
        if k:
            if k in by_title:
                union(by_title[k], m["master_id"])
            else:
                by_title[k] = m["master_id"]
    hashes = {}
    for i in ids:
        p = THUMBS / f"{i}.jpg"
        if p.exists():
            try:
                hashes[i] = dhash(p)
            except Exception:
                pass
    hs = list(hashes.items())
    for x in range(len(hs)):
        a, ha = hs[x]
        for y in range(x + 1, len(hs)):
            b, hb = hs[y]
            if bin(ha ^ hb).count("1") <= 10:
                union(a, b)
    groups = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    by_id = {m["master_id"]: m for m in master}
    out = {i: {"content": None, "topic": topic_of(by_id[i].get("caption")) or topic_of(texts.get(i))} for i in ids}
    n = 0
    for root, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        n += 1
        cid = f"C{n:03d}"
        topics = [out[i]["topic"] for i in members if out[i]["topic"]]
        common = max(set(topics), key=topics.count) if topics else None
        for i in members:
            out[i]["content"] = cid
            out[i]["topic"] = out[i]["topic"] or common
    return out


TEXT_CACHE = Path(__file__).resolve().parent / "thumb_text.json"


def _ocr(args):
    path, tessdata = args
    import subprocess, tempfile, os
    from PIL import Image
    try:
        im = Image.open(path).convert("L")
        im = im.resize((im.width * 4, im.height * 4), Image.LANCZOS)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            im.save(f.name)
            cmd = ["tesseract", f.name, "-", "-l", "hin+eng", "--psm", "11"] + (["--tessdata-dir", tessdata] if tessdata else [])
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        os.unlink(f.name)
        return " ".join(out.split())
    except Exception:
        return ""


def thumb_texts(ids, tessdata=None):
    """{video id: text read from its thumbnail}; read once per video and kept in thumb_text.json (needs tesseract
    with Hindi; without it nothing new is read and the cached texts are used)."""
    import json, shutil
    from concurrent.futures import ThreadPoolExecutor
    cache = json.loads(TEXT_CACHE.read_text(encoding="utf8")) if TEXT_CACHE.exists() else {}
    todo = [i for i in ids if i not in cache and (THUMBS / f"{i}.jpg").exists()]
    if todo and shutil.which("tesseract"):
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, txt in zip(todo, ex.map(_ocr, [(THUMBS / f"{i}.jpg", tessdata) for i in todo])):
                cache[i] = txt
        TEXT_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf8")
    return cache
