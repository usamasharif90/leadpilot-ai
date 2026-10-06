#!/usr/bin/env python3
"""SQLite dedupe store for lead-gen rows.

Shared data contract: every lead is a dict / CSV row with these columns:
  channel, channel_id, channel_url, subscribers, country, country_verified,
  views_last_30d, videos_last_30d, sells_coaching, coaching_confidence,
  faceless_screen, faces_in_thumbnails, topics, description_snippet,
  ig_handle, ig_followers, ig_bio, email, email_confidence, score

All columns are stored as TEXT. channel_id is the PRIMARY KEY (dedupe key).
Extra bookkeeping columns: added_at (first-seen timestamp), sent_batch
(batch name when the lead was delivered to a client, NULL = unsent).

CLI:
  python lead_store.py --csv data/leads_final.csv --batch clientA_week1 [--db data/leads.db]
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

COLUMNS = [
    "channel", "channel_id", "channel_url", "subscribers", "country",
    "country_verified", "views_last_30d", "videos_last_30d", "sells_coaching",
    "coaching_confidence", "faceless_screen", "faces_in_thumbnails", "topics",
    "description_snippet", "ig_handle", "ig_followers", "ig_bio", "email",
    "email_confidence", "score",
]


def _connect(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return sqlite3.connect(db_path)


def init_db(db_path="data/leads.db"):
    """Create the leads table if it doesn't exist."""
    coldefs = ['"channel_id" TEXT PRIMARY KEY']
    for c in COLUMNS:
        if c != "channel_id":
            coldefs.append(f'"{c}" TEXT')
    coldefs += ['"added_at" TEXT', '"sent_batch" TEXT NULL']
    ddl = f"CREATE TABLE IF NOT EXISTS leads ({', '.join(coldefs)})"
    with _connect(db_path) as con:
        con.execute(ddl)


def upsert_leads(rows, db_path="data/leads.db"):
    """Insert rows, ignoring duplicates on channel_id.

    Returns {"new": N, "dup": M, "inserted_ids": [...]}.
    Rows missing from the contract columns are padded with "".
    """
    init_db(db_path)
    new, dup, inserted_ids = 0, 0, []
    col_list = ", ".join(f'"{c}"' for c in COLUMNS)
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f'INSERT OR IGNORE INTO leads ({col_list}, "added_at") VALUES ({placeholders}, ?)'
    with _connect(db_path) as con:
        for r in rows:
            vals = [("" if r.get(c) is None else str(r.get(c))) for c in COLUMNS]
            cid = vals[COLUMNS.index("channel_id")].strip()
            if not cid:
                print("warning: skipping row with empty channel_id", file=sys.stderr)
                continue
            vals[COLUMNS.index("channel_id")] = cid
            before = con.total_changes
            con.execute(sql, vals + [datetime.now(timezone.utc).isoformat()])
            if con.total_changes > before:
                new += 1
                inserted_ids.append(cid)
            else:
                dup += 1
    return {"new": new, "dup": dup, "inserted_ids": inserted_ids}


def get_unsent(db_path="data/leads.db", limit=1000):
    """Return leads not yet sent to any client, best score first."""
    with _connect(db_path) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT * FROM leads WHERE sent_batch IS NULL "
            "ORDER BY CAST(score AS REAL) DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def mark_sent(channel_ids, batch_name, db_path="data/leads.db"):
    """Stamp a batch of channel_ids as delivered under batch_name."""
    ids = list(channel_ids)
    if not ids:
        return 0
    with _connect(db_path) as con:
        cur = con.executemany(
            "UPDATE leads SET sent_batch = ? WHERE channel_id = ?",
            [(batch_name, cid) for cid in ids],
        )
        return cur.rowcount


def main():
    ap = argparse.ArgumentParser(description="Upsert a leads CSV into the dedupe store.")
    ap.add_argument("--csv", required=True, help="Input CSV of leads")
    ap.add_argument("--batch", required=True, help="Batch name to stamp on the NEW leads")
    ap.add_argument("--db", default="data/leads.db", help="SQLite db path")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print(f"error: CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    res = upsert_leads(rows, args.db)
    marked = mark_sent(res["inserted_ids"], args.batch, args.db)
    print(
        f"upserted {len(rows)} rows from {args.csv}: "
        f"{res['new']} new, {res['dup']} duplicates; "
        f"marked {marked} as batch \"{args.batch}\""
    )


if __name__ == "__main__":
    main()
