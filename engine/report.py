#!/usr/bin/env python3
"""
report.py — Turn a leads CSV into a polished, standalone HTML report.

Usage:
    python report.py --in data/leads_final.csv --out report.html \
        [--title "Coaching Leads — Week 1"]

The output is a single self-contained HTML file (all CSS/JS inline, zero
external assets) that renders correctly opened directly from disk (file://).

Missing or empty input is handled gracefully: the script still writes a valid
report with a friendly empty state instead of crashing.
"""

import argparse
import csv
import html
import sys
from datetime import date
from pathlib import Path

CSS = """
:root{
  --bg:#0d1017; --card:#151a24; --card2:#1a2130; --border:#232b3d;
  --text:#e8ecf4; --muted:#8b94a7;
  --accent:#5b8cff; --accent2:#22d3ee;
  --green:#34d399; --amber:#fbbf24; --red:#f87171;
}
*{box-sizing:border-box}
body{
  margin:0; color:var(--text); line-height:1.5;
  background:radial-gradient(1200px 600px at 80% -10%, #1a2340 0%, var(--bg) 55%), var(--bg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
.wrap{max-width:1200px;margin:0 auto;padding:40px 24px 80px}
header h1{margin:0 0 8px;font-size:32px;letter-spacing:-.5px}
.meta{color:var(--muted);font-size:14px;margin-bottom:28px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:8px}
@media(max-width:800px){.stats{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 20px}
.stat .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.stat .v{font-size:28px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
h2{font-size:20px;margin:36px 0 16px}
.top-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.top-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.top-name{font-weight:600;margin-bottom:10px}
.top-name a{color:var(--text);text-decoration:none}
.top-name a:hover{color:var(--accent)}
.top-stats{display:flex;gap:14px;align-items:center;color:var(--muted);font-size:13px;flex-wrap:wrap}
.top-stats b{color:var(--text)}
.score-pill{display:inline-block;min-width:36px;text-align:center;font-weight:700;font-size:13px;
  padding:3px 10px;border-radius:999px;background:#233052;color:#bcd0ff}
.s-hot{background:rgba(52,211,153,.15);color:var(--green);border:1px solid rgba(52,211,153,.45)}
.s-warm{background:rgba(251,191,36,.12);color:var(--amber);border:1px solid rgba(251,191,36,.35)}
.s-cool{background:#233052;color:#bcd0ff}
.table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:14px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:900px}
th{background:var(--card2);text-align:left;padding:12px 14px;font-size:12px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--text);background:#202941}
th[data-dir="asc"] .arrow::after{content:" \\25B2"}
th[data-dir="desc"] .arrow::after{content:" \\25BC"}
td{padding:12px 14px;border-top:1px solid var(--border);vertical-align:top}
tr.hot-row td{background:rgba(52,211,153,.055)}
tbody tr:hover td{background:var(--card2)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.chan{font-weight:600;max-width:280px}
.chan a{color:var(--text);text-decoration:none}
.chan a:hover{color:var(--accent);text-decoration:underline}
td a{color:var(--accent2);text-decoration:none}
td a:hover{text-decoration:underline}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:2px 10px;border-radius:999px;white-space:nowrap}
.b-green{background:rgba(52,211,153,.15);color:var(--green);border:1px solid rgba(52,211,153,.4)}
.b-amber{background:rgba(251,191,36,.12);color:var(--amber);border:1px solid rgba(251,191,36,.35)}
.b-red{background:rgba(248,113,113,.12);color:var(--red);border:1px solid rgba(248,113,113,.35)}
.muted{color:var(--muted);font-size:12px}
.email{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;word-break:break-all}
.empty{background:var(--card);border:1px dashed var(--border);border-radius:14px;
  padding:60px 20px;text-align:center;color:var(--muted);margin-top:24px}
.empty h3{color:var(--text);margin:0 0 8px;font-size:20px}
footer{margin-top:48px;color:var(--muted);font-size:12px}
"""

