"""Local HTML dashboard for the paper lab: return curves per market, stats, open
positions, recent fills. Reads the database read-only, else the local journal."""

from __future__ import annotations

import html
import math
import json
import os
import re
import webbrowser
from datetime import date

from .. import config as momentum_config
from . import config, journal
from .methods import REGISTRY
from .portfolio import replay
from .report import max_drawdown

# A separate variable from sync's, so a machine set up to view can never write.
READ_URL_VAR = "MOMENTUM_READ_DATABASE_URL"

# Fixed categorical slots (validated light/dark); controls are muted and dashed.
METHOD_STYLE = {
    "theta_puts": 1, "theta_spread": 4, "overnight": 2, "sma_cross": 3,
    "overnight_ftse": 2, "sma_cross_ftse": 3,
    "random_walk": "control", "random_walk_ftse": "control", "lunar": "placebo",
}
CURRENCY = {"SPY": "$", config.FTSE: "£"}


def read_url() -> str | None:
    if os.environ.get(READ_URL_VAR):
        return os.environ[READ_URL_VAR]
    env_file = momentum_config.ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(rf'\s*(?:export\s+)?{READ_URL_VAR}\s*=\s*(.*)', line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def load_records() -> tuple[list[dict], str]:
    url = read_url()
    if not url:
        return journal.read_all(), "local journal (state/lab_journal.jsonl)"
    import psycopg

    with psycopg.connect(url, connect_timeout=15) as conn:
        conn.read_only = True
        rows = conn.execute("select record from lab_journal order by ts, line_hash").fetchall()
    return [r[0] for r in rows], "database (synced nightly from the server)"


# --- data shaping ---------------------------------------------------------------

def panels(records: list[dict]) -> list[dict]:
    """One panel per market: shared dates and each method's return % per date."""
    marks: dict[str, dict[str, float]] = {}
    for r in records:
        if r.get("kind") == "mark" and r.get("method") in REGISTRY:
            marks.setdefault(r["method"], {})[r["date"]] = r["value"]
    out = []
    for underlying in sorted({m.underlying for m in REGISTRY.values()}, key=lambda u: u != "SPY"):
        names = [n for n, m in REGISTRY.items() if m.underlying == underlying]
        dates = sorted({d for n in names for d in marks.get(n, {})})
        series = []
        for n in names:
            vals = marks.get(n, {})
            series.append({
                "name": n,
                "style": METHOD_STYLE.get(n, 1),
                "values": [round((vals[d] / REGISTRY[n].capital - 1) * 100, 4) if d in vals else None
                           for d in dates],
            })
        out.append({"market": underlying, "dates": dates, "series": series})
    return out


def stats(records: list[dict]) -> list[dict]:
    fills = {n: 0 for n in REGISTRY}
    friction = {n: 0.0 for n in REGISTRY}
    curves: dict[str, list[float]] = {n: [] for n in REGISTRY}
    for r in records:
        m = r.get("method")
        if m not in REGISTRY:
            continue
        if r["kind"] == "fill":
            fills[m] += 1
            friction[m] += r["friction"]
        elif r["kind"] == "mark":
            curves[m].append(r["value"])
    rows = []
    for n, method in REGISTRY.items():
        values = curves[n] or [method.capital]
        rows.append({
            "name": n, "market": method.underlying, "value": values[-1],
            "ret": values[-1] / method.capital - 1, "dd": max_drawdown(values),
            "fills": fills[n], "friction": friction[n], "style": METHOD_STYLE.get(n, 1),
        })
    return sorted(rows, key=lambda r: (r["market"] != "SPY", -r["ret"]))


def open_positions(records: list[dict]) -> list[tuple[str, str, int, float]]:
    accounts = replay(records, list(REGISTRY))
    return [(name, key, pos.qty, pos.avg_price)
            for name, acct in accounts.items() for key, pos in acct.positions.items()]


# --- rendering ---------------------------------------------------------------------

W, H = 760, 280
PAD_L, PAD_R, PAD_T, PAD_B = 52, 172, 14, 30


def _ticks(lo: float, hi: float) -> list[float]:
    """Gridlines whose first and last bracket the data, so no line is clipped."""
    span = max(hi - lo, 0.2)
    step = next(s for s in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 50) if span / s <= 6)
    first, last = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    return [round(k * step, 4) for k in range(first, last + 1)]


def _stroke(style) -> tuple[str, str]:
    if style == "control":
        return "var(--muted)", "6 4"
    if style == "placebo":
        return "var(--muted)", "2 4"
    return f"var(--series-{style})", ""


