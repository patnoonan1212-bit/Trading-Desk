"""The dashboard: one self-contained web page built from the paper account after every update.

It is published with GitHub Pages, so it opens on a phone or computer at a fixed address. Everything it shows
comes from the paper-trading files; there is no server and no login. The page has no outside scripts: the data
is embedded in the page and the charts are drawn with a few lines of plain JavaScript.
"""

from __future__ import annotations

import html
import json
import math

import pandas as pd


def _num(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), 4)


def build_data(*, now, started, equity, start_equity, day_start_equity, open_risk, positions, equity_curve: pd.Series,
               closed: pd.DataFrame, reviews: dict, checks, news_snapshot, upcoming, spend: dict, learning: dict | None,
               health: str, reviews_count: dict | None = None, readiness: list | None = None, team: dict | None = None) -> dict:
    curve = equity_curve.sort_index()
    if len(curve) > 2000:  # keep the page light: hourly points are plenty for the chart
        curve = curve.resample("1h").last().dropna()
    trades = []
    if len(closed):
        for _, r in closed.sort_values("closed_at").iterrows():
            key = f"{pd.Timestamp(r.planned_at).isoformat()}|{r.symbol}|{r.setup}"
            trades.append({"closed": str(r.closed_at), "symbol": r.symbol, "side": r.direction, "setup": str(r.setup).replace("_", " "),
                           "pnl": _num(r.net_pnl), "r": _num(r.r), "exit": str(r.exit), "trend": str(r.get("trend", "")),
                           "head": reviews.get(key, "")})
    by_setup = []
    if len(closed):
        for setup, g in closed.groupby("setup"):
            by_setup.append({"name": str(setup).replace("_", " "), "trades": int(len(g)), "total_r": _num(g["r"].sum()),
                             "avg_r": _num(g["r"].mean()), "win": _num((g["net_pnl"] > 0).mean())})
    return {
        "now": now.isoformat(), "started": started.isoformat(), "health": health,
        "equity": _num(equity), "start_equity": _num(start_equity), "day_start_equity": _num(day_start_equity),
        "open_risk": _num(open_risk), "positions": positions,
        "curve": [[int(pd.Timestamp(t).timestamp() * 1000), _num(v)] for t, v in curve.items()],
        "trades": trades[-200:], "by_setup": by_setup,
        "checks": [{"name": c.name, "status": c.status, "message": c.message} for c in checks],
        "news": news_snapshot, "upcoming": upcoming, "spend": spend, "learning": learning or {}, "reviews_count": reviews_count or {}, "readiness": readiness or [], "team": team or {},
    }


def render(data: dict) -> str:
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    return PAGE.replace("__DATA__", payload).replace("__TITLE__", html.escape("Trading desk"))


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="Paper trading desk: equity, positions, trades, the head trader and system health.">
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --pos: #2a78d6; --neg: #e34948;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b; --up-text: #006300; --down-text: #b42323;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --pos: #3987e5; --neg: #e66767; --up-text: #0ca30c; --down-text: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --pos: #3987e5; --neg: #e66767; --up-text: #0ca30c; --down-text: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 16px 16px 48px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; margin-bottom: 12px; }
h1 { font-size: 20px; margin: 0; }
h2 { font-size: 16px; margin: 0 0 10px; }
.sub { color: var(--ink-2); font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 12px; min-width: 0; }
.grid2 { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 820px) { .grid2 { grid-template-columns: 1fr 1fr; } .grid2 > .card { margin-top: 0; } .grid2 { margin-top: 12px; } }
.tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-top: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }
.tile .label { color: var(--ink-2); font-size: 13px; }
.tile .value { font-size: 24px; font-weight: 600; margin-top: 2px; }
.tile.hero { grid-column: 1 / -1; }
.tile.hero .value { font-size: 48px; line-height: 1.1; }
@media (min-width: 820px) { .tile.hero { grid-column: span 2; } }
.tile .delta { font-size: 13px; color: var(--ink-2); }
.up, .tile .delta.up { color: var(--up-text); } .down, .tile .delta.down { color: var(--down-text); }
.nowrap { white-space: nowrap; } .second { color: var(--ink-2); font-size: 12px; }
.health { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
       padding: 6px 8px; font-size: 12px; box-shadow: 0 2px 10px rgba(0,0,0,.12); white-space: nowrap; display: none; z-index: 2; }
.tip b { font-size: 14px; display: block; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.empty { color: var(--ink-2); font-size: 14px; margin: 4px 0; }
details { margin-top: 8px; font-size: 13px; } summary { cursor: pointer; color: var(--ink-2); }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); font-size: 12px; white-space: nowrap; }
ul.plain { margin: 6px 0 0; padding-left: 18px; }
.foot { color: var(--muted); font-size: 12px; margin-top: 20px; }
/* ----- Agents view: the trading floor ----- */
:root { --k-idea: #2a78d6; --k-approve: #1baf7a; --k-veto: #eb6834; --k-order: #6250d6; --k-info: #898781; --k-alert: #d03b3b; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { --k-idea: #3987e5; --k-approve: #199e70; --k-veto: #d95926; --k-order: #9085e9; } }
:root[data-theme="dark"] { --k-idea: #3987e5; --k-approve: #199e70; --k-veto: #d95926; --k-order: #9085e9; }
main { padding-bottom: 96px; }
.viewtoggle { position: fixed; left: 50%; bottom: calc(14px + env(safe-area-inset-bottom)); transform: translateX(-50%); z-index: 20;
  display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid var(--border); box-shadow: 0 6px 24px rgba(0,0,0,.14); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.viewtoggle button { border: 0; background: transparent; color: var(--ink-2); font: 600 14px/1 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 10px 18px; border-radius: 999px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; }
.viewtoggle button[aria-pressed="true"] { background: var(--ink); color: var(--surface); }
.viewtoggle .live { width: 8px; height: 8px; border-radius: 50%; background: var(--good); animation: ping 2s infinite; }
@keyframes ping { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--good) 55%, transparent); } 70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
[hidden] { display: none !important; }
.team-head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; margin-top: 12px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); font-size: 13px; color: var(--ink-2); }
.btn { border: 1px solid var(--border); background: var(--surface); color: var(--ink); border-radius: 999px; padding: 7px 14px;
  font: 600 13px/1 system-ui, -apple-system, "Segoe UI", sans-serif; cursor: pointer; }