JS = """
(function(){
  var ths = document.querySelectorAll('th[data-sort]');
  ths.forEach(function(th){
    th.addEventListener('click', function(){
      var table = th.closest('table');
      var tbody = table.querySelector('tbody');
      var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
      var type = th.getAttribute('data-sort');
      var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
      ths.forEach(function(o){ o.removeAttribute('data-dir'); });
      th.setAttribute('data-dir', dir);
      var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
      rows.sort(function(a, b){
        var av = a.children[idx].getAttribute('data-key') || '';
        var bv = b.children[idx].getAttribute('data-key') || '';
        if (type === 'num') {
          var an = parseFloat(av), bn = parseFloat(bv);
          var x = isNaN(an) ? -Infinity : an;
          var y = isNaN(bn) ? -Infinity : bn;
          return dir === 'asc' ? x - y : y - x;
        }
        var s1 = av.toLowerCase(), s2 = bv.toLowerCase();
        if (s1 < s2) return dir === 'asc' ? -1 : 1;
        if (s1 > s2) return dir === 'asc' ? 1 : -1;
        return 0;
      });
      rows.forEach(function(r){ tbody.appendChild(r); });
    });
  });
  var scoreTh = ths[ths.length - 1];
  if (scoreTh) scoreTh.click();
})();
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
@@CSS@@
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>@@TITLE@@</h1>
<div class="meta">Run date: @@RUN_DATE@@ &nbsp;·&nbsp; Source: @@SOURCE@@<br>@@SUMMARY@@</div>
</header>
<section class="stats">
@@STATS@@
</section>
@@TOP_SECTION@@
@@TABLE_SECTION@@
<footer>Generated by the Coach Lead System. Subscriber and view counts come from YouTube's official API; coaching and faceless screens are heuristic — check the confidence scores.</footer>
</div>
<script>
@@JS@@
</script>
</body>
</html>
"""

TABLE_COLS = [
    ("Channel", "str"),
    ("Subs", "num"),
    ("Country", "str"),
    ("Views 30d", "num"),
    ("Coaching", "str"),
    ("Faceless screen", "str"),
    ("IG", "str"),
    ("Email", "str"),
    ("Score", "num"),
]


def esc(value):
    """HTML-escape anything for safe insertion into text or attributes."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def parse_num(value):
    """Parse a float out of a messy CSV cell; None when not parseable."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def trim1(x):
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def fmt_compact(value):
    """1200 -> '1.2k', 3400000 -> '3.4M'."""
    n = parse_num(value)
    if n is None:
        return "—"
    if n >= 1_000_000:
        return trim1(n / 1_000_000) + "M"
    if n >= 1_000:
        return trim1(n / 1_000) + "k"
    return trim1(n)


def fmt_int(value):
    n = parse_num(value)
    if n is None:
        return "—"
    return f"{int(n):,}"


def fmt_score(value):
    n = parse_num(value)
    if n is None:
        return "—"
    return str(int(n)) if float(n).is_integer() else trim1(n)


def load_rows(path):
    """Return (rows, note). Never raises: missing/unreadable/empty CSV degrades gracefully."""
    p = Path(path)
    if not p.is_file():
        return [], f"Could not find the input file: {path}"
    try:
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                rows.append({str(k).strip(): (v or "").strip() for k, v in row.items() if k})
        return rows, ""
    except Exception as e:  # graceful degradation is the point
        return [], f"Could not read {path}: {e}"


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def stat_cards(rows):
    total = len(rows)
    avg_subs = mean([parse_num(r.get("subscribers")) for r in rows])
    avg_views = mean([parse_num(r.get("views_last_30d")) for r in rows])
    with_email = sum(1 for r in rows if (r.get("email") or "").strip())
    pct = f"{with_email / total * 100:.0f}%" if total else "—"
    cards = [
        ("Total leads", f"{total:,}"),
        ("Avg subscribers", fmt_compact(avg_subs) if avg_subs else "—"),
        ("Avg 30-day views", fmt_int(avg_views) if avg_views else "—"),
        ("Email found", pct),
    ]
    return "\n".join(
        f'<div class="stat"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
        for k, v in cards
    )