def render_panel(panel: dict) -> str:
    dates, series = panel["dates"], panel["series"]
    if not dates:
        return f'<p class="empty">No trading days recorded for {panel["market"]} yet.</p>'
    vals = [v for s in series for v in s["values"] if v is not None] + [0.0]
    ticks = _ticks(min(vals), max(vals))
    lo, hi = ticks[0], ticks[-1]
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    x = (lambda i: PAD_L + (i / (len(dates) - 1)) * pw) if len(dates) > 1 else (lambda i: PAD_L + pw / 2)
    y = lambda v: PAD_T + (hi - v) / (hi - lo) * ph

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Return by method, {panel["market"]}">']
    for t in ticks:
        cls = "zero" if t == 0 else "grid"
        parts.append(f'<line class="{cls}" x1="{PAD_L}" x2="{PAD_L + pw}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{t:+g}%</text>')
    for i in sorted({0, len(dates) - 1, len(dates) // 2}):
        anchor = "start" if i == 0 else "end" if i == len(dates) - 1 else "middle"
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{H - 8}" text-anchor="{anchor}">{dates[i][5:]}</text>')

    ends = []
    for s in series:
        color, dash = _stroke(s["style"])
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None]
        if not pts:
            continue
        if len(pts) > 1:
            d = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py) in enumerate(pts))
            parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                         f'stroke-dasharray="{dash}" stroke-linejoin="round" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" fill="{color}" '
                     f'stroke="var(--surface)" stroke-width="2"/>')
        last = next(v for v in reversed(s["values"]) if v is not None)
        ends.append([pts[-1][1], s["name"], last])

    # Direct labels at line ends, nudged apart so none overlap.
    ends.sort()
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 14)
    for ly, name, last in ends:
        parts.append(f'<text class="label" x="{PAD_L + pw + 10}" y="{ly + 4:.1f}">'
                     f'{html.escape(name)} {last:+.2f}%</text>')

    parts.append(f'<line class="crosshair" x1="0" x2="0" y1="{PAD_T}" y2="{PAD_T + ph}" visibility="hidden"/>')
    parts.append(f'<rect class="hit" x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}" fill="transparent"/>')
    parts.append("</svg>")
    data = json.dumps({"dates": dates, "left": PAD_L, "width": pw, "series": [
        {"name": s["name"], "values": s["values"]} for s in series]})
    legend = "".join(
        f'<span class="key"><svg width="22" height="10"><line x1="1" x2="21" y1="5" y2="5" '
        f'stroke="{_stroke(s["style"])[0]}" stroke-width="2" stroke-dasharray="{_stroke(s["style"])[1]}"/>'
        f'</svg>{html.escape(s["name"])}</span>' for s in series)
    return (f'<div class="legend">{legend}</div>'
            f'<div class="chart" data-panel=\'{html.escape(data, quote=True)}\'>{"".join(parts)}'
            f'<div class="tip" hidden></div></div>')


def render(records: list[dict], source: str, today: date | None = None) -> str:
    today = today or date.today()
    days_left = max((date.fromisoformat(config.ASSESSMENT_END) - today).days, 0)
    marked_days = len({r["date"] for r in records if r.get("kind") == "mark"})
    titles = {"SPY": "US market (SPY)", config.FTSE: "UK market (FTSE 100, ISF.L)"}

    body = []
    for p in panels(records):
        body.append(f'<section><h2>{titles.get(p["market"], p["market"])}</h2>'
                    f'<p class="sub">Return since start, per method. Dashed grey = control; '
                    f'dotted grey = placebo.</p>{render_panel(p)}</section>')

    rows = "".join(
        f'<tr><td><span class="dot" style="background:{_stroke(r["style"])[0]}"></span>'
        f'{html.escape(r["name"])}{" (control)" if r["style"] == "control" else " (placebo)" if r["style"] == "placebo" else ""}</td>'
        f'<td>{r["market"]}</td><td class="num">{CURRENCY.get(r["market"], "")}{r["value"]:,.0f}</td>'
        f'<td class="num">{r["ret"]:+.2%}</td><td class="num">{r["dd"]:.2%}</td>'
        f'<td class="num">{r["fills"]}</td><td class="num">{CURRENCY.get(r["market"], "")}{r["friction"]:,.2f}</td></tr>'
        for r in stats(records))
    positions = "".join(
        f'<tr><td>{html.escape(n)}</td><td>{html.escape(k.replace("EQ:", "").replace("OPT:", ""))}</td>'
        f'<td class="num">{q:+,}</td><td class="num">{px:,.2f}</td></tr>'
        for n, k, q, px in open_positions(records)) or '<tr><td colspan="4">None</td></tr>'
    fills = [r for r in records if r.get("kind") == "fill"][-15:][::-1]
    fill_rows = "".join(
        f'<tr><td>{r["date"]}</td><td>{html.escape(r["method"])}</td>'
        f'<td>{"BUY" if r["qty"] > 0 else "SELL"} {abs(r["qty"])}</td>'
        f'<td>{html.escape(r["instrument"].replace("EQ:", "").replace("OPT:", ""))}</td>'
        f'<td class="num">{r["price"]:,.2f}</td></tr>' for r in fills) or '<tr><td colspan="5">None yet</td></tr>'

    return TEMPLATE.format(
        days=marked_days, left=days_left, end=config.ASSESSMENT_END, source=html.escape(source),
        generated=today.isoformat(), charts="".join(body), rows=rows, positions=positions, fills=fill_rows)


