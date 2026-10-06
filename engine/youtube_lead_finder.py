#!/usr/bin/env python3
"""
YouTube Coach Lead Finder v2 — discovery core of the AI lead-gen system.

Reads filters from config.yaml (CLI flags override). Pipeline:
  search.list (per keyword) -> channels.list (subs/country/topics)
  -> coaching screen (regex, or LLM if LLM_API_KEY set)
  -> 30-day view velocity -> faceless screen (heuristics + thumbnail face check)
  -> score 0-100 -> CSV -> dedupe via lead_store (if present)

Usage:
  pip install -r requirements.txt
  export YOUTUBE_API_KEY="key1,key2"   # comma-separated = quota rotation
  python youtube_lead_finder.py [--config config.yaml] [--keywords "a,b"] [--out data/x.csv]

CSV columns (shared contract):
channel, channel_id, channel_url, subscribers, country, country_verified,
views_last_30d, videos_last_30d, sells_coaching, coaching_confidence,
faceless_screen, faces_in_thumbnails, topics, description_snippet,
ig_handle, ig_followers, ig_bio, email, email_confidence, score
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

try:
    import yaml
except ImportError:
    yaml = None

from llm_classifier import classify_lead
from face_check import count_faces_in_thumbnails

API = "https://www.googleapis.com/youtube/v3"
COLUMNS = ["channel", "channel_id", "channel_url", "subscribers", "country",
           "country_verified", "views_last_30d", "videos_last_30d",
           "sells_coaching", "coaching_confidence", "faceless_screen",
           "faces_in_thumbnails", "topics", "description_snippet",
           "ig_handle", "ig_followers", "ig_bio", "email", "email_confidence",
           "score"]


REGION_ALIASES = {
    "US": "US", "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "GB": "GB", "UK": "GB", "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB",
    "AU": "AU", "AUS": "AU", "AUSTRALIA": "AU",
}
REGION_DISPLAY = {"US": "USA", "GB": "UK", "AU": "AU"}

def normalize_region_code(value):
    return REGION_ALIASES.get((value or "").strip().upper(), (value or "").strip().upper())

COACHING_PATTERNS = re.compile(
    r"\b(coach(?:ing|es)?|1\s*:\s*1|1-1|mentou?r(?:ship)?|calendly|cal\.com|"
    r"book\s+(a\s+)?(call|strategy|discovery)|enroll(?:ment)?|mastermind|"
    r"program|academy|course|consultation|dm\s+me|apply\s+now)\b", re.IGNORECASE)
FACELESS_NAME_SIGNALS = re.compile(
    r"\b(ai|motivation(al)?|quotes?|compilation|luxury|facts?|top\s*\d+|"
    r"stories|mindset|success\s*(mindset|clips)?|clips|highlights)\b", re.IGNORECASE)
PERSONAL_SIGNALS = re.compile(
    r"\b(i['\u2019]m|i am|my name|i help|i['\u2019]ve|about me|welcome to my)\b",
    re.IGNORECASE)


class YTClient:
    """Rotates across multiple API keys when one hits quota."""

    def __init__(self, keys):
        self.keys = keys
        self.idx = 0
        self.session = requests.Session()
        self.search_calls = 0
        self.units = 0

    def get(self, path, params):
        last_err = None
        for _ in range(len(self.keys)):
            key = self.keys[self.idx]
            try:
                r = self.session.get(f"{API}/{path}",
                                     params={**params, "key": key}, timeout=30)
                if r.status_code == 403:
                    msg = r.json().get("error", {}).get("message", "")
                    if "quotaExceeded" in msg or "rateLimitExceeded" in msg:
                        last_err = msg
                        self.idx = (self.idx + 1) % len(self.keys)
                        continue
                    sys.exit(f"YouTube API 403: {msg}")
                r.raise_for_status()
                if path == "search":
                    self.search_calls += 1
                else:
                    self.units += 1
                return r.json()
            except requests.HTTPError:
                raise
            except requests.RequestException as e:
                last_err = str(e)
                self.idx = (self.idx + 1) % len(self.keys)
        sys.exit(f"YouTube API failed on all keys. Last error: {last_err}")


def iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def heuristic_faceless(name, description):
    name, description = name or "", description or ""
    if FACELESS_NAME_SIGNALS.search(name) and not PERSONAL_SIGNALS.search(description):
        return "likely_faceless"
    if PERSONAL_SIGNALS.search(description):
        return "likely_personal"
    if re.match(r"^[A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?$", name.strip()):
        return "likely_personal"
    return "uncertain"


def score_lead(views_30d, subs, coaching_conf, faceless):
    s = min(views_30d / 100_000, 1.0) * 50          # 30-day velocity
    s += 20 if 5_000 <= subs <= 250_000 else 10      # subscriber sweet spot
    s += max(0.0, min(1.0, coaching_conf)) * 20      # coaching confidence
    s += 10 if faceless == "likely_personal" else 0  # real person bonus
    return round(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--keywords")
    ap.add_argument("--regions")
    ap.add_argument("--min-subs", type=int)
    ap.add_argument("--max-subs", type=int)
    ap.add_argument("--min-views-30d", type=int)
    ap.add_argument("--pages", type=int)
    ap.add_argument("--out")
    args = ap.parse_args()

    cfg = {}
    if yaml and os.path.exists(args.config):
        with open(args.config) as f:
            cfg = yaml.safe_load(f) or {}
    yt, flt, llm_cfg, out_cfg = (cfg.get("youtube", {}), cfg.get("filters", {}),
                                 cfg.get("llm", {}), cfg.get("output", {}))

    keys = ([k.strip() for k in os.environ.get("YOUTUBE_API_KEY", "").split(",")
             if k.strip()] or yt.get("api_keys") or [])
    if not keys:
        sys.exit("No YouTube API key. Set YOUTUBE_API_KEY env var or youtube.api_keys in config.yaml "
                 "(free: Google Cloud Console -> APIs & Services -> Credentials).")

    keywords = ([k.strip() for k in args.keywords.split(",")] if args.keywords
                else yt.get("keywords", []))
    if not keywords:
        sys.exit("No keywords. Set youtube.keywords in config.yaml or --keywords.")
    regions = ([normalize_region_code(r) for r in args.regions.split(",") if r.strip()] if args.regions
               else [normalize_region_code(r) for r in yt.get("regions", []) if str(r).strip()])
    regions = list(dict.fromkeys(r for r in regions if r))
    min_subs = args.min_subs or yt.get("min_subs", 1000)
    max_subs = args.max_subs or yt.get("max_subs", 1_000_000)
    min_views = args.min_views_30d or yt.get("min_views_30d", 10_000)
    pages = args.pages or yt.get("pages_per_keyword", 1)
    llm_on = bool(llm_cfg.get("enabled")) and bool(os.environ.get("LLM_API_KEY"))
    out_dir = out_cfg.get("dir", "data")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or os.path.join(out_dir, f"leads_{stamp}.csv")

    yt_api = YTClient(keys)

    # 1. Discovery
    channel_ids = []
    search_regions = regions or [None]
    for kw in keywords:
        for search_region in search_regions:
            page_token = None
            for _ in range(pages):
                params = {"part": "snippet", "q": kw, "type": "video",
                          "order": "viewCount", "relevanceLanguage": "en",
                          "publishedAfter": iso_days_ago(365), "maxResults": 50}
                if search_region:
                    params["regionCode"] = search_region
                if page_token:
                    params["pageToken"] = page_token
                data = yt_api.get("search", params)
                for item in data.get("items", []):
                    cid = item["snippet"]["channelId"]
                    if cid not in channel_ids:
                        channel_ids.append(cid)
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
    print(f"[1/5] Discovery: {len(channel_ids)} unique channels "
          f"({yt_api.search_calls} search calls)")

    # 2. Channel metadata + subs/region filter
    def batched(it, n=50):
        it = list(it)
        return [it[i:i + n] for i in range(0, len(it), n)]

    channels = {}
    for batch in batched(channel_ids):
        data = yt_api.get("channels", {
            "part": "snippet,statistics,topicDetails,contentDetails",
            "id": ",".join(batch)})
        for c in data.get("items", []):
            channels[c["id"]] = c

    candidates = []
    for cid, c in channels.items():
        snip, stats = c.get("snippet", {}), c.get("statistics", {})
        if stats.get("hiddenSubscriberCount"):
            continue
        subs = int(stats.get("subscriberCount", 0))
        if not (min_subs <= subs <= max_subs):
            continue
        country = normalize_region_code(snip.get("country") or "")
        # STRICT GEO MODE: missing/unknown/non-target country is rejected.
        if regions and country not in regions:
            continue
        candidates.append((cid, c, subs, country))
    print(f"[2/5] Subs/region filter: {len(candidates)} candidates")

    # 3-5. Coaching screen, 30d velocity, faceless screen, scoring
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    rows = []
    for i, (cid, c, subs, country) in enumerate(candidates, 1):
        snip = c.get("snippet", {})
        title, desc = snip.get("title", ""), snip.get("description", "")
        uploads = c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

        # recent videos: ids -> views + titles + thumbnails
        vids, page_token = [], None
        for _ in range(2):
            if not uploads:
                break
            p = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
            if page_token:
                p["pageToken"] = page_token
            d = yt_api.get("playlistItems", p)
            vids += [it["contentDetails"]["videoId"] for it in d.get("items", [])]
            page_token = d.get("nextPageToken")
            if not page_token:
                break
        views_30d, n_30d, titles_30d, thumbs = 0, 0, [], []
        for batch in batched(vids):
            d = yt_api.get("videos", {"part": "statistics,snippet",
                                      "id": ",".join(batch)})
            for v in d.get("items", []):
                vsnip = v.get("snippet", {})
                pub = datetime.fromisoformat(
                    vsnip["publishedAt"].replace("Z", "+00:00"))
                if pub >= cutoff:
                    views_30d += int(v.get("statistics", {}).get("viewCount", 0))
                    n_30d += 1
                    titles_30d.append(vsnip.get("title", ""))
                    th = vsnip.get("thumbnails", {})
                    url = (th.get("maxres") or th.get("high") or {}).get("url")
                    if url:
                        thumbs.append(url)

        # coaching screen
        if llm_on:
            verdict = classify_lead(title, desc, titles_30d)
            if verdict and verdict["sells_coaching"]:
                coaching, conf = True, verdict["coaching_confidence"]
            elif verdict:
                continue  # LLM says not a coach
            else:
                coaching = bool(COACHING_PATTERNS.search(f"{title} {desc}"))
                conf = 0.7 if coaching else 0.0
                if not coaching:
                    continue
            llm_faceless = (verdict or {}).get("faceless")
        else:
            coaching = bool(COACHING_PATTERNS.search(
                f"{title} {desc} {' '.join(titles_30d)}"))
            if not coaching:
                continue
            conf, llm_faceless = 0.7, None

        if views_30d < min_views:
            continue

        # faceless screen
        faceless = heuristic_faceless(title, desc)
        if llm_faceless and llm_faceless != "uncertain":
            faceless = llm_faceless
        faces = count_faces_in_thumbnails(thumbs)
        if faces is not None and faces > 0:
            faceless = "likely_personal"
        if faceless == "likely_faceless":
            continue

        rows.append({
            "channel": title, "channel_id": cid,
            "channel_url": f"https://www.youtube.com/channel/{cid}",
            "subscribers": subs, "country": REGION_DISPLAY.get(country, country),
            "country_verified": "yes" if country in regions else "no",
            "views_last_30d": views_30d, "videos_last_30d": n_30d,
            "sells_coaching": "yes", "coaching_confidence": round(conf, 2),
            "faceless_screen": faceless,
            "faces_in_thumbnails": "" if faces is None else faces,
            "topics": "|".join(c.get("topicDetails", {}).get("topicIds", [])),
            "description_snippet": desc[:200].replace("\n", " "),
            "ig_handle": "", "ig_followers": "", "ig_bio": "",
            "email": "", "email_confidence": "",
            "score": score_lead(views_30d, subs, conf, faceless),
        })
        if i % 25 == 0:
            print(f"      ...processed {i}/{len(candidates)}")

    rows.sort(key=lambda r: r["score"], reverse=True)
    print(f"[3-5/5] Qualified: {len(rows)} leads "
          f"(quota used: {yt_api.search_calls} searches, {yt_api.units} units)")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # dedupe
    try:
        from lead_store import upsert_leads
        res = upsert_leads(rows)
        print(f"Dedupe: {res['new']} new, {res['dup']} already seen")
    except Exception as e:
        print(f"Dedupe skipped (lead_store unavailable): {e}")

    print(f"Done -> {out_path}")


if __name__ == "__main__":
    main()