def filter_summary(rows):
    if not rows:
        return "No filters applied — empty result set."
    subs = [s for s in (parse_num(r.get("subscribers")) for r in rows) if s]
    views = [v for v in (parse_num(r.get("views_last_30d")) for r in rows) if v is not None]
    countries = sorted({(r.get("country") or "").strip().upper() for r in rows} - {""})
    parts = []
    if subs:
        parts.append(f"Subscribers {fmt_int(min(subs))}–{fmt_int(max(subs))}")
    if views:
        parts.append(f"Views/30d ≥ {fmt_int(min(views))}")
    if countries:
        shown = ", ".join(countries[:8]) + ("…" if len(countries) > 8 else "")
        parts.append(f"Countries: {shown}")
    parts.append("Sells coaching: yes")
    parts.append("Language: English")
    return " · ".join(parts)


def score_of(row):
    n = parse_num(row.get("score"))
    return n if n is not None else 0


def top_cards(rows):
    ranked = sorted(rows, key=score_of, reverse=True)[:10]
    cards = []
    for r in ranked:
        name = esc(r.get("channel") or "Untitled channel")
        url = esc(r.get("channel_url") or "")
        link = f'<a href="{url}" target="_blank" rel="noopener">{name}</a>' if url else name
        subs = esc(fmt_compact(r.get("subscribers")))
        views = esc(fmt_int(r.get("views_last_30d")))
        score = esc(fmt_score(r.get("score")))
        cards.append(
            '<div class="top-card">'
            f'<div class="top-name">{link}</div>'
            '<div class="top-stats">'
            f"<span><b>{subs}</b> subs</span>"
            f"<span><b>{views}</b> views/30d</span>"
            f'<span class="score-pill">{score}</span>'
            "</div></div>"
        )
    return "\n".join(cards)


def coaching_cell(r):
    v = (r.get("sells_coaching") or "").strip().lower()
    conf = parse_num(r.get("coaching_confidence"))
    conf_txt = ""
    if conf is not None:
        conf_txt = f" · {int(round(conf * 100))}%" if conf <= 1 else f" · {trim1(conf)}%"
    if v in {"yes", "true", "1", "y"}:
        return (f'<span class="badge b-green">Coaching</span>'
                f'<span class="muted">{esc(conf_txt)}</span>'), "1"
    if v:
        return f'<span class="muted">{esc(v.title())}</span>', "0"
    return '<span class="muted">—</span>', "-1"


def faceless_cell(r):
    v = (r.get("faceless_screen") or "").strip()
    low = v.lower()
    if low == "likely_personal":
        cls = "b-green"
    elif low == "likely_faceless":
        cls = "b-red"
    else:
        cls = "b-amber"
    label = v.replace("_", " ").title() if v else "—"
    return f'<span class="badge {cls}">{esc(label)}</span>', low


def ig_cell(r):
    handle = (r.get("ig_handle") or "").strip().lstrip("@")
    followers = (r.get("ig_followers") or "").strip()
    if not handle:
        return '<span class="muted">—</span>', ""
    url = f"https://www.instagram.com/{handle}"
    fol = fmt_compact(followers) if followers else ""
    fol_html = f'<span class="muted"> · {esc(fol)}</span>' if fol and fol != "—" else ""
    return (f'<a href="{esc(url)}" target="_blank" rel="noopener">@{esc(handle)}</a>'
            f"{fol_html}"), handle.lower()


def email_cell(r):
    email = (r.get("email") or "").strip()
    if not email:
        return '<span class="muted">—</span>', ""
    conf = parse_num(r.get("email_confidence"))
    pct = f" · {int(round(conf * 100))}%" if conf is not None and conf <= 1 else ""
    return (f'<span class="email">{esc(email)}</span>'
            f'<span class="muted">{esc(pct)}</span>'), email.lower()


def score_cell(r):
    n = parse_num(r.get("score"))
    if n is None:
        return '<span class="muted">—</span>', -1
    cls = "s-hot" if n >= 70 else ("s-warm" if n >= 50 else "s-cool")
    return f'<span class="score-pill {cls}">{esc(fmt_score(r.get("score")))}</span>', n


def build_thead():
    ths = "".join(
        f'<th data-sort="{kind}">{esc(label)}<span class="arrow"></span></th>'
        for label, kind in TABLE_COLS
    )
    return f"<tr>{ths}</tr>"


