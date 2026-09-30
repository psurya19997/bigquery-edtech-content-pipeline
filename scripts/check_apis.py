"""Check the project's candidate APIs and save a few sample rows from each to CSV.

Usage:
    python check_apis.py                 # run all sources
    python check_apis.py diksha youtube  # run selected sources
    python check_apis.py datagov --datagov-key YOUR_KEY

Output: one CSV per source in ./api_samples/
Only dependency: requests  (pip install requests)
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")  # Hindi/Marathi text on Windows consoles

OUT = Path(__file__).parent / "api_samples"
OUT.mkdir(exist_ok=True)
TIMEOUT = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-api-check)"}


def save(name, rows):
    if not rows:
        print(f"  (no rows to save)")
        return
    path = OUT / f"{name}.csv"
    fields = list(dict.fromkeys(k for r in rows for k in r))  # union of keys, ordered
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows Indic text
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(rows)} rows -> {path}")


def show(rows, n=5):
    for r in rows[:n]:
        print("   ", r)


# ---------------------------------------------------------------- DIKSHA
def diksha():
    """Govt. of India school content platform. POST search, no key."""
    url = "https://diksha.gov.in/api/content/v1/search"
    fields = ["identifier", "name", "board", "gradeLevel", "subject", "medium",
              "primaryCategory", "mimeType", "createdOn", "lastUpdatedOn",
              "me_totalPlaySessionCount", "me_totalTimeSpentInSec",
              "me_averageRating", "me_totalRatingsCount"]
    body = {"request": {
        "filters": {"status": ["Live"], "medium": ["Marathi"], "gradeLevel": ["Class 6"]},
        "fields": fields,
        "limit": 20,
        "offset": 0,
        "facets": ["subject"],
    }}
    r = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
    print(f"  HTTP {r.status_code}")
    r.raise_for_status()
    res = r.json()["result"]
    print(f"  total matching resources: {res['count']}")

    rows = []
    for c in res.get("content", []):
        plays = c.get("me_totalPlaySessionCount") or {}
        secs = c.get("me_totalTimeSpentInSec") or {}
        rows.append({
            "identifier": c.get("identifier"),
            "name": c.get("name"),
            "board": c.get("board"),
            "grade": "|".join(c.get("gradeLevel") or []),
            "subject": "|".join(c.get("subject") or []),
            "medium": "|".join(c.get("medium") or []),
            "category": c.get("primaryCategory"),
            "mime_type": c.get("mimeType"),
            "created_on": c.get("createdOn"),
            "last_updated_on": c.get("lastUpdatedOn"),
            "play_sessions": plays.get("portal"),        # nested {'portal': n}
            "time_spent_sec": secs.get("portal"),
            "avg_rating": c.get("me_averageRating"),
            "ratings_count": c.get("me_totalRatingsCount"),
        })
    show(rows)
    subj = sorted(res["facets"][0]["values"], key=lambda v: -v["count"])[:8]
    print("  top subjects:", [(v["name"], v["count"]) for v in subj])
    save("diksha_marathi_class6", rows)


# ---------------------------------------------------------------- YouTube RSS
YT_CHANNELS = {
    "khan_india": "UCU0kWLAbhVGxXarmE3b8rHg",  # @KhanAcademyHindi resolves here; last upload Jun 2025
    "khan_india_english": "UCg4BkaHyyE_4-RvEMJ2PTtA",
}


def youtube():
    """Public RSS feed per channel: latest 15 videos with view counts. No key."""
    rows = []
    for label, cid in YT_CHANNELS.items():
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        print(f"  {label}: HTTP {r.status_code}")
        r.raise_for_status()
        for e in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
            def tag(p):
                m = re.search(p, e, re.S)
                return m.group(1) if m else None
            rows.append({
                "channel": label,
                "video_id": tag(r"<yt:videoId>(.*?)</yt:videoId>"),
                "title": tag(r"<title>(.*?)</title>"),
                "published": tag(r"<published>(.*?)</published>"),
                "views": tag(r'<media:statistics views="(\d+)"'),
                "rating_count": tag(r'<media:starRating count="(\d+)"'),
                "snapshot_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
    show(rows)
    save("youtube_rss", rows)


# ---------------------------------------------------------------- UNESCO UIS
def uis():
    """UNESCO Institute for Statistics. No key. National-level only."""
    url = "https://api.uis.unesco.org/api/public/data/indicators"
    params = {"indicator": ["CR.1", "CR.2", "ROFST.1.CP"], "geoUnit": ["IND", "BGD", "PAK"]}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    print(f"  HTTP {r.status_code}")
    r.raise_for_status()
    rows = r.json()["records"]
    print(f"  records: {len(rows)}   (CR.1/CR.2 = primary/lower-sec completion, ROFST.1.CP = out-of-school rate)")
    show(rows)
    save("uis_completion", rows)


# ---------------------------------------------------------------- World Bank
def worldbank():
    """World Bank indicators. No key."""
    url = "https://api.worldbank.org/v2/country/IND;BGD;PAK/indicator/SE.PRM.CMPT.ZS"
    r = requests.get(url, params={"format": "json", "per_page": 200}, headers=HEADERS, timeout=TIMEOUT)
    print(f"  HTTP {r.status_code}")
    r.raise_for_status()
    meta, data = r.json()
    print(f"  total records: {meta['total']}")
    rows = [{"country": d["country"]["value"], "iso3": d["countryiso3code"],
             "indicator": d["indicator"]["id"], "year": d["date"], "value": d["value"]}
            for d in data if d["value"] is not None]
    show(rows)
    save("worldbank_primary_completion", rows)


# ---------------------------------------------------------------- data.gov.in
def datagov(key):
    """OGD India. Needs a free key. Often returns 500/503 — this just checks."""
    if not key:
        print("  skipped: pass --datagov-key YOUR_KEY")
        return
    r = requests.get("https://api.data.gov.in/lists",
                     params={"api-key": key, "format": "json", "limit": 3},
                     headers=HEADERS, timeout=TIMEOUT)
    print(f"  HTTP {r.status_code}: {r.text[:300]}")


SOURCES = ["diksha", "youtube", "uis", "worldbank", "datagov"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*", help=f"any of: {', '.join(SOURCES)} (default: all except datagov)")
    ap.add_argument("--datagov-key")
    args = ap.parse_args()
    args.sources = args.sources or SOURCES[:-1]
    bad = set(args.sources) - set(SOURCES)
    if bad:
        ap.error(f"unknown source(s): {', '.join(bad)}")

    for s in args.sources:
        print(f"\n=== {s.upper()} ===")
        try:
            datagov(args.datagov_key) if s == "datagov" else globals()[s]()
        except Exception as e:  # keep going so one broken API doesn't hide the rest
            print(f"  FAILED: {type(e).__name__}: {e}")