def run_dashboard(open_browser: bool) -> int:
    try:
        records, source = load_records()
    except Exception as exc:
        print(f"Could not read the database ({str(exc).splitlines()[0]}); using the local journal.")
        records, source = journal.read_all(), "local journal (database unreachable)"
    out = momentum_config.OUTPUT_DIR / "lab_dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(records, source))
    print(f"Dashboard written to {out} ({len(records)} journal records, from {source}).")
    if open_browser:
        webbrowser.open(out.as_uri())
    return 0


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper lab</title>
<style>
:root {{ color-scheme: light; --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19;
  --ink:#fff; --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width: 820px; margin: 0 auto; padding: 28px 16px 48px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 0 0 2px; }}
.sub, .meta {{ color: var(--ink2); margin: 0 0 12px; }}
section {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  padding: 16px; margin: 16px 0; }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 16px; margin-bottom:8px; color:var(--ink2); font-size:13px; }}
.key {{ display:inline-flex; align-items:center; gap:6px; }}
.chart {{ position: relative; }} svg {{ width: 100%; height: auto; display: block; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }} .zero {{ stroke: var(--axis); stroke-width: 1; }}
.tick {{ fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
.label {{ fill: var(--ink2); font-size: 11px; }}
.crosshair {{ stroke: var(--axis); stroke-width: 1; }}
.tip {{ position:absolute; pointer-events:none; background:var(--surface); border:1px solid var(--ring);
  border-radius:8px; padding:8px 10px; font-size:12px; box-shadow:0 4px 16px rgba(0,0,0,.12);
  white-space:nowrap; }}
.tip b {{ display:block; margin-bottom:4px; }} .tip .v {{ font-variant-numeric: tabular-nums; float:right; margin-left:14px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--ink2); font-weight:600; border-bottom:1px solid var(--axis); padding:6px 4px; }}
td {{ border-bottom:1px solid var(--grid); padding:6px 4px; }}
.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px; }}
.empty {{ color: var(--ink2); }}
.note {{ color: var(--ink2); font-size: 13px; }}
.scroll {{ overflow-x: auto; }}
</style></head><body><main>
<h1>Paper-trading lab</h1>
<p class="meta">Day {days} &middot; {left} days to assessment ({end}) &middot; data: {source} &middot; generated {generated}</p>
{charts}
<section><h2>Scoreboard</h2><p class="sub">100,000 start per method in its market's currency; theta_spread starts with $1,300 (about £1,000), sized to the real pilot.</p>
<div class="scroll"><table><thead><tr><th>Method</th><th>Market</th><th class="num">Value</th><th class="num">Return</th>
<th class="num">Max drawdown</th><th class="num">Fills</th><th class="num">Costs paid</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="note">Judge each method against the control in its own market. Three months can refute a method,
never validate one; theta_puts earns small and loses rare-and-big, so its win rate here says nothing about its tail.</p></section>
<section><h2>Open positions</h2><div class="scroll"><table><thead><tr><th>Method</th><th>Instrument</th>
<th class="num">Qty</th><th class="num">Entry</th></tr></thead><tbody>{positions}</tbody></table></div></section>
<section><h2>Recent fills</h2><div class="scroll"><table><thead><tr><th>Date</th><th>Method</th><th>Side</th>
<th>Instrument</th><th class="num">Price</th></tr></thead><tbody>{fills}</tbody></table></div></section>
</main>
<script>
document.querySelectorAll('.chart').forEach(function (el) {{
  var p = JSON.parse(el.dataset.panel), svg = el.querySelector('svg'), tip = el.querySelector('.tip'),
      hair = svg.querySelector('.crosshair'), hit = svg.querySelector('.hit');
  if (!hit) return;
  function show(evt) {{
    var box = svg.getBoundingClientRect(), scale = svg.viewBox.baseVal.width / box.width,
        sx = (evt.clientX - box.left) * scale, n = p.dates.length,
        i = n > 1 ? Math.round((sx - p.left) / (p.width / (n - 1))) : 0;
    i = Math.max(0, Math.min(n - 1, i));
    var hx = n > 1 ? p.left + i * p.width / (n - 1) : p.left + p.width / 2;
    hair.setAttribute('x1', hx); hair.setAttribute('x2', hx); hair.setAttribute('visibility', 'visible');
    var rows = p.series.map(function (s) {{
      var v = s.values[i];
      return '<div>' + s.name + '<span class="v">' + (v == null ? '&ndash;' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%') + '</span></div>';
    }}).join('');
    tip.innerHTML = '<b>' + p.dates[i] + '</b>' + rows;
    tip.hidden = false;
    var left = hx / scale + 14;
    if (left + tip.offsetWidth > box.width) left = hx / scale - tip.offsetWidth - 14;
    tip.style.left = left + 'px'; tip.style.top = '8px';
  }}
  hit.addEventListener('mousemove', show);
  hit.addEventListener('mouseleave', function () {{ tip.hidden = true; hair.setAttribute('visibility', 'hidden'); }});
}});
</script></body></html>
"""
