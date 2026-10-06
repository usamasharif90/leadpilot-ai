#!/usr/bin/env python3
"""Instagram enrichment for lead-gen rows.

Step 1 (always): guess an IG handle from the channel name —
  lowercase, strip to alphanumeric ("Fit With Joe!" -> "fitwithjoe").
  Also tries the spaces-only variant when they differ ("First  Last" edge cases).
  Best guess goes in ig_handle.

Step 2 (optional): if env IG_ACCESS_TOKEN and IG_USER_ID are set, verify each
  handle via Meta Business Discovery and fill ig_followers + ig_bio:
    GET https://graph.facebook.com/v21.0/{IG_USER_ID}
        ?fields=business_discovery.username({handle}){followers_count,biography}
  Any per-handle error leaves blanks and continues; the script never crashes.

Without tokens: writes ig_handle guesses only, warns to stderr that follower
counts need a Meta app token, exits 0.

CLI:
  python instagram_enrich.py --in data/leads.csv --out data/leads_ig.csv
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


def guess_handle(name):
    """Best-guess IG handle from a channel name.

    Returns the alnum-only variant primarily; falls back to the spaces-removed
    variant if the alnum one is empty (e.g. names with only symbols).
    """
    name = (name or "").strip().lower()
    alnum = re.sub(r"[^a-z0-9]", "", name)
    if alnum:
        return alnum
    return name.replace(" ", "")


def business_discovery(handle, user_id, token):
    """Return (followers_count, biography) for a handle, or ("", "") on any error."""
    url = f"https://graph.facebook.com/v21.0/{user_id}"
    params = {
        "fields": f"business_discovery.username({handle}){{followers_count,biography}}",
        "access_token": token,
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        bd = r.json().get("business_discovery") or {}
        followers = bd.get("followers_count")
        bio = bd.get("biography") or ""
        return (str(followers) if followers is not None else "", bio)
    except Exception as e:  # network error, bad token, unknown handle, rate limit...
        print(f"warning: business_discovery failed for @{handle}: {e}", file=sys.stderr)
        return "", ""


def main():
    ap = argparse.ArgumentParser(description="Enrich leads with Instagram handles/followers.")
    ap.add_argument("--in", dest="input", required=True, help="Input CSV of leads")
    ap.add_argument("--out", dest="output", required=True, help="Output CSV path")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between API calls (politeness)")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"error: input CSV not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    token = os.environ.get("IG_ACCESS_TOKEN")
    user_id = os.environ.get("IG_USER_ID")
    api_enabled = bool(token and user_id)
    if not api_enabled:
        print(
            "warning: IG_ACCESS_TOKEN and/or IG_USER_ID not set — writing handle "
            "guesses only. Set both env vars (Meta app with Instagram Public Content "
            "Access) to fill ig_followers and ig_bio.",
            file=sys.stderr,
        )

    with open(args.input, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    for row in rows:
        out = {c: row.get(c, "") or "" for c in CONTRACT}
        if not out["ig_handle"]:
            out["ig_handle"] = guess_handle(out["channel"])
        if api_enabled and out["ig_handle"]:
            followers, bio = business_discovery(out["ig_handle"], user_id, token)
            out["ig_followers"], out["ig_bio"] = followers, bio
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
          f"(api={'on' if api_enabled else 'off'})")


if __name__ == "__main__":
    main()
