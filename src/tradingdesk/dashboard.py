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
               health: str, reviews_count: dict | None = None) -> dict:
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
        "news": news_snapshot, "upcoming": upcoming, "spend": spend, "learning": learning or {}, "reviews_count": reviews_count or {},
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
</style>
</head>
<body>
<main>
  <header>
    <h1>Trading desk <span class="sub">· paper money</span></h1>
    <div class="sub" id="updated"></div>
  </header>
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
    <h2>What the desk is learning</h2>
    <div id="learning"></div>
  </section>

  <p class="foot">Paper trading with prices about 10 minutes delayed. Same rules and fill model as the backtests. Updated automatically.</p>
</main>
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
</script>
</body>
</html>
"""