.btn:hover { background: var(--page); }
.floor { position: relative; margin-top: 12px; border-radius: 16px; overflow: hidden; border: 1px solid var(--border); background: var(--surface); }
.floor canvas { display: block; width: 100%; height: auto; cursor: pointer; touch-action: manipulation; }
.floor .now { position: absolute; left: 12px; top: 10px; font-size: 12px; color: #fff; background: rgba(0,0,0,.45); padding: 3px 9px; border-radius: 999px; }
.roster { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.roster button { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--ink);
  border-radius: 999px; padding: 4px 10px; font: 500 12px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; cursor: pointer; }
.roster button[aria-pressed="true"] { box-shadow: 0 0 0 2px var(--ink); }
.sdot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; font-size: 12px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.kdot { width: 9px; height: 9px; border-radius: 3px; display: inline-block; }
.feed { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow: auto; }
.feed li { display: grid; grid-template-columns: 64px 1fr; gap: 4px 12px; padding: 9px 4px; border-bottom: 1px solid var(--grid); transition: background .4s; }
.feed li.flash { background: color-mix(in srgb, var(--k-idea) 12%, transparent); }
.feed .t { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; padding-top: 2px; white-space: pre-line; }
.feed .route { font-size: 12px; color: var(--ink-2); display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.feed .route b { color: var(--ink); font-weight: 650; }
.feed .msg { grid-column: 2; font-size: 13px; line-height: 1.4; }
.ktag { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border); }
.detail { margin-top: 4px; margin-bottom: 8px; display: grid; gap: 6px; }
.detail .row { font-size: 13px; color: var(--ink-2); } .detail .row b { color: var(--ink); }
</style>
</head>
<body>
<main>
  <header>
    <h1>Trading desk <span class="sub">· paper money</span></h1>
    <div class="sub" id="updated"></div>
  </header>
  <div id="view-dash">
  <div id="health" class="sub"></div>
  <section class="tiles" id="tiles"></section>

  <section class="card">
    <h2>Equity</h2>
    <div class="chart" id="equity"></div>
    <h2 style="margin-top:14px">Drop from the highest point</h2>
    <div class="chart" id="drawdown"></div>
    <details><summary>Show as a table</summary><div class="tablewrap" id="equity-table"></div></details>
  </section>

  <section class="card">
    <h2>Open positions</h2>
    <div class="tablewrap" id="positions"></div>
  </section>

  <div class="grid2">
    <section class="card">
      <h2>Results by setup <span class="sub">(total R; R = profit or loss in units of the risk taken)</span></h2>
      <div class="chart" id="setups"></div>
      <details><summary>Show as a table</summary><div class="tablewrap" id="setups-table"></div></details>
    </section>
    <section class="card">
      <h2>The head trader (Claude)</h2>
      <div id="head"></div>
    </section>
  </div>

  <section class="card">
    <h2>Recent trades</h2>
    <div class="tablewrap" id="trades"></div>
  </section>

  <div class="grid2">
    <section class="card">
      <h2>News</h2>
      <div id="news"></div>
    </section>
    <section class="card">
      <h2>System health (Watchdog)</h2>
      <div class="tablewrap" id="checks"></div>
    </section>
  </div>

  <section class="card">
    <h2>Ready for real money?</h2>
    <div id="ready"></div>
  </section>

  <section class="card">
    <h2>What the desk is learning</h2>
    <div id="learning"></div>
  </section>

  </div>
  <div id="view-agents" hidden>
    <div class="team-head">
      <div>
        <h2 style="margin:0">The trading floor</h2>
        <div class="sub" id="team-sub"></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <span class="chip" id="market-chip"></span>
        <button class="btn" id="replay" type="button">▶ Replay last update</button>
      </div>
    </div>
    <div class="floor" id="floor">
      <canvas id="sim" role="img" aria-label="Animated trading floor: each agent at their desk; agents walk over to deliver messages to each other."></canvas>
      <span class="now" id="sim-now"></span>
    </div>
    <div class="roster" id="roster" aria-label="Agents"></div>
    <div class="legend" id="legend"></div>
    <section class="card">
      <h2 id="feed-title">Conversation</h2>
      <div class="detail" id="detail" hidden></div>
      <ul class="feed" id="feed"></ul>
    </section>
  </div>

  <p class="foot">Paper trading with prices about 10 minutes delayed. Same rules and fill model as the backtests. Updated automatically.</p>
</main>
<nav class="viewtoggle" aria-label="Switch view">
  <button type="button" id="btn-dash" aria-pressed="true">Dashboard</button>
  <button type="button" id="btn-agents" aria-pressed="false"><span class="live"></span>Agents</button>
</nav>
<script>
const D = __DATA__;
const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const money = (x, sign) => x == null ? "–" : (sign && x > 0 ? "+" : x < 0 ? "−" : "") + "$" + Math.abs(x).toLocaleString(undefined, {maximumFractionDigits: 0});
const cents = (x) => x == null ? "–" : "$" + x.toFixed(2);
const pct = (x, sign) => x == null ? "–" : (sign && x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(Math.abs(x) < 0.1 ? 2 : 1) + "%";
const rfmt = (x) => x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(2) + "R";
const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}); };
function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
function svgEl(tag, attrs = {}) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function table(container, cols, rows, empty) {
  container.replaceChildren();
  if (!rows.length) { container.append(el("p", {class: "empty"}, empty)); return; }
  const t = el("table"), head = el("tr");
  cols.forEach(c => head.append(el("th", c.n ? {class: "n"} : {}, c.label)));
  const thead = el("thead"); thead.append(head); t.append(thead);
  const body = el("tbody");
  rows.forEach(r => {
    const tr = el("tr");
    cols.forEach(c => {
      const td = el("td", c.n ? {class: "n"} : {});
      const v = c.get(r);
      if (v instanceof Node) td.append(v); else td.textContent = v;
      if (c.tone) { const t2 = c.tone(r); if (t2) td.classList.add(t2); }
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body);
  container.append(t);
}
const STATUS = {ok: ["✓", "OK", "--good"], warning: ["⚠", "Warning", "--warning"], alert: ["✕", "Needs attention", "--critical"]};
const HEALTH = {ok: "All systems normal", warning: "Running, with warnings (see System health)", alert: "Needs attention (see System health)"};
function statusBadge(s, text) {
  const [icon, label, color] = STATUS[s] || STATUS.ok;
  const span = el("span", {class: "health"});
  const dot = el("span", {class: "dot"}); dot.style.background = css(color);
  span.append(dot, document.createTextNode(icon + " " + (text || label)));
  return span;
}

// ----- header and tiles -----
$("updated").textContent = "Updated " + fmtTime(D.now);
$("health").replaceChildren(statusBadge(D.health, HEALTH[D.health]));
(function tiles() {
  const box = $("tiles");
  const tile = (label, value, delta, tone, hero) => {
    const t = el("div", {class: "tile" + (hero ? " hero" : "")});
    t.append(el("div", {class: "label"}, label), el("div", {class: "value"}, value));
    if (delta) { const d = el("div", {class: "delta" + (tone ? " " + tone : "")}, delta); t.append(d); }
    box.append(t);
  };
  const ret = D.equity / D.start_equity - 1;
  tile("Equity", money(D.equity), pct(ret, true) + " since " + new Date(D.started).toLocaleDateString(), ret >= 0 ? "up" : "down", true);
  const today = D.equity - (D.day_start_equity || D.equity);
  tile("Today", money(today, true), pct(today / (D.day_start_equity || D.equity), true), today >= 0 ? "up" : "down");
  tile("Open risk", money(D.open_risk), D.positions.length + " open position" + (D.positions.length === 1 ? "" : "s"));
  const n = D.trades.length, wins = D.trades.filter(t => t.pnl > 0).length;
  tile("Closed trades", String(n), n ? Math.round(wins / n * 100) + "% winners" : "none yet");
  tile("Claude this month", cents(D.spend.spent), "of " + money(D.spend.budget) + " budget");
})();

// ----- line chart with crosshair -----
function lineChart(container, points, opts) {
  container.replaceChildren();
  if (points.length < 2) { container.append(el("p", {class: "empty"}, opts.empty)); return; }
  const W = Math.max(300, container.clientWidth), H = opts.height, m = {l: 56, r: 12, t: 8, b: 24};
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (opts.zeroTop) { y1 = 0; } if (y0 === y1) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.08; if (!opts.zeroTop) { y0 -= pad; y1 += pad; } else { y0 -= pad; }
  const X = (x) => m.l + (x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1) * (W - m.l - m.r);
  const Y = (y) => m.t + (y1 - y) / (y1 - y0) * (H - m.t - m.b);
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label});
  for (let i = 0; i <= 4; i++) {
    const v = y0 + (y1 - y0) * i / 4, y = Y(v);
    svg.append(svgEl("line", {x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: css("--grid"), "stroke-width": 1}));
    const t = svgEl("text", {x: m.l - 6, y: y + 4, "text-anchor": "end", "font-size": 11, fill: css("--muted")});
    t.style.fontVariantNumeric = "tabular-nums"; t.textContent = opts.tick(v); svg.append(t);
  }
  [xs[0], xs[xs.length - 1]].forEach((x, i) => {
    const t = svgEl("text", {x: X(x), y: H - 6, "text-anchor": i ? "end" : "start", "font-size": 11, fill: css("--muted")});
    t.textContent = new Date(x).toLocaleDateString(undefined, {month: "short", day: "numeric"}); svg.append(t);
  });
  const d = points.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join("");
  const base = opts.zeroTop ? Y(0) : H - m.b;
  const area = svgEl("path", {d: d + `L${X(xs[xs.length - 1])},${base}L${X(xs[0])},${base}Z`, fill: css(opts.color), "fill-opacity": 0.1});
  svg.append(area, svgEl("path", {d, fill: "none", stroke: css(opts.color), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  const last = points[points.length - 1];
  svg.append(svgEl("circle", {cx: X(last[0]), cy: Y(last[1]), r: 4, fill: css(opts.color), stroke: css("--surface"), "stroke-width": 2}));
  const hair = svgEl("line", {y1: m.t, y2: H - m.b, stroke: css("--axis"), "stroke-width": 1, visibility: "hidden"});
  const dot = svgEl("circle", {r: 4, fill: css(opts.color), stroke: css("--surface"), "stroke-width": 2, visibility: "hidden"});
  svg.append(hair, dot);
  const tip = el("div", {class: "tip"});
  const hit = svgEl("rect", {x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent", tabindex: 0});
  svg.append(hit);
  const show = (clientX) => {
    const r = svg.getBoundingClientRect(), x = (clientX - r.left) * W / r.width;
    const t = xs[0] + (x - m.l) / (W - m.l - m.r) * (xs[xs.length - 1] - xs[0]);
    let i = 0, best = Infinity; xs.forEach((v, k) => { const dd = Math.abs(v - t); if (dd < best) { best = dd; i = k; } });
    const px = X(xs[i]), py = Y(ys[i]);
    hair.setAttribute("x1", px); hair.setAttribute("x2", px); hair.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
    tip.replaceChildren(el("b", {}, opts.value(ys[i])), document.createTextNode(fmtTime(xs[i])));
    tip.style.display = "block";
    const left = px * r.width / W; tip.style.left = Math.min(Math.max(0, left - 60), r.width - 130) + "px"; tip.style.top = "0px";
  };
  const hide = () => { hair.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.style.display = "none"; };
  hit.addEventListener("pointermove", (e) => show(e.clientX));
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => { const r = svg.getBoundingClientRect(); show(r.right - 1); });
  hit.addEventListener("blur", hide);
  container.append(svg, tip);
}

// ----- horizontal bars around zero (polarity: blue gains, red losses) -----
function barChart(container, rows) {
  container.replaceChildren();
  if (!rows.length) { container.append(el("p", {class: "empty"}, "No closed trades yet.")); return; }
  const vals = rows.map(r => r.total_r), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = (hi - lo) || 1;
  // Names on the left, then room for the value labels of losing setups, so the two never touch.
  const W = Math.max(300, container.clientWidth), bar = 20, gap = 12, m = {l: 120 + (lo < 0 ? 56 : 0), r: 56, t: 4, b: 4};
  const H = m.t + m.b + rows.length * (bar + gap);
  const X = (v) => m.l + (v - lo) / span * (W - m.l - m.r);
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Total R by setup"});
  svg.append(svgEl("line", {x1: X(0), x2: X(0), y1: 0, y2: H, stroke: css("--axis"), "stroke-width": 1}));
  const tip = el("div", {class: "tip"});
  rows.forEach((r, i) => {
    const y = m.t + i * (bar + gap), x0 = X(Math.min(0, r.total_r)), w = Math.max(2, Math.abs(X(r.total_r) - X(0)));
    const pos = r.total_r >= 0, rad = 4;
    // 4px rounded data end, square at the zero line
    const path = pos
      ? `M${x0},${y}H${x0 + w - rad}Q${x0 + w},${y} ${x0 + w},${y + rad}V${y + bar - rad}Q${x0 + w},${y + bar} ${x0 + w - rad},${y + bar}H${x0}Z`
      : `M${x0 + w},${y}H${x0 + rad}Q${x0},${y} ${x0},${y + rad}V${y + bar - rad}Q${x0},${y + bar} ${x0 + rad},${y + bar}H${x0 + w}Z`;
    const mark = svgEl("path", {d: path, fill: css(pos ? "--pos" : "--neg"), tabindex: 0});
    const name = svgEl("text", {x: 112, y: y + bar / 2 + 4, "text-anchor": "end", "font-size": 12, fill: css("--ink-2")}); name.textContent = r.name;
    const val = svgEl("text", {x: pos ? x0 + w + 6 : x0 - 6, y: y + bar / 2 + 4, "text-anchor": pos ? "start" : "end", "font-size": 12, fill: css("--ink")});
    val.style.fontVariantNumeric = "tabular-nums"; val.textContent = rfmt(r.total_r);
    const showTip = () => {
      tip.replaceChildren(el("b", {}, rfmt(r.total_r)), document.createTextNode(`${r.name} · ${r.trades} trades · ${Math.round(r.win * 100)}% winners · avg ${rfmt(r.avg_r)}`));
      const rect = svg.getBoundingClientRect(); tip.style.display = "block";
      tip.style.left = Math.max(0, Math.min(rect.width - 240, (m.l) * rect.width / W)) + "px"; tip.style.top = (y + bar + 4) * rect.width / W + "px";
      mark.setAttribute("fill-opacity", 0.8);
    };
    const hideTip = () => { tip.style.display = "none"; mark.setAttribute("fill-opacity", 1); };
    mark.addEventListener("pointermove", showTip); mark.addEventListener("pointerleave", hideTip);
    mark.addEventListener("focus", showTip); mark.addEventListener("blur", hideTip);
    svg.append(name, mark, val);
  });
  container.append(svg, tip);
}

function drawCharts() {
  const pts = D.curve;
  lineChart($("equity"), pts, {height: 220, color: "--series-1", label: "Equity over time", empty: "The equity curve appears after the first trading day.",
    tick: (v) => "$" + (Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(0)), value: (v) => money(v)});
  let peak = -Infinity; const dd = pts.map(([t, v]) => { peak = Math.max(peak, v); return [t, peak > 0 ? (v / peak - 1) : 0]; });
  lineChart($("drawdown"), dd, {height: 110, color: "--series-2", zeroTop: true, label: "Drop from the highest point", empty: "",
    tick: (v) => (v * 100).toFixed(1) + "%", value: (v) => pct(v)});
  barChart($("setups"), [...D.by_setup].sort((a, b) => b.total_r - a.total_r));
}
drawCharts();
let lastW = document.body.clientWidth;
new ResizeObserver(() => { if (Math.abs(document.body.clientWidth - lastW) > 20) { lastW = document.body.clientWidth; drawCharts(); } }).observe(document.body);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawCharts);

// ----- tables -----
const daily = []; { let lastDay = ""; D.curve.forEach(([t, v]) => { const day = new Date(t).toLocaleDateString(); if (day !== lastDay) { daily.push([t, v]); lastDay = day; } else daily[daily.length - 1] = [t, v]; }); }
table($("equity-table"), [{label: "Day", get: r => new Date(r[0]).toLocaleDateString()}, {label: "Equity (end of day)", n: 1, get: r => money(r[1])}],
  daily.slice().reverse(), "No data yet.");
table($("setups-table"), [{label: "Setup", get: r => r.name}, {label: "Trades", n: 1, get: r => r.trades}, {label: "Winners", n: 1, get: r => pct(r.win)},
  {label: "Avg R", n: 1, get: r => rfmt(r.avg_r)}, {label: "Total R", n: 1, get: r => rfmt(r.total_r)}], D.by_setup, "No closed trades yet.");
table($("positions"), [
  {label: "Contract", get: r => r.symbol}, {label: "Side", get: r => r.side}, {label: "Contracts", n: 1, get: r => r.contracts},
  {label: "Entry", n: 1, get: r => r.entry.toFixed(2)}, {label: "Stop", n: 1, get: r => r.stop.toFixed(2)}, {label: "Target", n: 1, get: r => r.target.toFixed(2)},
  {label: "Open P&L", n: 1, get: r => money(r.pnl, true), tone: r => r.pnl >= 0 ? "up" : "down"}, {label: "Setup", get: r => r.setup}],
  D.positions, "No open positions. The desk waits for setups that meet every rule.");
const short = (iso) => { const d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString(undefined, {month: "short", day: "numeric"}) + " " + d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", hour12: false}); };
const two = (a, b) => { const f = document.createDocumentFragment(); f.append(el("div", {}, a), el("div", {class: "second"}, b)); return f; };
table($("trades"), [
  {label: "Closed", get: r => el("span", {class: "nowrap"}, short(r.closed))},
  {label: "Trade", get: r => two(`${r.symbol} ${r.side} · ${r.setup}`, r.exit + (r.head ? " · head trader: " + r.head : ""))},
  {label: "P&L", n: 1, get: r => money(r.pnl, true), tone: r => r.pnl >= 0 ? "up" : "down"},
  {label: "R", n: 1, get: r => rfmt(r.r)}],
  D.trades.slice(-25).reverse(), "No closed trades yet.");
table($("checks"), [{label: "Status", get: r => statusBadge(r.status)}, {label: "Check", get: r => r.name}, {label: "Detail", get: r => r.message}],
  D.checks.slice().sort((a, b) => ({alert: 0, warning: 1, ok: 2}[a.status] - {alert: 0, warning: 1, ok: 2}[b.status])), "No checks yet.");

// ----- head trader -----
(function head() {
  const box = $("head"), h = D.learning.head_trader || {}, c = D.reviews_count || {};
  if (!h.reviewed && c.total) {
    box.append(el("p", {}, `Reviewed ${c.total} ideas so far: approved ${c.approved || 0}, made ${c.reduced || 0} smaller, passed on ${c.rejected || 0}.`),
               el("p", {class: "sub"}, "How its calls compare with the rules alone appears after the weekly learning review."));
  } else if (!h.reviewed) {
    box.append(el("p", {class: "empty"}, D.spend.connected ? "No trade ideas reviewed yet. Claude reviews each idea that passes every rule."
      : "Not connected. Add the ANTHROPIC_API_KEY secret to turn it on."));
  } else {
    const p = el("p"); p.textContent = `Reviewed ${h.reviewed} ideas: approved ${h.approved}, made ${h.reduced} smaller, passed on ${h.rejected}.`;
    box.append(p);
    if (h.rejected_known) box.append(el("p", {}, `The ideas it passed on would have made ${rfmt(h.rejected_rules_r)} in total under the rules alone (${h.rejected_rules_wins} winners).`));
    if (h.rules_only_equity != null) {
      const diff = h.paper_equity - h.rules_only_equity;
      box.append(el("p", {}, `Paper account ${money(h.paper_equity)} vs ${money(h.rules_only_equity)} with the rules alone: Claude has ${diff >= 0 ? "added" : "cost"} ${money(Math.abs(diff))} so far.` + (h.reviewed < 30 ? " Too few reviews to judge yet." : "")));
    }
  }
  box.append(el("p", {class: "sub"}, `Claude spending this month: ${cents(D.spend.spent)} of ${money(D.spend.budget)} (trade reviews ${cents(D.spend.reviews)}, news ${cents(D.spend.news)}).`));
})();

// ----- news -----
(function news() {
  const box = $("news"), s = D.news;
  if (s) {
    box.append(el("p", {}, s.summary), el("p", {class: "sub"}, `Read ${fmtTime(s.ts)} · risk ${s.risk_level}`));
    const ul = el("ul", {class: "plain"});
    Object.entries(s.markets || {}).forEach(([k, v]) => { const li = el("li"); li.append(el("strong", {}, k + ": "), document.createTextNode(v.mood + (v.note ? " – " + v.note : ""))); ul.append(li); });
    box.append(ul);
  } else box.append(el("p", {class: "empty"}, "No news read yet."));
  box.append(el("h2", {style: "margin-top:14px"}, "Scheduled releases, next 24 hours"));
  if (D.upcoming.length) { const ul = el("ul", {class: "plain"}); D.upcoming.forEach(u => ul.append(el("li", {}, fmtTime(u.ts) + " · " + u.event))); box.append(ul); }
  else box.append(el("p", {class: "empty"}, "None."));
})();

// ----- live readiness -----
(function ready() {
  const box = $("ready"), items = D.readiness || [];
  if (!items.length) { box.append(el("p", {class: "empty"}, "Checklist appears after the next update.")); return; }
  const done = items.filter(i => i.passed).length;
  box.append(el("p", {}, `${done} of ${items.length} checks passed. Real-money trading stays locked until all pass, and then it is still your decision.`));
  const wrap = el("div", {class: "tablewrap"}); box.append(wrap);
  table(wrap, [{label: "Status", get: i => statusBadge(i.passed ? "ok" : "warning", i.passed ? "Passed" : "Not yet")},
               {label: "Check", get: i => i.name}, {label: "Where it stands", get: i => i.detail}], items, "");
})();

// ----- learning -----
(function learning() {
  const box = $("learning"), L = D.learning;
  if (!L.generated) { box.append(el("p", {class: "empty"}, "The first weekly learning review hasn't run yet.")); return; }
  box.append(el("p", {class: "sub"}, `Last review ${fmtTime(L.generated)}, based on ${L.trades} trades.`));
  const props = L.proposals || [];
  if (!props.length) box.append(el("p", {}, "No changes suggested: nothing in the results is clear enough to act on."));
  props.forEach(p => {
    const d = el("div", {style: "margin-top:10px"});
    const tag = {passed: "Passed its test · waiting for your approval", failed: "Didn't hold up on held-back history", "not tested": "Not tested yet"}[p.status];
    d.append(el("strong", {}, p.id + ": " + p.title + " "), el("span", {class: "pill"}, tag), el("div", {class: "sub"}, p.why));
    box.append(d);
  });
  const groups = (L.groups || []).filter(g => g.verdict.startsWith("clearly")).slice(0, 8);
  if (groups.length) {
    box.append(el("h2", {style: "margin-top:14px"}, "Clearest patterns"));
    const wrap = el("div", {class: "tablewrap"}); box.append(wrap);
    table(wrap, [{label: "Area", get: g => g.area}, {label: "Group", get: g => g.name.replaceAll("_", " ")}, {label: "Trades", n: 1, get: g => g.trades},
      {label: "Avg R", n: 1, get: g => rfmt(g.exp_r)}, {label: "Verdict", get: g => g.verdict}], groups, "");
  }
})();

// ===================== Agents view: the trading floor =====================
(function floor() {
  const T = D.team || {};
  const agents = T.agents || [], msgs = T.messages || [];
  if (!agents.length) return;
  const byId = Object.fromEntries(agents.map(a => [a.id, a]));
  const KIND = {idea: ["Trade idea", "--k-idea"], approve: ["Approval", "--k-approve"], veto: ["Veto", "--k-veto"],
                order: ["Order", "--k-order"], info: ["Update", "--k-info"], alert: ["Alert", "--k-alert"]};
  const STATE = {working: "Working", idle: "Idle", problem: "Problem", off: "Off", paused: "Paused trading"};
  const STATE_COLOR = {working: "--good", idle: "--axis", problem: "--critical", off: "--muted", paused: "--warning"};
  const ROLE = {data: "the robot who fetches prices", news: "the reporter", regime: "the market weather forecaster",
                scanner: "the scout who spots setups", risk: "the referee with the final say", overseer: "the boss (Claude)",
                execution: "the floor trader", broker: "the exchange teller", watchdog: "the guard dog", learning: "the professor",
                you: "your desk"};

  // ----- floor plans: wide screens (one long aisle) and phones (a tall one) -----
  const PLANS = {
    wide: {w: 1000, h: 640, aisle: "h", at: 360, wall: 104,
      desks: {data: [110, 238, "down"], scanner: [290, 238, "down"], risk: [500, 238, "down"], overseer: [680, 238, "down"],
              execution: [870, 238, "down"], news: [110, 506, "up"], regime: [290, 506, "up"], learning: [500, 506, "up"],
              broker: [680, 506, "up"], you: [870, 506, "up"]},
      room: [405, 140, 370, 180], dogbed: [36, 360], coffee: [968, 360], cooler: [968, 300],
      shelf: [585, 600, 110], printer: [955, 238], board: [200, 116], plants: [[976, 124], [24, 616], [976, 616], [395, 616], [785, 616]]},
    tall: {w: 520, h: 1190, aisle: "v", at: 260, wall: 104,
      desks: {data: [105, 215, "right"], news: [415, 215, "left"], scanner: [105, 400, "right"], regime: [415, 400, "left"],
              risk: [105, 585, "right"], you: [415, 585, "left"], overseer: [105, 770, "right"], learning: [415, 770, "left"],
              execution: [105, 955, "right"], broker: [415, 955, "left"]},
      room: [12, 512, 210, 365], dogbed: [260, 130], coffee: [260, 1165], cooler: [330, 1150],
      shelf: null, printer: [40, 1125], board: [0, 0], plants: [[496, 124], [24, 1170], [496, 1170]]},
  };

  const canvas = $("sim"), ctx = canvas.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let plan, scale = 1, dark = false, pal = {};
  const people = {};
  let dog;
  let bubbles = [], effects = [], prints = [];
  let selected = null, alarmUntil = 0, flashUntil = 0, flashColor = "#fff", tickerBoost = 0;
  const night = !T.market_open;
  const regimes = T.regimes || [];

  function palette() {
    const attr = document.documentElement.getAttribute("data-theme");
    dark = attr === "dark" || (attr !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
    pal = dark ? {floor: "#2a2620", plank: "#25221c", aisle: "#3a2f3a", aisleLine: "#4a3b49", wall: "#1f2228", wallTrim: "#3a3e46", desk: "#5a4634",
                  deskTop: "#6b543e", screenOff: "#121211", chair: "#45464d", room: "rgba(90,140,220,.10)", roomEdge: "rgba(130,170,240,.45)",
                  rug: "#3b4a6b", ink: "#f0efec", ink2: "#c3c2b7", plate: "rgba(30,30,29,.9)", bubble: "#f6f5f1", bubbleInk: "#0b0b0b",
                  shadow: "rgba(0,0,0,.4)", plant: "#3f7a4a", plant2: "#2f6a3c", pot: "#7a5236", metal: "#8a93a3"}
                : {floor: "#efe4d2", plank: "#e6d9c4", aisle: "#d9cfe0", aisleLine: "#cbbfd6", wall: "#e3eaf2", wallTrim: "#c5cfdb", desk: "#b98b5e",
                  deskTop: "#d0a57a", screenOff: "#2b2b2b", chair: "#5b5f66", room: "rgba(90,140,220,.08)", roomEdge: "rgba(70,120,200,.4)",
                  rug: "#c9d6ef", ink: "#0b0b0b", ink2: "#52514e", plate: "rgba(255,255,255,.94)", bubble: "#ffffff", bubbleInk: "#0b0b0b",
                  shadow: "rgba(0,0,0,.14)", plant: "#4f9a5c", plant2: "#3f8a4c", pot: "#b5703f", metal: "#9aa3b2"};
  }

  // ----- geometry -----
  function seat(id) {
    const [x, y, face] = plan.desks[id];
    return {up: [x, y + 44], down: [x, y - 44], right: [x - 64, y - 6], left: [x + 64, y - 6]}[face];
  }
  function front(id, off = 0) {
    const [x, y, face] = plan.desks[id];
    return {down: [x + off, y + 112], up: [x + off, y - 112], right: [x + 130, y + off], left: [x - 130, y + off]}[face];
  }
  const toAisle = p => plan.aisle === "h" ? [p[0], plan.at] : [plan.at, p[1]];
  const route = (a, b) => [toAisle(a), toAisle(b), b];

  function layout() {
    const cssW = Math.max(300, $("floor").getBoundingClientRect().width);
    const p = cssW < 700 ? PLANS.tall : PLANS.wide, changed = p !== plan;
    plan = p; scale = cssW / plan.w;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(plan.h * scale * dpr);
    canvas.style.height = plan.h * scale + "px";
    $("sim-now").style.top = Math.round(plan.wall * scale + 8) + "px";
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0);
    palette();
    if (changed) {
      agents.forEach(a => { if (a.id !== "watchdog") { const s = seat(a.id); people[a.id] = {id: a.id, x: s[0], y: s[1], path: [], speed: 190, seated: true, facing: 1, phase: Math.random() * 6, hop: 0, hopT: 0, onArrive: null}; } });
      dog = {x: plan.dogbed[0], y: plan.dogbed[1], path: [], speed: 120, facing: 1, phase: 0, patrolAt: 0, onArrive: null, sitting: true, barkUntil: 0, hopT: 0, hop: 0};
    }
  }

  // ----- drawing helpers -----
  const px = n => n / scale;
  function font(size, weight = 500) { ctx.font = `${weight} ${px(size)}px system-ui, -apple-system, "Segoe UI", sans-serif`; }
  function rr(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }
  function circle(x, y, r, c) { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); }
  function textAt(s, x, y, size, weight, color, align = "center") { font(size, weight); ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(s, x, y); }

  // ----- the room -----
  function drawRoom(t) {
    // wooden floor
    ctx.fillStyle = pal.floor; ctx.fillRect(0, 0, plan.w, plan.h);
    ctx.fillStyle = pal.plank;
    for (let y = plan.wall, row = 0; y < plan.h; y += 22, row++) {
      ctx.fillRect(0, y, plan.w, 1.2);
      for (let x = (row % 3) * 70; x < plan.w; x += 210) ctx.fillRect(x, y, 1.2, 22);
    }
    // the aisle runner
    ctx.fillStyle = pal.aisle;
    if (plan.aisle === "h") { ctx.fillRect(0, plan.at - 36, plan.w, 72); ctx.fillStyle = pal.aisleLine; ctx.fillRect(0, plan.at - 30, plan.w, 3); ctx.fillRect(0, plan.at + 27, plan.w, 3); }
    else { ctx.fillRect(plan.at - 42, plan.wall, 84, plan.h - plan.wall); ctx.fillStyle = pal.aisleLine; ctx.fillRect(plan.at - 36, plan.wall, 3, plan.h); ctx.fillRect(plan.at + 33, plan.wall, 3, plan.h); }
    // decision room: rug and glass walls with a door gap
    const [rx, ry, rw, rh] = plan.room;
    ctx.fillStyle = pal.rug; rr(rx + 12, ry + 24, rw - 24, rh - 36, 18); ctx.fill();
    ctx.fillStyle = pal.room; rr(rx, ry, rw, rh, 14); ctx.fill();
    ctx.strokeStyle = pal.roomEdge; ctx.lineWidth = 3; ctx.setLineDash([]); rr(rx, ry, rw, rh, 14); ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,.5)"; ctx.lineWidth = 2;
    for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.moveTo(rx + 20 + i * 14, ry + 6); ctx.lineTo(rx + 8 + i * 14, ry + 18); ctx.stroke(); }  // glass glint
    textAt("DECISION ROOM", rx + 12, ry + px(16), 10.5, 750, pal.ink2, "left");
    // wall
    ctx.fillStyle = pal.wall; ctx.fillRect(0, 0, plan.w, plan.wall);
    ctx.fillStyle = pal.wallTrim; ctx.fillRect(0, plan.wall - 7, plan.w, 7);
    const wide = plan.w > 600;
    const sky = night ? (dark ? "#0b1530" : "#1c2b52") : (dark ? "#2a4a6e" : "#9fd0f5");
    const wins = wide ? [[24, 14, 120], [856, 14, 120]] : [[14, 14, 92], [414, 14, 92]];
    wins.forEach(([x, y, w], k) => {
      const h = plan.wall - 46;
      ctx.fillStyle = sky; rr(x, y, w, h, 4); ctx.fill();
      if (night) { for (let i = 0; i < 7; i++) circle(x + 10 + (i * 37) % (w - 14), y + 6 + (i * 17) % (h - 10), 1.1, "#f5f1c4");
                   circle(x + w - 18, y + 14, 7, "#f4f1d0"); circle(x + w - 15, y + 12, 7, sky); }
      else { circle(x + w - 20, y + 15, 7, "#ffd166"); ctx.fillStyle = "rgba(255,255,255,.85)"; const cx = x + 10 + ((t / 90 + k * 40) % (w - 30)); rr(cx, y + 18, 26, 8, 4); ctx.fill(); }
      ctx.strokeStyle = pal.wallTrim; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
      ctx.strokeStyle = "rgba(0,0,0,.08)"; ctx.lineWidth = 1;                         // blinds
      for (let yy = y + 5; yy < y + h * 0.42; yy += 4) { ctx.beginPath(); ctx.moveTo(x, yy); ctx.lineTo(x + w, yy); ctx.stroke(); }
    });
    // clock showing the update time (New York)
    const ny = new Date(new Date(D.now).toLocaleString("en-US", {timeZone: "America/New_York"}));
    const ck = wide ? [190, 40] : [126, 34];
    circle(ck[0], ck[1], 17, "#fff"); ctx.strokeStyle = "#3a3e46"; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(ck[0], ck[1], 17, 0, 7); ctx.stroke();
    const hA = ((ny.getHours() % 12) + ny.getMinutes() / 60) / 12 * Math.PI * 2, mA = ny.getMinutes() / 60 * Math.PI * 2;
    ctx.lineCap = "round"; ctx.lineWidth = 2.6; ctx.beginPath(); ctx.moveTo(ck[0], ck[1]); ctx.lineTo(ck[0] + Math.sin(hA) * 8, ck[1] - Math.cos(hA) * 8); ctx.stroke();
    ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(ck[0], ck[1]); ctx.lineTo(ck[0] + Math.sin(mA) * 12, ck[1] - Math.cos(mA) * 12); ctx.stroke(); ctx.lineCap = "butt";
    if (wide) textAt("NEW YORK", ck[0], ck[1] + 27, 8.5, 700, pal.ink2);
    // alarm light (spins red during alerts)
    const al = wide ? [810, 34] : [396, 30];
    const alarm = performance.now() < alarmUntil;
    circle(al[0], al[1], 9, alarm ? "#ff3b30" : "#8f2a24");
    if (alarm && !reduce) { const a = t / 120; ctx.fillStyle = "rgba(255,59,48,.18)"; ctx.beginPath(); ctx.moveTo(al[0], al[1]); ctx.arc(al[0], al[1], 70, a, a + 0.6); ctx.fill(); }
    ctx.fillStyle = "#555"; ctx.fillRect(al[0] - 9, al[1] + 7, 18, 4);
    // the big screen
    const sw = wide ? 360 : 220, sx = (plan.w - sw) / 2, sy = 10, sh = wide ? 58 : 64;
    ctx.fillStyle = "#101418"; rr(sx, sy, sw, sh, 8); ctx.fill();
    const ret = D.equity / D.start_equity - 1;
    textAt(wide ? "PAPER EQUITY" : "EQUITY", sx + 12, sy + px(15), 10, 650, "#8fa3b8", "left");
    textAt("$" + Math.round(D.equity).toLocaleString(), sx + 12, sy + px(34), 17, 750, "#ffffff", "left");
    textAt((ret >= 0 ? "▲ " : "▼ ") + Math.abs(ret * 100).toFixed(2) + "%", sx + 12, sy + px(50), 11.5, 700, ret >= 0 ? "#3ddc84" : "#ff6b6b", "left");
    textAt(wide ? (T.market_open ? "● MARKET OPEN" : "● MARKET CLOSED") : (T.market_open ? "● OPEN" : "● CLOSED"),
           sx + sw - 10, wide ? sy + px(15) : sy + px(50), 10, 750, T.market_open ? "#3ddc84" : "#ffb347", "right");
    const pts = (D.curve || []).slice(-50);
    if (pts.length > 1 && wide) {
      const vs = pts.map(p => p[1]), lo = Math.min(...vs), hi = Math.max(...vs);
      ctx.strokeStyle = ret >= 0 ? "#3ddc84" : "#ff6b6b"; ctx.lineWidth = 2; ctx.beginPath();
      pts.forEach((p, i) => { const x = sx + sw - 150 + i * (136 / (pts.length - 1)), y = sy + sh - 8 - (p[1] - lo) / ((hi - lo) || 1) * 28; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.stroke();
    }
    // the LED ticker under the screen
    const ty = sy + sh + 6, tw = sw, th = 16;
    ctx.fillStyle = "#0b0d10"; rr(sx, ty, tw, th, 4); ctx.fill();
    ctx.save(); ctx.beginPath(); ctx.rect(sx + 4, ty, tw - 8, th); ctx.clip();
    const tick = tickerText, speed = reduce ? 0 : 0.045 + tickerBoost;
    font(10.5, 700); const tl = ctx.measureText(tick).width;
    const off = (t * speed) % (tl + 60);
    ctx.fillStyle = performance.now() < tickerFlash ? "#ffd166" : "#ffb347"; ctx.textAlign = "left";
    ctx.fillText(tick, sx + tw - off, ty + px(12)); ctx.fillText(tick, sx + tw - off + tl + 60, ty + px(12));
    ctx.restore();
    // a framed chart on the wall (wide only)
    if (wide) { ctx.fillStyle = "#6b4b2e"; rr(682, 22, 64, 44, 3); ctx.fill(); ctx.fillStyle = "#fbf7ee"; ctx.fillRect(688, 28, 52, 32);
                ctx.strokeStyle = "#1baf7a"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(691, 54); ctx.lineTo(704, 46); ctx.lineTo(713, 50); ctx.lineTo(736, 32); ctx.stroke(); }
    // whiteboard with the Scanner's notes (wide only)
    if (wide) {
      const [bx, by] = plan.board;
      ctx.fillStyle = "#fdfdfd"; rr(bx, by, 70, 46, 3); ctx.fill(); ctx.strokeStyle = "#9aa3b2"; ctx.lineWidth = 2; ctx.stroke();
      ctx.strokeStyle = "#2a78d6"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(bx + 8, by + 34); ctx.lineTo(bx + 22, by + 20); ctx.lineTo(bx + 32, by + 28); ctx.lineTo(bx + 52, by + 12); ctx.stroke();
      ctx.strokeStyle = "#eb6834"; ctx.beginPath(); ctx.arc(bx + 52, by + 12, 5, 0, 7); ctx.stroke();
      ctx.fillStyle = "#555"; ctx.fillRect(bx + 8, by + 40, 30, 1.5);
    }
    // bookshelf by the professor
    if (!plan.shelf) { /* no room on phones */ }
    else if (plan.shelf[2]) {
      const [sx2, sy2, sw2] = plan.shelf;
      ctx.fillStyle = "#7a5236"; rr(sx2, sy2, sw2, 30, 3); ctx.fill();
      const cols = ["#d03b3b", "#2a78d6", "#1baf7a", "#eda100", "#6250d6", "#eb6834"];
      for (let i = 0, x = sx2 + 6; x < sx2 + sw2 - 10; i++) { const w = 8 + (i * 7) % 6; ctx.fillStyle = cols[i % cols.length]; ctx.fillRect(x, sy2 + 4 + (i % 3), w, 22 - (i % 3)); x += w + 2; }
    } else {
      const [sx2, sy2] = plan.shelf; ctx.fillStyle = "#7a5236"; rr(sx2 - 16, sy2 - 60, 32, 120, 3); ctx.fill();
      const cols = ["#d03b3b", "#2a78d6", "#1baf7a", "#eda100", "#6250d6"];
      for (let i = 0; i < 9; i++) { ctx.fillStyle = cols[i % 5]; ctx.fillRect(sx2 - 12, sy2 - 56 + i * 13, 24 - (i % 3) * 3, 10); }
    }
    // printer (prints a ticket when an order goes out)
    const [pr, pry] = plan.printer;
    ctx.fillStyle = dark ? "#6a6d75" : "#d7dbe2"; rr(pr - 18, pry - 14, 36, 26, 5); ctx.fill();
    ctx.fillStyle = "#3a3e46"; ctx.fillRect(pr - 12, pry - 10, 24, 3);
    prints = prints.filter(pp => t - pp.start < 2600);
    prints.forEach(pp => { const k = Math.min(1, (t - pp.start) / 700); ctx.fillStyle = "#fff"; ctx.fillRect(pr - 9, pry + 10, 18, 14 * k); ctx.fillStyle = "#6250d6"; if (k > .6) ctx.fillRect(pr - 6, pry + 14, 12, 2); });
    textAt("printer", pr, pry + px(32), 9, 600, pal.ink2);
    // water cooler and coffee
    const [wc, wcy] = plan.cooler;
    ctx.fillStyle = "#cfe8ff"; rr(wc - 9, wcy - 34, 18, 18, 6); ctx.fill(); ctx.fillStyle = pal.metal; rr(wc - 11, wcy - 16, 22, 28, 3); ctx.fill();
    if (!reduce && Math.sin(t / 900) > 0.9) circle(wc + 2, wcy - 26 - ((t / 40) % 8), 1.6, "#ffffff");
    const [cx2, cy2] = plan.coffee;
    ctx.fillStyle = dark ? "#55524c" : "#3d3a36"; rr(cx2 - 14, cy2 - 36, 28, 36, 5); ctx.fill();
    ctx.fillStyle = "#c0392b"; ctx.fillRect(cx2 - 8, cy2 - 28, 16, 5);
    if (!reduce) { ctx.strokeStyle = "rgba(200,200,200,.6)"; ctx.lineWidth = 1.5; for (let i = 0; i < 2; i++) { const s = (t / 600 + i * 0.5) % 1; ctx.beginPath(); ctx.moveTo(cx2 - 3 + i * 6, cy2 - 40 - s * 10); ctx.quadraticCurveTo(cx2 + i * 6, cy2 - 46 - s * 10, cx2 - 3 + i * 6, cy2 - 52 - s * 10); ctx.stroke(); } }
    textAt("coffee", cx2, cy2 + px(12), 9.5, 600, pal.ink2);
    // dog bed
    const [bx2, by2] = plan.dogbed;
    ctx.fillStyle = dark ? "#7a3d3d" : "#d98c8c"; ctx.beginPath(); ctx.ellipse(bx2, by2 + 8, 26, 12, 0, 0, 7); ctx.fill();
    ctx.fillStyle = dark ? "#9a5050" : "#f2b1b1"; ctx.beginPath(); ctx.ellipse(bx2, by2 + 8, 19, 8, 0, 0, 7); ctx.fill();
    // plants
    plan.plants.forEach(([x, y], i) => plant(x, y, i, t));
  }
  function plant(x, y, i, t) {
    ctx.fillStyle = pal.pot; rr(x - 10, y - 4, 20, 17, 3); ctx.fill();
    const sway = reduce ? 0 : Math.sin(t / 1400 + i) * 1.5;
    [[-9, -10, 9, pal.plant], [8, -12, 9, pal.plant2], [sway, -22, 10, pal.plant], [-3 + sway, -14, 7, pal.plant2]].forEach(([dx, dy, r, c]) => circle(x + dx, y + dy, r, c));
  }

  // ----- desks and their props -----
  function deskBox(id) {
    const [x, y, face] = plan.desks[id], horiz = face === "up" || face === "down";
    return {x, y, face, horiz, dw: horiz ? 128 : 72, dh: horiz ? 56 : 108};
  }
  function drawChair(id) {
    const s = seat(id), big = id === "overseer";
    ctx.fillStyle = big ? "#6b3a2a" : pal.chair; rr(s[0] - (big ? 19 : 15), s[1] - (big ? 20 : 14), big ? 38 : 30, big ? 38 : 30, 9); ctx.fill();
    ctx.fillStyle = "rgba(0,0,0,.12)"; rr(s[0] - 15, s[1] + 10, 30, 6, 3); ctx.fill();
  }
  function drawDesk(id, t) {
    const a = byId[id], {x, y, face, horiz, dw, dh} = deskBox(id);
    const working = a.status === "working", off = a.status === "off";
    if (night && working) { const g = ctx.createRadialGradient(x, y, 4, x, y, 110); g.addColorStop(0, "rgba(255,214,120,.35)"); g.addColorStop(1, "rgba(255,214,120,0)"); ctx.fillStyle = g; ctx.fillRect(x - 110, y - 110, 220, 220); }
    ctx.fillStyle = pal.shadow; rr(x - dw / 2 + 3, y - dh / 2 + 6, dw, dh, 9); ctx.fill();
    ctx.fillStyle = id === "broker" ? "#5b6b73" : pal.desk; rr(x - dw / 2, y - dh / 2, dw, dh, 9); ctx.fill();
    ctx.fillStyle = id === "broker" ? "#7d8f99" : pal.deskTop; rr(x - dw / 2 + 3, y - dh / 2 + 3, dw - 6, dh - 6, 7); ctx.fill();
    // monitor, facing the person
    const mw = horiz ? 52 : 14, mh = horiz ? 11 : 48;
    const mx = {down: x - mw / 2, up: x - mw / 2, right: x - dw / 2 + 6, left: x + dw / 2 - 20}[face];
    const my = {down: y - dh / 2 + 4, up: y + dh / 2 - 17, right: y - mh / 2, left: y - mh / 2}[face];
    if (id !== "broker") {
      ctx.fillStyle = "#1b1d22"; rr(mx - 2, my - 2, mw + 4, mh + 4, 3); ctx.fill();
      ctx.fillStyle = off ? pal.screenOff : working ? `hsl(${150 + 20 * Math.sin(t / 300)}, 70%, ${night ? 45 : 55}%)` : "#3a6ea5";
      rr(mx, my, mw, mh, 2); ctx.fill();
      if (working && !reduce) {
        ctx.strokeStyle = "rgba(255,255,255,.85)"; ctx.lineWidth = 1.2; ctx.beginPath();
        for (let i = 0; i <= 8; i++) { const v = Math.sin(t / 250 + i * 1.3 + x) * 0.35 + 0.5; const px_ = horiz ? mx + i * mw / 8 : mx + v * mw, py_ = horiz ? my + v * mh : my + i * mh / 8; i ? ctx.lineTo(px_, py_) : ctx.moveTo(px_, py_); }
        ctx.stroke();
      }
    }
    deskProps(id, x, y, face, horiz, dw, dh, t, working);
    if (off) {
      const word = id === "broker" ? (horiz ? "SIMULATED" : "SIM") : "AWAY";
      font(10, 750); const w = ctx.measureText(word).width + px(12);
      ctx.fillStyle = "#fff8e1"; rr(x - w / 2, y - px(10), w, px(20), 4); ctx.fill(); ctx.strokeStyle = "#b58b00"; ctx.lineWidth = 1; ctx.stroke();
      textAt(word, x, y + px(3.5), 10, 750, "#6b5200");
    }
  }
  function deskProps(id, x, y, face, horiz, dw, dh, t, working) {
    const side = horiz ? [x + dw / 2 - 22, y] : [x, y + dh / 2 - 20];
    const flick = n => Math.sin(t / 130 + n * 2.1) > 0.2;
    if (id === "data") {   // a server rack with blinking lights
      const rx = horiz ? x - dw / 2 - 26 : x + (face === "right" ? 44 : -64), ry = horiz ? y - 36 : y - 50;
      ctx.fillStyle = "#2b2f38"; rr(rx, ry, 20, horiz ? 72 : 52, 3); ctx.fill();
      for (let i = 0; i < (horiz ? 8 : 6); i++) { circle(rx + 6, ry + 6 + i * 8, 1.8, flick(i) ? "#3ddc84" : "#1d5c36"); circle(rx + 13, ry + 6 + i * 8, 1.8, flick(i + 9) ? "#2a78d6" : "#1c3a66"); }
    }
    if (id === "news") {   // a little TV running headlines
      const [tx, tyy] = side; ctx.fillStyle = "#222"; rr(tx - 16, tyy - 12, 32, 22, 3); ctx.fill();
      ctx.fillStyle = "#c0392b"; ctx.fillRect(tx - 13, tyy - 9, 26, 7); textAt("NEWS", tx, tyy - 3.5, 6.5, 800, "#fff");
      ctx.fillStyle = "#ffd166"; ctx.fillRect(tx - 13 + ((t / 25) % 26), tyy + 2, 6, 3);
    }
    if (id === "regime") {   // the weather sign: today's market weather
      const r0 = regimes[0];
      const [wx, wy] = horiz ? [x - dw / 2 + 22, y] : [x, y - dh / 2 + 22];
      weather(wx, wy, r0 ? r0.trend : "unknown", r0 ? r0.volatility : "normal", t, 1);
    }
    if (id === "scanner") {  // a telescope
      const [tx, tyy] = side; ctx.strokeStyle = "#6b4b2e"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(tx - 6, tyy + 10); ctx.lineTo(tx, tyy); ctx.lineTo(tx + 6, tyy + 10); ctx.stroke();
      ctx.save(); ctx.translate(tx, tyy); ctx.rotate(-0.6 + (reduce ? 0 : Math.sin(t / 900) * 0.25)); ctx.fillStyle = "#c8a15a"; rr(-4, -16, 8, 20, 3); ctx.fill(); ctx.restore();
    }
    if (id === "risk") {     // rule book and a whistle stand
      const [tx, tyy] = side; ctx.fillStyle = "#1f3b73"; rr(tx - 10, tyy - 8, 20, 15, 2); ctx.fill(); textAt("RULES", tx, tyy + 2, 6, 800, "#fff");
    }
    if (id === "overseer") { // a nameplate with Claude's sparkle
      const [tx, tyy] = side; ctx.fillStyle = "#3a2a1a"; rr(tx - 14, tyy - 5, 28, 10, 2); ctx.fill(); sparkle(tx, tyy, 4.5, "#d97757", t);
    }
    if (id === "execution") { // two phones
      const [tx, tyy] = side; ctx.fillStyle = "#222"; rr(tx - 12, tyy - 6, 10, 14, 2); ctx.fill(); rr(tx + 2, tyy - 6, 10, 14, 2); ctx.fill();
      if (working && Math.sin(t / 160) > 0) circle(tx - 7, tyy - 9, 2, "#3ddc84");
    }
    if (id === "broker") {   // the exchange window and its bell
      textAt("EXCHANGE", x, y + (horiz ? 4 : -30), 9.5, 800, "#ffffff");
      const [bx, by] = horiz ? [x + 40, y] : [x, y + 30];
      ctx.fillStyle = "#d4a24c"; ctx.beginPath(); ctx.arc(bx, by + 3, 7, Math.PI, 0); ctx.fill(); ctx.fillRect(bx - 9, by + 3, 18, 3); circle(bx, by - 5, 1.8, "#d4a24c");
    }
    if (id === "learning") { // chalkboard and books
      const [cbx, cby] = horiz ? [x, y + dh / 2 + 52] : [x + (face === "left" ? 78 : -78), y];
      if (horiz) { ctx.fillStyle = "#2f4a3a"; rr(cbx - 46, cby + 8, 92, 30, 3); ctx.fill(); ctx.strokeStyle = "#e8e2d0"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(cbx - 38, cby + 32); ctx.lineTo(cbx - 22, cby + 20); ctx.lineTo(cbx - 10, cby + 26); ctx.lineTo(cbx + 6, cby + 14); ctx.stroke(); textAt("E[R] > 0 ?", cbx + 26, cby + 26, 7.5, 600, "#e8e2d0"); }
      ["#9085e9", "#eb6834", "#1baf7a"].forEach((c, i) => { ctx.fillStyle = c; ctx.fillRect(side[0] - 8 + i * 6, side[1] - 9, 5, 17); });
    }
    if (id === "you") {      // in-tray that fills with alerts, and a mug
      const [tx, tyy] = side, n = Math.min(4, T.inbox || 0);
      ctx.fillStyle = "#8a5a2b"; rr(tx - 12, tyy - 6, 24, 14, 2); ctx.fill();
      for (let i = 0; i < n; i++) { ctx.fillStyle = "#fff"; ctx.fillRect(tx - 9, tyy - 8 - i * 3, 18, 3); }
      const [mx2, my2] = horiz ? [x - dw / 2 + 18, y] : [x, y - dh / 2 + 16];
      ctx.fillStyle = "#e85d75"; rr(mx2 - 5, my2 - 6, 10, 12, 2); ctx.fill(); ctx.strokeStyle = "#e85d75"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(mx2 + 6, my2, 3, -1.2, 1.2); ctx.stroke();
    }
  }
  function weather(x, y, trend, vol, t, s) {
    const bob = reduce ? 0 : Math.sin(t / 500) * 2;
    y += bob;
    ctx.fillStyle = "rgba(255,255,255,.9)"; rr(x - 17 * s, y - 17 * s, 34 * s, 34 * s, 8); ctx.fill();
    if (trend === "trending_up") { circle(x, y, 8 * s, "#ffb703"); ctx.strokeStyle = "#ffb703"; ctx.lineWidth = 2; for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2 + (reduce ? 0 : t / 2000); ctx.beginPath(); ctx.moveTo(x + Math.cos(a) * 10 * s, y + Math.sin(a) * 10 * s); ctx.lineTo(x + Math.cos(a) * 14 * s, y + Math.sin(a) * 14 * s); ctx.stroke(); } }
    else if (trend === "trending_down") { cloud(x, y - 3, s, "#8a9bb0"); ctx.strokeStyle = "#2a78d6"; ctx.lineWidth = 1.6; for (let i = 0; i < 3; i++) { const k = ((t / 300) + i / 3) % 1; ctx.beginPath(); ctx.moveTo(x - 6 + i * 6, y + 5 + k * 8); ctx.lineTo(x - 8 + i * 6, y + 9 + k * 8); ctx.stroke(); } }
    else if (trend === "ranging") { circle(x + 5, y - 5, 6 * s, "#ffb703"); cloud(x - 2, y + 2, s * 0.9, "#c7d2de"); }
    else if (trend === "transition") { ctx.strokeStyle = "#7aa6c2"; ctx.lineWidth = 2; for (let i = 0; i < 3; i++) { ctx.beginPath(); const o = reduce ? 0 : Math.sin(t / 300 + i) * 2; ctx.moveTo(x - 11, y - 6 + i * 6); ctx.quadraticCurveTo(x + o, y - 10 + i * 6, x + 11, y - 6 + i * 6); ctx.stroke(); } }
    else { textAt("?", x, y + 5, 14, 800, "#8a9bb0"); }
    if (vol === "high") { ctx.fillStyle = "#ffd166"; ctx.beginPath(); ctx.moveTo(x + 9, y - 14); ctx.lineTo(x + 3, y - 3); ctx.lineTo(x + 8, y - 3); ctx.lineTo(x + 4, y + 6); ctx.lineTo(x + 13, y - 6); ctx.lineTo(x + 8, y - 6); ctx.fill(); }
  }
  function cloud(x, y, s, c) { [[-6, 0, 6], [0, -4, 7], [6, 0, 6], [0, 2, 6]].forEach(([dx, dy, r]) => circle(x + dx * s, y + dy * s, r * s, c)); }
  function sparkle(x, y, r, c, t) {
    const k = reduce ? 1 : 1 + Math.sin(t / 300) * 0.15; ctx.fillStyle = c; ctx.beginPath();
    for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4, rad = (i % 2 ? r * 0.35 : r) * k; ctx.lineTo(x + Math.cos(a) * rad, y + Math.sin(a) * rad); }
    ctx.closePath(); ctx.fill();
  }

  // ----- the characters -----
  function drawPerson(p, t) {
    const id = p.id, a = byId[id], walking = p.path.length > 0, face = plan.desks[id][2];
    const back = p.seated && !walking && face === "up";
    const working = a.status === "working";
    const bob = walking ? Math.abs(Math.sin(t / 110 + p.phase)) * 3 : Math.sin(t / (working ? 160 : 900) + p.phase) * (working ? 0.9 : 0.7);
    const S = 1.18;
    ctx.save(); ctx.translate(p.x, p.y - bob - p.hop); ctx.scale(p.facing < 0 && walking ? -S : S, S);
    // shadow
    ctx.fillStyle = pal.shadow; ctx.beginPath(); ctx.ellipse(0, 18 + (bob + p.hop) / S, 13, 4.5, 0, 0, 7); ctx.fill();
    const C = {data: ["#9aa8bd", "#9aa8bd"], news: ["#c8b08a", "#d9b892"], regime: ["#1baf7a", "#e8b48f"], scanner: ["#b8a271", "#f3d2b5"],
               risk: ["stripes", "#a8714e"], overseer: ["#26324a", "#f0c9a5"], execution: ["#ff7a3d", "#d9a07a"], broker: ["#3f6b4f", "#eec3a0"],
               learning: ["#7b5a3a", "#e6c2a0"], you: ["#5b6cc0", "#efc6a2"]}[id] || ["#888", "#eee"];
    const [shirt, skin] = C;
    // legs
    if (!p.seated || walking) { const sw = walking ? Math.sin(t / 110 + p.phase) * 5 : 0; ctx.fillStyle = id === "data" ? "#5d6878" : "#2f3640"; rr(-7 + sw * .4, 6, 5.5, 13, 3); ctx.fill(); rr(1.5 - sw * .4, 6, 5.5, 13, 3); ctx.fill(); }
    // body
    if (shirt === "stripes") { ctx.save(); rr(-12, -10, 24, 22, 9); ctx.clip(); ctx.fillStyle = "#fff"; ctx.fillRect(-12, -10, 24, 22); ctx.fillStyle = "#111"; for (let i = -12; i < 12; i += 6) ctx.fillRect(i, -10, 3, 22); ctx.restore(); }
    else { ctx.fillStyle = shirt; rr(-12, -10, 24, 22, 9); ctx.fill(); }
    // arms
    const typing = p.seated && !walking && working && !reduce;
    const k = typing ? Math.sin(t / 70 + p.phase) * 2 : 0;
    const armC = shirt === "stripes" ? "#fff" : shirt;
    if (id === "execution" && working && !walking) { ctx.fillStyle = armC; rr(-17, -6 + k, 6, 12, 3); ctx.fill(); rr(9, -18, 6, 12, 3); ctx.fill(); }   // phone to the ear
    else if (id === "scanner" && !back) { ctx.fillStyle = armC; rr(-14, -16, 6, 12, 3); ctx.fill(); rr(8, -16, 6, 12, 3); ctx.fill(); }               // holding binoculars
    else { ctx.fillStyle = armC; rr(-17, -6 + k, 6, 12, 3); ctx.fill(); rr(11, -6 - k, 6, 12, 3); ctx.fill(); }
    // outfit details
    if (id === "news") { ctx.fillStyle = "#a88e66"; ctx.fillRect(-1, -10, 2, 22); ctx.fillStyle = "#6b5a40"; ctx.fillRect(-12, 2, 24, 3); }
    if (id === "overseer") { ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(-4, -10); ctx.lineTo(4, -10); ctx.lineTo(0, -2); ctx.fill(); ctx.fillStyle = "#d97757"; ctx.beginPath(); ctx.moveTo(-2, -6); ctx.lineTo(2, -6); ctx.lineTo(0, 6); ctx.fill(); sparkle(-7, -3, 3, "#d97757", t); }
    if (id === "execution") { ctx.fillStyle = "#fff"; rr(2, -6, 9, 7, 1); ctx.fill(); textAt("42", 6.5, -0.8, 5.5, 900, "#111"); }
    if (id === "broker") { ctx.fillStyle = "#fff"; ctx.fillRect(-3, -10, 6, 22); ctx.fillStyle = "#c0392b"; ctx.fillRect(-1.5, -9, 3, 9); }
    if (id === "regime") { ctx.fillStyle = "#e85d75"; ctx.beginPath(); ctx.moveTo(0, -8); ctx.lineTo(-5, -11); ctx.lineTo(-5, -5); ctx.fill(); ctx.beginPath(); ctx.moveTo(0, -8); ctx.lineTo(5, -11); ctx.lineTo(5, -5); ctx.fill(); }
    if (id === "learning") { ctx.fillStyle = "#5c4129"; ctx.fillRect(-12, -10, 4, 22); ctx.fillRect(8, -10, 4, 22); }
    if (id === "you") { ctx.fillStyle = "#4a59a8"; rr(-6, 0, 12, 7, 2); ctx.fill(); }
    if (id === "risk") { ctx.strokeStyle = "#ddd"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(-5, -10); ctx.lineTo(0, -1); ctx.lineTo(5, -10); ctx.stroke(); ctx.fillStyle = "#c0c4cc"; rr(-3, -2, 6, 4, 1); ctx.fill(); }
    // head
    if (id === "data") {
      ctx.fillStyle = "#b9c3d2"; rr(-11, -32, 22, 20, 6); ctx.fill(); ctx.fillStyle = "#0e2238"; rr(-8, -29, 16, 13, 4); ctx.fill();
      const eyeC = working ? "#3ddc84" : "#7fc8ff", blink = Math.sin(t / 1500 + p.phase) > 0.97;
      if (!back) { ctx.fillStyle = eyeC; if (blink) { ctx.fillRect(-6, -23, 4, 1.5); ctx.fillRect(2, -23, 4, 1.5); } else { rr(-6, -26, 4, 5, 1.5); ctx.fill(); rr(2, -26, 4, 5, 1.5); ctx.fill(); } }
      ctx.strokeStyle = "#8a93a3"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(0, -32); ctx.lineTo(0, -38); ctx.stroke();
      circle(0, -39, 2.4, Math.sin(t / 250) > 0 ? "#ff5a5a" : "#7a2a2a");
    } else {
      circle(0, -20, 10, skin);
      const hair = {news: "#1f1a17", regime: "#7a4b2a", scanner: "#c8a15a", risk: "#24180f", overseer: "#5a3a26", execution: "#2b1d14", broker: "#9a9a9a", learning: "#e8e8e8", you: "#4b3426"}[id] || "#333";
      ctx.fillStyle = hair;
      if (back) { ctx.beginPath(); ctx.arc(0, -21, 10.5, 0, 7); ctx.fill(); }
      else { ctx.beginPath(); ctx.arc(0, -23, 10.3, Math.PI * 1.02, Math.PI * 1.98); ctx.fill(); }
      if (!back) {
        const blink = Math.sin(t / 1700 + p.phase * 3) > 0.985;
        if (id === "scanner") { ctx.fillStyle = "#2b2b2b"; circle(-4, -20, 4, "#2b2b2b"); circle(4, -20, 4, "#2b2b2b"); const sc = reduce ? 0 : Math.sin(t / 700) * 1.2; circle(-4 + sc, -20, 2, "#7fc8ff"); circle(4 + sc, -20, 2, "#7fc8ff"); }
        else if (!blink) { circle(-3.5, -19, 1.3, "#1a1a1a"); circle(3.5, -19, 1.3, "#1a1a1a"); }
        if (id === "learning" || id === "regime") { ctx.strokeStyle = "#1a1a1a"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(-3.5, -19, 3, 0, 7); ctx.moveTo(6.5, -19); ctx.arc(3.5, -19, 3, 0, 7); ctx.stroke(); }
        const mood = a.status === "problem" ? -1 : a.status === "paused" ? -0.4 : 1;
        ctx.strokeStyle = "#7a3e2a"; ctx.lineWidth = 1.1; ctx.beginPath();
        if (mood > 0) ctx.arc(0, -16, 3, 0.2, Math.PI - 0.2); else ctx.arc(0, -11.5, 3, Math.PI + 0.4, -0.4);
        ctx.stroke();
        if (id === "learning") { ctx.fillStyle = "#e8e8e8"; ctx.beginPath(); ctx.arc(0, -14, 6, 0.1, Math.PI - 0.1); ctx.fill(); }
      }
      // hats
      if (id === "news") { ctx.fillStyle = "#3b3226"; ctx.beginPath(); ctx.ellipse(0, -27, 15, 3.5, 0, 0, 7); ctx.fill(); rr(-8, -36, 16, 10, 4); ctx.fill(); ctx.fillStyle = "#a33"; ctx.fillRect(-8, -29, 16, 2.5); ctx.fillStyle = "#fff"; ctx.fillRect(2, -35, 5, 4); }
      if (id === "scanner") { ctx.fillStyle = "#c9b27a"; ctx.beginPath(); ctx.ellipse(0, -27, 15, 4, 0, 0, 7); ctx.fill(); ctx.beginPath(); ctx.arc(0, -28, 8, Math.PI, 0); ctx.fill(); ctx.fillStyle = "#6b5a40"; ctx.fillRect(-8, -29, 16, 2); }
      if (id === "risk") { ctx.fillStyle = "#111"; ctx.beginPath(); ctx.arc(0, -25, 10.5, Math.PI, 0); ctx.fill(); rr(-2, -26, 14, 3, 1.5); ctx.fill(); }
      if (id === "execution") { ctx.fillStyle = "#222"; ctx.lineWidth = 2; ctx.strokeStyle = "#222"; ctx.beginPath(); ctx.arc(0, -21, 11.5, Math.PI * 1.05, Math.PI * 1.95); ctx.stroke(); }
      if (id === "broker") { ctx.fillStyle = "rgba(30,160,90,.75)"; ctx.beginPath(); ctx.ellipse(3, -28, 12, 3.5, -0.1, 0, 7); ctx.fill(); }
      if (id === "you") { ctx.strokeStyle = "#5b6cc0"; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(0, -20, 12, Math.PI * 0.75, Math.PI * 2.25); ctx.stroke(); }
    }
    // things held
    if (!walking && !back) {
      if (id === "news") { ctx.fillStyle = "#f5f5f0"; rr(8, -7, 13, 16, 1); ctx.fill(); ctx.fillStyle = "#999"; ctx.fillRect(10, -4, 9, 1.2); ctx.fillRect(10, -1, 9, 1.2); ctx.fillRect(10, 2, 6, 1.2); }
      if (id === "learning") { ctx.fillStyle = "#9085e9"; rr(-9, -3, 18, 11, 2); ctx.fill(); ctx.fillStyle = "#fff"; ctx.fillRect(-0.5, -3, 1, 11); }
      if (id === "regime") { ctx.strokeStyle = "#5c4129"; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(14, 2); ctx.lineTo(24, -16); ctx.stroke(); }
      if (id === "overseer" && p.thinkUntil > performance.now()) { ctx.fillStyle = "#6b4b2e"; rr(9, -10, 10, 12, 2); ctx.fill(); }
    }
    if (id === "risk") {   // the referee's card: green go, red veto, yellow paused/smaller
      const card = p.card && p.card.until > performance.now() ? p.card.color : a.status === "paused" ? "#f5c518" : null;
      if (card) { ctx.fillStyle = armC; rr(11, -24, 6, 14, 3); ctx.fill(); ctx.fillStyle = card; ctx.save(); ctx.translate(16, -30); ctx.rotate(0.15); rr(-4, -8, 9, 12, 1.5); ctx.fill(); ctx.restore(); }
    }
    if (walking && p.carry) {
      ctx.fillStyle = css(p.carry); rr(10, -6, 15, 11, 2); ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(10, -6); ctx.lineTo(17.5, 0); ctx.lineTo(25, -6); ctx.stroke();
    }
    ctx.restore();
    const hx = p.x, hy = p.y - bob - p.hop - 46;
    if (a.status === "problem") badge(hx + 14, hy + 4, "!", css("--critical"), t);
    if (a.status === "paused" && id !== "risk") badge(hx + 14, hy + 4, "II", css("--warning"), t);
    if (p.thinkUntil > performance.now()) thought(hx, hy - 6, t);
    else if (working && p.seated && !walking && !reduce && !p.emote) typingDots(hx + 18, hy + 6, t);
    if (night && a.status === "idle" && p.seated && !walking && !reduce) zzz(hx + 12, hy + 4, t);
    if (selected === id) { ctx.strokeStyle = pal.ink; ctx.lineWidth = 2; ctx.beginPath(); ctx.ellipse(p.x, p.y + 21, 21, 7, 0, 0, 7); ctx.stroke(); }
  }
  function badge(x, y, text, color, t) {
    const s = 1 + (reduce ? 0 : Math.sin(t / 200) * 0.08);
    circle(x, y, 8 * s, color);
    ctx.fillStyle = "#fff"; ctx.font = `800 ${10 * s}px system-ui, sans-serif`; ctx.textAlign = "center"; ctx.fillText(text, x, y + 3.5 * s);
  }
  function typingDots(x, y, t) {
    const n = Math.floor(t / 400) % 4;
    ctx.fillStyle = pal.bubble; rr(x - 13, y - 7, 26, 14, 7); ctx.fill();
    for (let i = 0; i < 3; i++) { ctx.globalAlpha = i < n ? 1 : 0.3; circle(x - 6 + i * 6, y, 1.8, "#888"); }
    ctx.globalAlpha = 1;
  }
  function thought(x, y, t) {
    circle(x + 6, y + 14, 2.5, pal.bubble); circle(x + 10, y + 7, 4, pal.bubble);
    ctx.fillStyle = pal.bubble; rr(x - 6, y - 26, 44, 26, 13); ctx.fill();
    sparkle(x + 16, y - 13, 8, "#d97757", t);
  }
  function zzz(x, y, t) {
    const k = (t / 1500) % 1;
    ["z", "z", "Z"].forEach((c, i) => { const kk = (k + i / 3) % 1; ctx.globalAlpha = 1 - kk; textAt(c, x + kk * 10 + i * 3, y - kk * 18, 9 + i * 2, 800, pal.ink2); });
    ctx.globalAlpha = 1;
  }

  // ----- the watchdog -----
  function drawDog(t) {
    const d = dog, walking = d.path.length > 0, a = byId.watchdog;
    const bark = performance.now() < d.barkUntil;
    const shake = bark && !reduce ? Math.sin(t / 30) * 1.5 : 0;
    const x = d.x + shake, y = d.y - (walking ? Math.abs(Math.sin(t / 90)) * 2 : 0) - d.hop, f = d.facing;
    const sleeping = night && d.sitting && !walking && Math.hypot(d.x - plan.dogbed[0], d.y - plan.dogbed[1]) < 4;
    ctx.save(); ctx.translate(x, y); ctx.scale(1.25, 1.25);
    ctx.fillStyle = pal.shadow; ctx.beginPath(); ctx.ellipse(0, 9, 16, 4, 0, 0, 7); ctx.fill();
    if (sleeping) {
      ctx.fillStyle = "#c98b4a"; ctx.beginPath(); ctx.ellipse(0, 2, 15, 8, 0, 0, 7); ctx.fill(); circle(11 * f, -1, 7, "#c98b4a");
      ctx.fillStyle = "#8a5a2b"; ctx.beginPath(); ctx.ellipse(8 * f, -5, 3, 5, f * 0.8, 0, 7); ctx.fill();
      ctx.strokeStyle = "#1a1a1a"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(13 * f, -2, 1.8, 0, Math.PI); ctx.stroke();
      ctx.restore(); zzz(x + 10, y - 16, t);
    } else {
      ctx.fillStyle = "#c98b4a";
      const leg = walking ? Math.sin(t / 90) * 3 : 0;
      [[-9, leg], [-4, -leg], [5, leg], [10, -leg]].forEach(([dx, kk]) => { rr(dx * f - 2, 2, 4, 9 + (d.sitting && !walking && dx < 0 ? -3 : 0) + kk * 0.2, 2); ctx.fill(); });
      rr(-14, -7, 28, 13, 7); ctx.fill();
      ctx.fillStyle = "#e0b07a"; ctx.beginPath(); ctx.ellipse(-2 * f, -1, 6, 4, 0, 0, 7); ctx.fill();   // patch
      const sniff = walking && !reduce ? Math.sin(t / 60) * 1 : 0;
      circle(15 * f, -10 + sniff, 8, "#c98b4a");
      ctx.fillStyle = "#8a5a2b"; ctx.beginPath(); ctx.ellipse(12 * f, -16, 3, 6, f * (bark ? -0.2 : 0.5), 0, 7); ctx.fill();
      circle(18 * f, -11 + sniff, 1.4, "#1a1a1a"); circle(23 * f, -8 + sniff, 2, "#1a1a1a");
      if (bark) { ctx.fillStyle = "#7a2a2a"; ctx.beginPath(); ctx.arc(21 * f, -5, 3, 0, Math.PI); ctx.fill(); }
      const wag = Math.sin(t / (walking ? 60 : 160)) * 0.6;
      ctx.strokeStyle = "#c98b4a"; ctx.lineWidth = 3; ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(-13 * f, -4);
      ctx.lineTo(-13 * f - Math.cos(wag) * 9 * f, -4 - Math.sin(wag + 0.8) * 9); ctx.stroke(); ctx.lineCap = "butt";
      ctx.fillStyle = "#2a78d6"; ctx.beginPath(); ctx.arc(15 * f, -17, 6, Math.PI, 0); ctx.fill(); rr(15 * f - (f < 0 ? 9 : 0), -18, 9, 2.5, 1); ctx.fill();
      ctx.fillStyle = "#d03b3b"; ctx.fillRect(9 * f - 3, -6, 6, 2.5); circle(9 * f, -2.5, 1.8, "#ffd166");   // collar and tag
      ctx.restore();
    }
    if (a.status === "problem") badge(x + 10, y - 34, "!", css("--critical"), t);
    font(11, 650); const w = ctx.measureText("Watchdog").width + px(12);
    ctx.fillStyle = pal.plate; rr(x - w / 2, y + 18, w, px(17), px(6)); ctx.fill();
    if (selected === "watchdog") { ctx.strokeStyle = pal.ink; ctx.lineWidth = px(2); ctx.stroke(); }
    textAt("Watchdog", x, y + 18 + px(12.5), 11, 650, pal.ink);
  }

  // ----- name tags: who, how they are, what they're up to -----
  function fit(text, maxW) {
    if (ctx.measureText(text).width <= maxW) return text;
    while (text.length > 1 && ctx.measureText(text + "…").width > maxW) text = text.slice(0, -1);
    return text + "…";
  }
  function namePlate(id) {
    const a = byId[id], {x, y, face} = deskBox(id);
    const maxW = plan.aisle === "h" ? 172 : 200;
    font(12.5, 700); const name = fit(a.name, maxW - px(16)); const w1 = ctx.measureText(name).width;
    font(10.5, 600); const state = fit(STATE[a.status] || a.status, maxW - px(30)); const w2 = ctx.measureText(state).width + px(12);
    font(10.5, 500); const short = fit(a.short || "", maxW - px(16)); const w3 = ctx.measureText(short).width;
    const w = Math.min(maxW, Math.max(w1, w2, w3) + px(18)), h = px(short ? 50 : 36);
    let py = {down: y + 36, up: y - 36 - h, right: y + 60, left: y + 60}[face];
    const bx = Math.max(4, Math.min(plan.w - w - 4, x - w / 2)), cx = bx + w / 2;
    ctx.fillStyle = pal.plate; rr(bx, py, w, h, px(9)); ctx.fill();
    ctx.strokeStyle = selected === id ? pal.ink : "rgba(0,0,0,.07)"; ctx.lineWidth = px(selected === id ? 2 : 1); ctx.stroke();
    textAt(name, cx, py + px(15), 12.5, 700, pal.ink);
    const sx = cx - w2 / 2;
    circle(sx + px(4), py + px(25.5), px(4), css(STATE_COLOR[a.status] || "--axis"));
    textAt(state, sx + px(12), py + px(29.5), 10.5, 600, pal.ink2, "left");
    if (short) textAt(short, cx, py + px(43), 10.5, 500, pal.ink2);
  }

  // ----- speech bubbles -----
  function drawBubble(b, now) {
    const who = b.who === "watchdog" ? dog : people[b.who];
    if (!who) return;
    font(12, 500);
    const maxW = px(210), words = b.text.split(" "), lines = [];
    let line = "";
    words.forEach(w => { const test = line ? line + " " + w : w; if (ctx.measureText(test).width > maxW && line) { lines.push(line); line = w; } else line = test; });
    if (line) lines.push(line);
    const shown = lines.slice(0, 4); if (lines.length > 4) shown[3] = shown[3].replace(/.{0,2}$/, "…");
    const lh = px(15), padX = px(10), padY = px(8);
    const bw = Math.min(maxW, Math.max(...shown.map(l => ctx.measureText(l).width))) + padX * 2 + px(8);
    const bh = shown.length * lh + padY * 2 + px(13);
    let bx = who.x - bw / 2, by = who.y - 56 - bh;
    bx = Math.max(6, Math.min(plan.w - bw - 6, bx)); by = Math.max(plan.wall + 4, by);
    ctx.globalAlpha = Math.max(0, Math.min(1, (b.until - now) / 300, (now - b.start) / 150));
    const pop = reduce ? 1 : Math.min(1, 0.85 + (now - b.start) / 800);
    ctx.save(); ctx.translate(who.x, by + bh); ctx.scale(pop, pop); ctx.translate(-who.x, -(by + bh));
    ctx.fillStyle = "rgba(0,0,0,.18)"; rr(bx + 2, by + 3, bw, bh, px(10)); ctx.fill();
    ctx.fillStyle = pal.bubble; rr(bx, by, bw, bh, px(10)); ctx.fill();
    ctx.fillStyle = css(b.color); rr(bx, by, px(5), bh, [px(10), 0, 0, px(10)]); ctx.fill();
    ctx.fillStyle = pal.bubble; ctx.beginPath(); ctx.moveTo(who.x - 7, by + bh - 1); ctx.lineTo(who.x + 7, by + bh - 1); ctx.lineTo(who.x, by + bh + 9); ctx.fill();
    textAt(b.title.toUpperCase(), bx + padX + px(4), by + padY + px(9), 10.5, 750, css(b.color), "left");
    font(12, 500); ctx.fillStyle = pal.bubbleInk;
    shown.forEach((l, i) => ctx.fillText(l, bx + padX + px(4), by + padY + px(13) + lh * (i + 1) - px(3)));
    ctx.restore(); ctx.globalAlpha = 1;
  }

  // ----- effects: confetti, coins, rain, sparks, floating words, emotes -----
  function fx(type, x, y, opts = {}) {
    if (reduce && type !== "text") return;
    const now = performance.now();
    if (type === "confetti" || type === "coins" || type === "spark") {
      const n = type === "spark" ? 10 : 26;
      for (let i = 0; i < n; i++) effects.push({type, x, y, vx: (Math.random() - .5) * (type === "spark" ? 120 : 180), vy: -60 - Math.random() * 140,
        c: type === "coins" ? "#ffd166" : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"][i % 6], start: now, dur: 1300 + Math.random() * 500, r: Math.random() * 6});
    } else effects.push({type, x, y, start: now, dur: opts.dur || 1800, ...opts});
  }
  function drawEffects(now) {
    effects = effects.filter(e => now - e.start < e.dur);
    effects.forEach(e => {
      const k = (now - e.start) / e.dur, s = (now - e.start) / 1000;
      ctx.globalAlpha = Math.max(0, 1 - k * k);
      if (e.type === "confetti" || e.type === "spark") { const x = e.x + e.vx * s, y = e.y + e.vy * s + 160 * s * s; ctx.save(); ctx.translate(x, y); ctx.rotate(e.r + s * 8); ctx.fillStyle = e.c; ctx.fillRect(-3, -1.5, e.type === "spark" ? 3 : 6, 3); ctx.restore(); }
      else if (e.type === "coins") { const x = e.x + e.vx * s, y = e.y + e.vy * s + 200 * s * s; circle(x, y, 4, "#ffd166"); circle(x, y, 2.5, "#f2b705"); }
      else if (e.type === "text") { textAt(e.text, e.x, e.y - k * 40, e.size || 15, 850, e.color || pal.ink); }
      else if (e.type === "rain") { cloud(e.x, e.y - 50, 1.6, "#8a9bb0"); ctx.strokeStyle = "#2a78d6"; ctx.lineWidth = 1.6; for (let i = 0; i < 5; i++) { const kk = ((now / 280) + i / 5) % 1; ctx.beginPath(); ctx.moveTo(e.x - 12 + i * 6, e.y - 42 + kk * 22); ctx.lineTo(e.x - 14 + i * 6, e.y - 36 + kk * 22); ctx.stroke(); } }
      else if (e.type === "emote") emote(e.kind, e.x, e.y - Math.sin(Math.min(1, k * 4) * Math.PI / 2) * 10, now);
      ctx.globalAlpha = 1;
    });
    if (now < flashUntil) { ctx.globalAlpha = (flashUntil - now) / 400 * 0.5; ctx.fillStyle = flashColor; ctx.fillRect(0, 0, plan.w, plan.h); ctx.globalAlpha = 1; }
  }
  function emote(kind, x, y, t) {
    ctx.fillStyle = pal.bubble; rr(x - 14, y - 14, 28, 28, 14); ctx.fill();
    if (kind === "bulb") { circle(x, y - 2, 7, "#ffd166"); ctx.fillStyle = "#9aa3b2"; ctx.fillRect(x - 3.5, y + 4, 7, 5); ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 1.5; for (let i = 0; i < 5; i++) { const a = -Math.PI + i * Math.PI / 4; ctx.beginPath(); ctx.moveTo(x + Math.cos(a) * 10, y - 2 + Math.sin(a) * 10); ctx.lineTo(x + Math.cos(a) * 13, y - 2 + Math.sin(a) * 13); ctx.stroke(); } }
    else if (kind === "heart") { ctx.fillStyle = "#e8436b"; ctx.beginPath(); ctx.moveTo(x, y + 7); ctx.bezierCurveTo(x - 12, y - 2, x - 6, y - 11, x, y - 4); ctx.bezierCurveTo(x + 6, y - 11, x + 12, y - 2, x, y + 7); ctx.fill(); }
    else if (kind === "sweat") { ctx.fillStyle = "#5aa9e6"; ctx.beginPath(); ctx.moveTo(x, y - 9); ctx.quadraticCurveTo(x + 8, y + 3, x, y + 7); ctx.quadraticCurveTo(x - 8, y + 3, x, y - 9); ctx.fill(); }
    else if (kind === "note") { ctx.fillStyle = pal.ink2; circle(x - 3, y + 5, 3.5, pal.ink2); ctx.fillRect(x, y - 9, 1.8, 14); ctx.fillRect(x, y - 9, 7, 2.5); }
    else if (kind === "question") textAt("?", x, y + 6, 17, 850, "#2a78d6");
    else if (kind === "bang") textAt("!", x, y + 6, 17, 850, "#d03b3b");
    else if (kind === "star") sparkle(x, y, 9, "#eda100", t);
    else if (kind === "coffee") { ctx.fillStyle = "#8a5a2b"; rr(x - 6, y - 5, 12, 11, 2); ctx.fill(); ctx.strokeStyle = "#8a5a2b"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x + 7, y, 3, -1.3, 1.3); ctx.stroke(); }
  }
  function emoteOn(id, kind, dur = 1800) {
    const p = id === "watchdog" ? dog : people[id]; if (!p) return;
    if (p.emoteTimer) clearTimeout(p.emoteTimer);
    p.emote = kind; p.emoteTimer = setTimeout(() => { p.emote = null; }, dur);
    fx("emote", p.x + 16, p.y - 66, {kind, dur});
  }
  const spot = id => id === "watchdog" ? [dog.x, dog.y] : people[id] ? [people[id].x, people[id].y] : plan.desks[id] ? plan.desks[id].slice(0, 2) : [plan.w / 2, plan.h / 2];

  // ----- what happens when a message arrives -----
  function pnlFor(m) {
    const ts = new Date(m.ts).getTime(), sym = (m.text.match(/^(?:Trade closed: )?([A-Z]{3})/) || [])[1];
    let best = null, bd = 6 * 3600e3;
    (D.trades || []).forEach(tr => { if (sym && tr.symbol !== sym) return; const d = Math.abs(new Date(tr.closed).getTime() - ts); if (d < bd) { bd = d; best = tr; } });
    return best ? best.pnl : null;
  }
  function react(m) {
    const [tx, ty] = spot(m.to), [fx_, fy] = spot(m.from);
    if (m.from === "scanner" && m.kind === "idea") emoteOn("risk", "question");
    if (m.from === "risk" && m.kind === "approve") { people.risk.card = {color: "#1baf7a", until: performance.now() + 2600}; fx("text", fx_, fy - 70, {text: "toot!", color: "#1baf7a", size: 13});
                                                      people.overseer && (people.overseer.thinkUntil = performance.now() + 2600); }
    if (m.from === "risk" && m.kind === "veto") { people.risk.card = {color: "#d03b3b", until: performance.now() + 2600}; fx("text", fx_, fy - 70, {text: "TWEET!", color: "#d03b3b", size: 15}); emoteOn("scanner", "sweat"); }
    if (m.from === "risk" && m.kind === "info") people.risk.card = {color: "#f5c518", until: performance.now() + 2600};
    if (m.from === "overseer" && m.kind === "approve") { fx("confetti", tx, ty - 40); emoteOn("execution", "heart"); }
    if (m.from === "overseer" && m.kind === "veto") emoteOn("scanner", "sweat");
    if (m.kind === "order" && m.to === "broker") { prints.push({start: performance.now()}); fx("text", tx, ty - 70, {text: "ding!", color: "#d4a24c", size: 13}); fx("spark", tx, ty - 30); }
    if (m.to === "you" && m.from === "execution") {
      const pnl = pnlFor(m);
      if (pnl != null && pnl >= 0) { fx("coins", tx, ty - 30); fx("text", tx, ty - 70, {text: "+$" + Math.round(pnl).toLocaleString(), color: "#1baf7a", size: 16, dur: 2200}); emoteOn("execution", "star"); }
      else if (pnl != null) { fx("rain", spot("execution")[0], spot("execution")[1] - 10, {dur: 2600}); fx("text", tx, ty - 70, {text: "−$" + Math.abs(Math.round(pnl)).toLocaleString(), color: "#d03b3b", size: 16, dur: 2200}); }
    }
    if (m.kind === "alert") {
      alarmUntil = performance.now() + 3200;
      if (m.from === "watchdog") { dog.barkUntil = performance.now() + 1400; fx("text", dog.x, dog.y - 46, {text: "WOOF!", color: "#d03b3b", size: 16}); }
      emoteOn("you", "bang");
      if (m.from === "data") { flashUntil = performance.now() + 400; flashColor = "#ffffff"; fx("text", fx_, fy - 70, {text: "Sudden move!", color: "#d03b3b"}); }
    }
    if (m.from === "data" && m.kind === "info") { tickerFlash = performance.now() + 1500; fx("spark", fx_, fy - 50); }
    if (m.from === "news") emoteOn(m.to, "question", 1400);
    if (m.from === "learning") emoteOn(m.to, "bulb", 1400);
    const r = m.to === "watchdog" ? dog : people[m.to]; if (r) r.hopT = 0.0001;
  }
  let tickerFlash = 0;
  const tickerText = (() => {
    const parts = [`PAPER EQUITY $${Math.round(D.equity).toLocaleString()}`];
    regimes.forEach(r => parts.push(`${r.symbol} ${r.trend.replace("_", " ").toUpperCase()}${r.volatility === "high" ? " · VOLATILE" : ""}`));
    agents.forEach(a => { if (a.id === "scanner" || a.id === "risk" || a.id === "execution") parts.push(`${a.name.toUpperCase()}: ${a.short}`); });
    parts.push(T.market_open ? "MARKET OPEN" : "MARKET CLOSED · BACK SUNDAY 6 PM ET");
    return parts.join("   ◆   ");
  })();

  // ----- movement -----
  function step(obj, dt) {
    if (!obj.path.length) return;
    const [tx, ty] = obj.path[0], dx = tx - obj.x, dy = ty - obj.y, d = Math.hypot(dx, dy), s = obj.speed * dt;
    if (Math.abs(dx) > 1) obj.facing = Math.sign(dx);
    if (d <= s) { obj.x = tx; obj.y = ty; obj.path.shift(); if (!obj.path.length && obj.onArrive) { const f = obj.onArrive; obj.onArrive = null; f(); } }
    else { obj.x += dx / d * s; obj.y += dy / d * s; }
  }
  function walk(obj, points, speed, done) {
    if (reduce) { const last = points[points.length - 1]; obj.x = last[0]; obj.y = last[1]; obj.path = []; done && done(); return; }
    obj.path = points.slice(); obj.speed = speed; obj.onArrive = done;
  }

  // ----- replaying the last update's messages as deliveries -----
  function trips() {
    let list = msgs.map((m, i) => ({...m, i})).filter(m => m.fresh);
    if (!list.length) list = msgs.map((m, i) => ({...m, i})).slice(-8);
    const out = [];
    list.forEach(m => { const last = out[out.length - 1]; if (last && last.from === m.from && last.text === m.text) last.stops.push(m); else out.push({from: m.from, text: m.text, stops: [m]}); });
    return out;
  }
  let playing = false, playToken = 0;
  function say(who, m, ms) {
    const [title, color] = KIND[m.kind] || KIND.info, now = performance.now();
    bubbles = bubbles.filter(b => b.who !== who);
    const head = who === "watchdog" ? "Woof! " : "";
    bubbles.push({who, title: `${head}${title} → ${(byId[m.to] || {name: m.to}).name}`, text: m.text.replace(/\s+/g, " ").slice(0, 170), color, start: now, until: now + ms});
    const li = document.querySelector(`#feed li[data-i="${m.i}"]`);
    if (li) { li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), ms); }
  }
  function runTrip(trip, token) {
    return new Promise(resolve => {
      const isDog = trip.from === "watchdog", who = isDog ? dog : people[trip.from];
      if (!who || token !== playToken) return resolve();
      const home = isDog ? plan.dogbed : seat(trip.from);
      if (trip.from === "scanner" && trip.stops[0].kind === "idea") emoteOn("scanner", "bulb", 1500);
      let k = 0;
      const next = () => {
        if (token !== playToken) return resolve();
        if (k >= trip.stops.length) {
          who.carry = null;
          walk(who, route([who.x, who.y], home), isDog ? 170 : 190, () => { if (!isDog) who.seated = true; else who.sitting = true; });
          return resolve();
        }
        const m = trip.stops[k++], target = m.to;
        const dest = target === "watchdog" ? [dog.x + 30, dog.y] : front(target, (k % 2 ? -18 : 18));
        who.seated = false; who.sitting = false; who.carry = (KIND[m.kind] || KIND.info)[1];
        walk(who, route([who.x, who.y], dest), isDog ? 230 : 210, () => {
          say(isDog ? "watchdog" : trip.from, m, 2600);
          react(m);
          setTimeout(next, reduce ? 1400 : 2400);
        });
      };
      next();
    });
  }
  async function replay() {
    playToken++; const token = playToken;
    playing = true; $("replay").textContent = "■ Stop"; $("sim-now").textContent = "Replaying the last update";
    tickerBoost = 0.05;
    for (const t of trips()) { if (token !== playToken) break; await runTrip(t, token); }
    if (token === playToken) { playing = false; tickerBoost = 0; $("replay").textContent = "▶ Replay last update"; $("sim-now").textContent = "Now"; }
  }
  function stopReplay() {
    playToken++; playing = false; tickerBoost = 0; $("replay").textContent = "▶ Replay last update"; $("sim-now").textContent = "Now";
    agents.forEach(a => { const p = people[a.id]; if (p && !p.seated) { p.carry = null; walk(p, route([p.x, p.y], seat(a.id)), 260, () => { p.seated = true; }); } });
  }
  $("replay").addEventListener("click", () => playing ? stopReplay() : replay());

  // ----- everyday life on the floor -----
  let nextCoffee = performance.now() + 9000, nextEmote = performance.now() + 4000;
  function ambient(now) {
    if (reduce) return;
    const asleep = night;
    // the watchdog patrols the aisle (and naps in its bed when the market is closed)
    if (!dog.path.length && now > dog.patrolAt && !playing) {
      if (asleep && Math.random() < 0.6) { dog.sitting = false; walk(dog, [plan.dogbed], 70, () => { dog.sitting = true; dog.patrolAt = now + 20000; }); dog.patrolAt = now + 60000; }
      else {
        const span = plan.aisle === "h" ? [70, plan.w - 90] : [plan.wall + 50, plan.h - 90], target = span[0] + Math.random() * (span[1] - span[0]);
        const pt = plan.aisle === "h" ? [target, plan.at + 16] : [plan.at + 20, target];
        dog.sitting = false;
        walk(dog, [pt], 70, () => { dog.sitting = true; dog.patrolAt = now + 2500 + Math.random() * 4000; if (Math.random() < 0.3) fx("text", dog.x, dog.y - 34, {text: "sniff sniff", color: pal.ink2, size: 11}); });
        dog.patrolAt = now + 60000;
      }
    }
    if (playing) return;
    // coffee runs
    if (now > nextCoffee) {
      nextCoffee = now + 13000 + Math.random() * 12000;
      const idle = agents.filter(a => !["watchdog", "broker"].includes(a.id) && a.status !== "off" && people[a.id] && people[a.id].seated && !people[a.id].path.length);
      if (idle.length) {
        const p = people[idle[Math.floor(Math.random() * idle.length)].id];
        const cup = plan.aisle === "h" ? [plan.coffee[0] - 30, plan.coffee[1]] : [plan.coffee[0], plan.coffee[1] - 44];
        p.seated = false;
        walk(p, route([p.x, p.y], cup), 120, () => { emoteOn(p.id, "coffee", 1600); setTimeout(() => { if (!playing) walk(p, route([p.x, p.y], seat(p.id)), 120, () => { p.seated = true; }); }, 1700); });
      }
    }
    // little moods
    if (now > nextEmote) {
      nextEmote = now + 3500 + Math.random() * 4500;
      const pick = agents[Math.floor(Math.random() * agents.length)];
      if (pick.id === "watchdog" || !people[pick.id] || !people[pick.id].seated) return;
      const mood = {working: ["note", "star"], idle: ["note", "coffee"], problem: ["sweat", "bang"], paused: ["sweat"], off: []}[pick.status] || [];
      if (pick.id === "scanner" && pick.status === "working") mood.push("question");
      if (pick.id === "learning") mood.push("bulb");
      if (mood.length && !(night && pick.status === "idle")) emoteOn(pick.id, mood[Math.floor(Math.random() * mood.length)], 1600);
    }
  }

  // ----- main loop -----
  let last = performance.now(), running = false;
  function frame(now) {
    if (!running) return;
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    [...Object.values(people), dog].forEach(p => {
      step(p, dt);
      if (p.hopT > 0) { p.hopT += dt * 3; p.hop = Math.sin(Math.min(1, p.hopT) * Math.PI) * 8; if (p.hopT >= 1) { p.hopT = 0; p.hop = 0; } }
    });
    ambient(now);
    bubbles = bubbles.filter(b => b.until > now);
    ctx.clearRect(0, 0, plan.w, plan.h);
    drawRoom(now);
    const items = [];
    Object.keys(plan.desks).forEach(id => {
      const {y, face} = deskBox(id), p = people[id], st = seat(id);
      items.push({y: face === "down" ? y - 50 : st[1] - 1, draw: () => drawChair(id)});
      items.push({y: face === "up" ? y + 10 : y, draw: () => drawDesk(id, now)});
      if (p) items.push({y: p.seated && face === "down" ? y - 1 : p.y, draw: () => drawPerson(p, now)});
    });
    items.push({y: dog.y, draw: () => drawDog(now)});
    items.sort((a, b) => a.y - b.y).forEach(it => it.draw());
    Object.keys(plan.desks).forEach(namePlate);
    if (night) { ctx.fillStyle = dark ? "rgba(0,0,30,.22)" : "rgba(20,30,80,.14)"; ctx.fillRect(0, plan.wall, plan.w, plan.h - plan.wall); }
    drawEffects(now);
    bubbles.forEach(b => drawBubble(b, now));
    requestAnimationFrame(frame);
  }
  function start() { if (running) return; running = true; last = performance.now(); layout(); requestAnimationFrame(frame); }
  function stop() { running = false; }

  // ----- tapping someone -----
  canvas.addEventListener("click", ev => {
    const r = canvas.getBoundingClientRect(), x = (ev.clientX - r.left) / scale, y = (ev.clientY - r.top) / scale;
    let best = null, bd = 64;
    Object.keys(plan.desks).forEach(id => {
      const p = people[id], [dx, dy] = plan.desks[id];
      [[p ? p.x : dx, p ? p.y - 12 : dy], [dx, dy]].forEach(([cx, cy]) => { const d = Math.hypot(cx - x, cy - y); if (d < bd) { bd = d; best = id; } });
    });
    if (Math.hypot(dog.x - x, dog.y - y) < bd) best = "watchdog";
    select(best === selected ? null : best);
    if (best && best !== "watchdog" && people[best]) { people[best].hopT = 0.0001; emoteOn(best, "heart", 1200); }
    if (best === "watchdog") { dog.hopT = 0.0001; fx("text", dog.x, dog.y - 40, {text: "woof!", color: pal.ink2, size: 12}); }
  });

  // ----- roster, conversation, details -----
  const roster = $("roster");
  agents.forEach(a => {
    const b = el("button", {type: "button", "aria-pressed": "false", "data-id": a.id, title: `${a.name}: ${ROLE[a.id] || a.job}`});
    const dot = el("span", {class: "sdot"}); dot.style.background = css(STATE_COLOR[a.status] || "--axis");
    b.append(dot, document.createTextNode(`${a.name} · ${a.short || STATE[a.status]}`));
    b.addEventListener("click", () => select(selected === a.id ? null : a.id));
    roster.append(b);
  });
  function renderFeed() {
    const feed = $("feed"); feed.replaceChildren();
    const list = msgs.map((m, i) => [m, i]).filter(([m]) => !selected || m.from === selected || m.to === selected).reverse();
    $("feed-title").textContent = selected ? `${byId[selected].name}: messages` : "Conversation";
    if (!list.length) { feed.append(el("li", {}, selected ? "No messages from or to this agent yet." : "No messages yet. While the market is open, each update's conversation appears here.")); return; }
    list.forEach(([m, i]) => {
      const li = el("li", {"data-i": String(i)}), d = new Date(m.ts);
      li.append(el("span", {class: "t"}, d.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + "\n" + d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", hour12: false})));
      const rt = el("div", {class: "route"}), [klabel, kvar] = KIND[m.kind] || KIND.info;
      const tag = el("span", {class: "ktag"}), kd = el("span", {class: "kdot"}); kd.style.background = css(kvar);
      tag.append(kd, document.createTextNode(klabel));
      rt.append(el("b", {}, (byId[m.from] || {name: m.from}).name), document.createTextNode("→"), el("b", {}, (byId[m.to] || {name: m.to}).name), tag);
      if (m.fresh) rt.append(el("span", {class: "ktag"}, "this update"));
      li.append(rt, el("div", {class: "msg"}, m.text));
      feed.append(li);
    });
  }
  function select(id) {
    selected = id;
    roster.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
    const det = $("detail");
    if (id) {
      const a = byId[id]; det.hidden = false; det.replaceChildren();
      [["Who", `${a.name}, ${ROLE[id] || ""}`], ["Job", a.job], ["Right now", a.doing], ["Status", STATE[a.status] || a.status], ["Last message", a.last]].forEach(([k, v]) => {
        const r = el("div", {class: "row"}); r.append(el("b", {}, k + ": "), document.createTextNode(v)); det.append(r);
      });
    } else det.hidden = true;
    renderFeed();
  }

  // ----- header and legend -----
  const working = agents.filter(a => a.status === "working").length, problems = agents.filter(a => a.status === "problem").length;
  $("team-sub").textContent = `${working} working · ${problems} with a problem · as of ${fmtTime(D.now)}. Tap anyone to see what they're up to.`;
  const chip = $("market-chip"), mdot = el("span", {class: "sdot"});
  mdot.style.background = css(T.market_open ? "--good" : "--axis");
  chip.append(mdot, document.createTextNode(T.market_open ? "Market open" : "Market closed: lights down, dog napping"));
  Object.values(KIND).forEach(([label, v]) => { const s = el("span"), d = el("span", {class: "kdot"}); d.style.background = css(v); s.append(d, document.createTextNode(label + " envelope")); $("legend").append(s); });
  $("sim-now").textContent = "Now";
  renderFeed();

  let w0 = 0;
  addEventListener("resize", () => { if (running && Math.abs(innerWidth - w0) > 10) { w0 = innerWidth; layout(); } });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => palette());
  window.__floor = {start, stop, replay};
})();

// ===================== the view toggle =====================
(function views() {
  const dash = $("view-dash"), team = $("view-agents"), bd = $("btn-dash"), bt = $("btn-agents");
  let first = true;
  function show(v) {
    const agentsView = v === "agents";
    dash.hidden = agentsView; team.hidden = !agentsView;
    bd.setAttribute("aria-pressed", String(!agentsView)); bt.setAttribute("aria-pressed", String(agentsView));
    try { localStorage.setItem("desk-view", v); } catch (e) { /* storage may be blocked */ }
    if (agentsView && window.__floor) {
      requestAnimationFrame(() => { window.__floor.start(); if (first) { first = false; setTimeout(() => window.__floor.replay(), 700); } });
    } else { window.__floor && window.__floor.stop(); drawCharts(); }
    if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  }
  bd.addEventListener("click", () => show("dash"));
  bt.addEventListener("click", () => show("agents"));
  let start = location.hash === "#agents" ? "agents" : location.hash === "#dash" ? "dash" : null;
  if (!start) { try { start = localStorage.getItem("desk-view"); } catch (e) { start = null; } }
  show(start === "agents" ? "agents" : "dash");
  // Stay current: reload every 5 minutes while the page is open and visible (not in the middle of a replay).
  setInterval(() => { if (document.visibilityState === "visible") location.reload(); }, 5 * 60 * 1000);
})();
</script>
</body>
</html>
"""
