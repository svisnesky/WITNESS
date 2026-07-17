"""Session stats persistence + a self-contained HTML dashboard.

record_session() appends one row per play session to stats/marathon_stats.csv
and regenerates stats/dashboard.html (opens in any browser, no internet needed).
"""

import csv
import os

FIELDS = ["date", "start", "duration_min", "total",
          "precision", "finisher", "assist", "down", "kpm"]


def _paths(base_dir):
    sdir = os.path.join(base_dir, "stats")
    os.makedirs(sdir, exist_ok=True)
    return os.path.join(sdir, "marathon_stats.csv"), os.path.join(sdir, "dashboard.html")


def record_session(base_dir, session):
    """Append a session dict and rebuild the dashboard. Returns the html path."""
    csv_path, html_path = _paths(base_dir)
    new = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: session.get(k, "") for k in FIELDS})
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_build_html(rows))
    return html_path


def _num(v, cast=int, default=0):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _tile(label, value, accent=False):
    cls = "tile accent" if accent else "tile"
    return (f'<div class="{cls}"><div class="tv">{value}</div>'
            f'<div class="tl">{label}</div></div>')


def _build_html(rows):
    latest = rows[-1] if rows else {}
    # lifetime aggregates
    life_total = sum(_num(r.get("total")) for r in rows)
    life_prec = sum(_num(r.get("precision")) for r in rows)
    sessions = len(rows)

    tiles = "".join([
        _tile("Kills this session", _num(latest.get("total")), accent=True),
        _tile("Precision downs", _num(latest.get("precision"))),
        _tile("Finishers", _num(latest.get("finisher"))),
        _tile("Assists", _num(latest.get("assist"))),
        _tile("Minutes", _num(latest.get("duration_min"), float)),
        _tile("Kills / min", _num(latest.get("kpm"), float)),
    ])

    # trend: total kills per session (last 24), simple CSS bars
    recent = rows[-24:]
    peak = max([_num(r.get("total")) for r in recent] + [1])
    bars = ""
    for r in recent:
        t = _num(r.get("total"))
        d = r.get("date", "")
        pct = max(4, round(t / peak * 100))
        bars += ('<div class="bar" title="' + d + ' - ' + str(t) + ' kills">'
                 '<div class="fill" style="height:' + str(pct) + '%"></div>'
                 '<div class="bnum">' + str(t) + '</div></div>')

    # recent sessions table (last 12, newest first)
    trows = ""
    for r in reversed(rows[-12:]):
        trows += ("<tr>"
                  f"<td>{r.get('date','')}</td><td>{r.get('start','')}</td>"
                  f"<td class='r'>{_num(r.get('total'))}</td>"
                  f"<td class='r'>{_num(r.get('precision'))}</td>"
                  f"<td class='r'>{_num(r.get('finisher'))}</td>"
                  f"<td class='r'>{_num(r.get('assist'))}</td>"
                  f"<td class='r'>{_num(r.get('duration_min'), float)}</td>"
                  f"<td class='r'>{_num(r.get('kpm'), float)}</td></tr>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marathon — Kill Recorder Stats</title>
<style>
  :root {{ --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
           --muted:#7d8a94; --accent:#9c58da; --ink:#0b0f12; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:32px 20px 64px; -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  .kick {{ color:var(--accent); letter-spacing:.22em; font-size:.72rem;
    text-transform:uppercase; margin:0 0 6px; }}
  h1 {{ font-size:1.9rem; margin:0 0 2px; letter-spacing:-.01em; }}
  .sub {{ color:var(--muted); font-size:.85rem; margin:0 0 26px; }}
  h2 {{ font-size:.78rem; letter-spacing:.16em; text-transform:uppercase;
    color:var(--muted); margin:34px 0 14px; border-bottom:1px solid var(--line);
    padding-bottom:8px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:12px; }}
  .tile {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px; }}
  .tile.accent {{ border-color:var(--accent); }}
  .tv {{ font-size:2rem; font-weight:700; line-height:1; }}
  .tile.accent .tv {{ color:var(--accent); }}
  .tl {{ color:var(--muted); font-size:.72rem; margin-top:8px;
    text-transform:uppercase; letter-spacing:.08em; }}
  .chart {{ display:flex; align-items:flex-end; gap:6px; height:180px;
    background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px; overflow-x:auto; }}
  .bar {{ flex:1 0 18px; min-width:18px; height:100%; display:flex;
    flex-direction:column; justify-content:flex-end; align-items:center; gap:4px; }}
  .fill {{ width:100%; background:var(--accent); border-radius:3px 3px 0 0; }}
  .bnum {{ font-size:.62rem; color:var(--muted); }}
  table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
  th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ color:var(--muted); font-size:.66rem; letter-spacing:.1em;
    text-transform:uppercase; }}
  td.r, th.r {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .foot {{ color:var(--muted); font-size:.72rem; margin-top:34px;
    border-top:1px solid var(--line); padding-top:14px; }}
</style></head><body><div class="wrap">
  <p class="kick">Marathon // Tau Ceti IV</p>
  <h1>Kill Recorder — Session Recap</h1>
  <p class="sub">Latest session on {latest.get('date','')} at {latest.get('start','')}
     &nbsp;·&nbsp; {sessions} sessions logged &nbsp;·&nbsp;
     {life_total} lifetime kills ({life_prec} precision)</p>

  <h2>This session</h2>
  <div class="tiles">{tiles}</div>

  <h2>Kills per session</h2>
  <div class="chart">{bars}</div>

  <h2>Recent sessions</h2>
  <table>
    <tr><th>Date</th><th>Start</th><th class="r">Kills</th><th class="r">Prec</th>
        <th class="r">Fin</th><th class="r">Asst</th><th class="r">Min</th>
        <th class="r">K/min</th></tr>
    {trows}
  </table>

  <p class="foot">Generated by Marathon Kill Recorder. Updates every time you end a session.</p>
</div></body></html>"""
