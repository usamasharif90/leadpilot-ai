#!/usr/bin/env python3
"""Email enrichment for lead-gen rows.

Step 1 (always): extract a domain from URLs in description_snippet, skipping
  known social/link-in-bio platforms (hunter can't domain-search those).

Step 2 (optional): if env HUNTER_API_KEY is set, call Hunter domain search:
    GET https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}
  and take the first email + its confidence score into email / email_confidence.
  0.5s sleep between calls; per-domain errors leave blanks and continue.
  A 401 (bad key) stops further lookups.

Without a key: adds empty email/email_confidence columns, warns to stderr, exits 0.

CLI:
  python email_enrich.py --in data/leads_ig.csv --out data/leads_final.csv
"""

import argparse
import csv
import os
import re
import sys
import time

import requests

CONTRACT = [
    "channel", "channel_id", "channel_url", "subscribers", "country",
    "country_verified", "views_last_30d", "videos_last_30d", "sells_coaching",
    "coaching_confidence", "faceless_screen", "faces_in_thumbnails", "topics",
    "description_snippet", "ig_handle", "ig_followers", "ig_bio", "email",
    "email_confidence", "score",
]

DOMAIN_RE = re.compile(r"https?://(?:www\.)?([A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.IGNORECASE)

# Hunter domain-search can't resolve these; skip to save API calls.
SKIP_DOMAINS = {
    "youtube.com", "youtu.be", "instagram.com", "facebook.com", "twitter.com",
    "x.com", "tiktok.com", "linkedin.com", "linktr.ee", "beacons.ai",
    "calendly.com", "cal.com", "patreon.com", "discord.gg", "discord.com",
    "amazon.com", "amzn.to", "bit.ly", "tinyurl.com", "goo.gl",
}


def extract_domain(text):
    """First usable domain found in text, or ''."""
    for m in DOMAIN_RE.finditer(text or ""):
        domain = m.group(1).lower()
        if domain not in SKIP_DOMAINS:
            return domain
    return ""


def hunter_lookup(domain, api_key):
    """Return (email, confidence) or ("", "") on any error."""
    try:
        r = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": api_key},
            timeout=30,
        )
        if r.status_code == 401:
            print("warning: Hunter API key rejected (401) — stopping lookups",
                  file=sys.stderr)
            return None  # signal: stop everything
        r.raise_for_status()
        emails = (r.json().get("data") or {}).get("emails") or []
        if emails:
            first = emails[0]
            return (first.get("value", "") or "",
                    str(first.get("confidence", "") or ""))
        return "", ""
    except Exception as e:
        print(f"warning: hunter lookup failed for {domain}: {e}", file=sys.stderr)
        return "", ""


def main():
    ap = argparse.ArgumentParser(description="Enrich leads with emails via Hunter.io.")
    ap.add_argument("--in", dest="input", required=True, help="Input CSV of leads")
    ap.add_argument("--out", dest="output", required=True, help="Output CSV path")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between API calls (politeness)")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"error: input CSV not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("HUNTER_API_KEY")
    if not api_key:
        print(
            "warning: HUNTER_API_KEY not set — adding empty email/email_confidence "
            "columns. Set it to enable Hunter domain-search lookups.",
            file=sys.stderr,
        )

    with open(args.input, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    stop = False
    for row in rows:
        out = {c: row.get(c, "") or "" for c in CONTRACT}
        if api_key and not out["email"] and not stop:
            domain = extract_domain(out["description_snippet"])
            if domain:
                res = hunter_lookup(domain, api_key)
                if res is None:  # 401 — bad key, stop lookups
                    stop = True
                else:
                    out["email"], out["email_confidence"] = res
                time.sleep(args.sleep)
        out_rows.append(out)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CONTRACT)
        w.writeheader()
        w.writerows(out_rows)

    print(f"enriched {len(out_rows)} rows -> {args.output} "
          f"(hunter={'on' if api_key and not stop else 'off'})")


if __name__ == "__main__":
    main()