def build_tbody(rows):
    out = []
    for r in rows:
        name = esc(r.get("channel") or "Untitled channel")
        url = esc(r.get("channel_url") or "")
        name_html = (f'<a href="{url}" target="_blank" rel="noopener">{name}</a>'
                     if url else name)
        subs_n = parse_num(r.get("subscribers"))
        views_n = parse_num(r.get("views_last_30d"))
        country = esc((r.get("country") or "").strip().upper() or "—")
        verified = (r.get("country_verified") or "").strip().lower() in {"yes", "true", "1"}
        check = (' <span class="muted" title="Channel country matched the region filter">✓</span>'
                 if verified and country != "—" else "")
        coaching_html, coaching_key = coaching_cell(r)
        faceless_html, faceless_key = faceless_cell(r)
        ig_html, ig_key = ig_cell(r)
        email_html, email_key = email_cell(r)
        score_html, score_n = score_cell(r)
        hot = ' class="hot-row"' if score_n is not None and score_n >= 70 else ""
        out.append(
            f"<tr{hot}>"
            f'<td data-key="{esc((r.get("channel") or "").lower())}" class="chan">{name_html}</td>'
            f'<td data-key="{subs_n if subs_n is not None else -1}" class="num">{esc(fmt_compact(r.get("subscribers")))}</td>'
            f'<td data-key="{esc((r.get("country") or "").lower())}">{country}{check}</td>'
            f'<td data-key="{views_n if views_n is not None else -1}" class="num">{esc(fmt_int(r.get("views_last_30d")))}</td>'
            f'<td data-key="{coaching_key}">{coaching_html}</td>'
            f'<td data-key="{esc(faceless_key)}">{faceless_html}</td>'
            f'<td data-key="{esc(ig_key)}">{ig_html}</td>'
            f'<td data-key="{esc(email_key)}">{email_html}</td>'
            f'<td data-key="{score_n if score_n is not None else -1}" class="num">{score_html}</td>'
            "</tr>"
        )
    return "\n".join(out)


def build_report(rows, title, in_path, note, run_date):
    if rows:
        top_section = ("<h2>Top 10 by score</h2>\n"
                       '<div class="top-grid">\n' + top_cards(rows) + "\n</div>")
        table_section = ("<h2>All leads</h2>\n"
                         '<p class="muted">Click any column header to sort. Green rows scored 70+.</p>\n'
                         '<div class="table-wrap">\n<table>\n<thead>\n'
                         + build_thead() + "\n</thead>\n<tbody>\n"
                         + build_tbody(rows) + "\n</tbody>\n</table>\n</div>")
    else:
        msg = note if note else ("The input CSV loaded fine but has no leads — "
                                 "check the filters or re-run the pipeline.")
        top_section = ""
        table_section = ('<div class="empty"><h3>No leads to show</h3>'
                         f"<p>{esc(msg)}</p>"
                         f'<p>Input was <span class="email">{esc(in_path)}</span></p></div>')

    page = PAGE
    page = page.replace("@@TITLE@@", esc(title))
    page = page.replace("@@RUN_DATE@@", esc(run_date))
    page = page.replace("@@SOURCE@@", esc(in_path))
    page = page.replace("@@SUMMARY@@", esc(filter_summary(rows)))
    page = page.replace("@@STATS@@", stat_cards(rows))
    page = page.replace("@@TOP_SECTION@@", top_section)
    page = page.replace("@@TABLE_SECTION@@", table_section)
    page = page.replace("@@CSS@@", CSS)
    page = page.replace("@@JS@@", JS)
    return page


def main():
    ap = argparse.ArgumentParser(
        description="Generate a standalone HTML lead report from a leads CSV.")
    ap.add_argument("--in", dest="in_path", required=True,
                    help="Input CSV path (e.g. data/leads_final.csv)")
    ap.add_argument("--out", dest="out_path", required=True,
                    help="Output HTML path (e.g. report.html)")
    ap.add_argument("--title", default="Coaching Leads Report",
                    help="Report title shown in the header")
    args = ap.parse_args()

    rows, note = load_rows(args.in_path)
    if note:
        print(f"warning: {note}", file=sys.stderr)

    html_doc = build_report(rows, args.title, args.in_path, note, date.today().isoformat())
    Path(args.out_path).write_text(html_doc, encoding="utf-8")
    print(f"Wrote {args.out_path} ({len(rows)} leads)")


if __name__ == "__main__":
    main()
