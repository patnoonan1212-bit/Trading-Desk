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
/* ----- Agents view ----- */
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
.viewtoggle .live { width: 8px; height: 8px; border-radius: 50%; background: var(--good); box-shadow: 0 0 0 0 var(--good); animation: ping 2s infinite; }
[hidden] { display: none !important; }
.team-head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; margin-top: 12px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); font-size: 13px; color: var(--ink-2); }
.btn { border: 1px solid var(--border); background: var(--surface); color: var(--ink); border-radius: 999px; padding: 7px 14px;
  font: 600 13px/1 system-ui, -apple-system, "Segoe UI", sans-serif; cursor: pointer; }
.btn:hover { background: var(--page); }
.board { position: relative; margin-top: 12px; padding: 14px; border-radius: 16px; border: 1px solid var(--border);
  background: radial-gradient(circle at 1px 1px, var(--grid) 1px, transparent 0) 0 0 / 18px 18px, var(--surface); }
.stages { position: relative; z-index: 1; display: grid; gap: 14px; grid-template-columns: 1fr; }
.stage h3 { margin: 0 0 8px; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); font-weight: 700; }
.stage .cards { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.stage.oversight .cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
/* Wide screens: one production line along the top (Data -> Scanner -> Risk -> Head trader -> Execution -> Broker),
   the agents that feed it underneath, and the ones that watch over it on the bottom row. */
@media (min-width: 1000px) {
  .stages { grid-template-columns: repeat(6, minmax(0, 1fr)); column-gap: 46px; row-gap: 58px; padding-top: 30px; }
  .stage, .stage .cards { display: contents; }
  .stage h3 { display: none; }
  #ag-data { grid-area: 1 / 1; } #ag-scanner { grid-area: 1 / 2; } #ag-risk { grid-area: 1 / 3; }
  #ag-overseer { grid-area: 1 / 4; } #ag-execution { grid-area: 1 / 5; } #ag-broker { grid-area: 1 / 6; }
  #ag-regime { grid-area: 2 / 2; } #ag-news { grid-area: 2 / 3; }
  #ag-learning { grid-area: 3 / 4; } #ag-you { grid-area: 3 / 5; } #ag-watchdog { grid-area: 3 / 6; }
}
.agent { position: relative; text-align: left; border: 1px solid var(--border); border-radius: 14px; background: var(--surface);
  padding: 10px 11px; cursor: pointer; color: var(--ink); font: inherit; min-width: 0; transition: transform .25s, box-shadow .25s, border-color .25s, opacity .25s; }
.agent:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(0,0,0,.08); }
.agent .top { display: flex; align-items: center; gap: 8px; }
.agent .ico { width: 28px; height: 28px; border-radius: 9px; display: grid; place-items: center; flex: none; background: var(--page); color: var(--ink-2); }
.agent .name { font-weight: 650; font-size: 14px; line-height: 1.2; overflow-wrap: anywhere; }
.agent .state { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--ink-2); margin-top: 2px; }
.agent .doing { font-size: 12px; color: var(--ink-2); margin-top: 7px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.agent .when { font-size: 11px; color: var(--muted); margin-top: 5px; }
.sdot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.agent[data-status="working"] { border-color: color-mix(in srgb, var(--good) 45%, var(--border)); }
.agent[data-status="working"] .sdot { background: var(--good); animation: ping 2s infinite; }
.agent[data-status="idle"] .sdot { background: var(--axis); }
.agent[data-status="problem"] { border-color: var(--critical); }
.agent[data-status="problem"] .sdot { background: var(--critical); }
.agent[data-status="paused"] { border-color: color-mix(in srgb, var(--warning) 70%, var(--border)); }
.agent[data-status="paused"] .sdot { background: var(--warning); }
.agent[data-status="off"] { border-style: dashed; }
.agent[data-status="off"] > * { opacity: .6; }
.agent[data-status="off"] .sdot { background: transparent; border: 1.5px solid var(--muted); width: 7px; height: 7px; }
.agent.selected { box-shadow: 0 0 0 2px var(--ink); }
.agent.dim { opacity: .35; }
.agent.recv { animation: recv .7s ease-out; }
@keyframes ping { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--good) 55%, transparent); } 70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
@keyframes recv { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--glow, var(--k-idea)) 55%, transparent); } 100% { box-shadow: 0 0 0 9px transparent; } }
.wires { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; overflow: visible; }
.wires.top { z-index: 2; }
.wire { fill: none; stroke: var(--axis); stroke-width: 1.5; transition: stroke .3s, stroke-width .3s, opacity .3s; }
.wire.hot { stroke: var(--ink-2); stroke-width: 2; }
.wire.dim { opacity: .15; }
.wlabel { font-size: 10.5px; fill: var(--muted); paint-order: stroke; stroke: var(--surface); stroke-width: 4px; stroke-linejoin: round; }
.pulse { filter: drop-shadow(0 0 4px currentColor); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; font-size: 12px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.kdot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.feed { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow: auto; }
.feed li { display: grid; grid-template-columns: 64px 1fr; gap: 4px 12px; padding: 9px 4px; border-bottom: 1px solid var(--grid); transition: background .4s; }
.feed li.flash { background: color-mix(in srgb, var(--k-idea) 10%, transparent); }
.feed .t { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; padding-top: 2px; }
.feed .route { font-size: 12px; color: var(--ink-2); display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.feed .route b { color: var(--ink); font-weight: 650; }
.feed .msg { grid-column: 2; font-size: 13px; line-height: 1.4; }
.ktag { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border); }
.detail { margin-top: 12px; display: grid; gap: 6px; }
.detail .row { font-size: 13px; color: var(--ink-2); } .detail .row b { color: var(--ink); }
@media (prefers-reduced-motion: reduce) { .agent, .wire { transition: none; } .sdot, .viewtoggle .live { animation: none !important; } }
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
        <h2 style="margin:0">The team</h2>
        <div class="sub" id="team-sub"></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <span class="chip" id="market-chip"></span>
        <button class="btn" id="replay" type="button">▶ Replay last update</button>
      </div>
    </div>
    <figure style="margin:0">
      <div class="board" id="board">
        <svg class="wires" id="wires" aria-hidden="true"></svg>
        <div class="stages" id="stages"></div>
        <svg class="wires top" id="wires-top" aria-hidden="true"></svg>
      </div>
      <figcaption class="sub" style="margin-top:8px">How a trade travels: prices and news come in, the Regime Agent and Scanner find an idea, the Risk Manager
        and head trader decide, and Execution places it. Moving dots are messages between agents. Tap an agent to see only its messages.</figcaption>
    </figure>
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
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawCharts(); window.__drawWires && window.__drawWires(); });

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

// ===================== Agents view =====================
(function team() {
  const T = D.team || {};
  const agents = T.agents || [], edges = T.edges || [], msgs = T.messages || [];
  const byId = Object.fromEntries(agents.map(a => [a.id, a]));
  const KIND = {idea: ["Trade idea", "--k-idea"], approve: ["Approval", "--k-approve"], veto: ["Veto", "--k-veto"],
                order: ["Order", "--k-order"], info: ["Update", "--k-info"], alert: ["⚠ Alert", "--k-alert"]};
  const STATE = {working: "Working", idle: "Idle", problem: "Problem", off: "Off", paused: "Paused new trades"};
  // Lines that would run behind another card on wide screens arch over the top row instead.
  const ARCS = new Set(["risk>execution", "overseer>scanner"]);
  const GROUPS = [["inputs", "Inputs"], ["analysis", "Analysis"], ["decision", "Decision"], ["action", "Action"], ["oversight", "Oversight"]];
  const ICONS = {
    data: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    news: "M5 4h11v16H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1zm11 4h3v10a2 2 0 0 1-2 2M8 8h5M8 12h5M8 16h3",
    regime: "M3 16c3 0 3-8 6-8s3 8 6 8 3-8 6-8M3 20h18",
    scanner: "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M12 12l6-6",
    risk: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6zM9 12l2 2 4-4",
    overseer: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM18 16l.8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8z",
    execution: "M13 3L5 14h6l-1 7 8-11h-6z",
    broker: "M3 9l9-5 9 5M5 9v9M10 9v9M14 9v9M19 9v9M3 20h18",
    watchdog: "M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12zM12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0",
    learning: "M3 8l9-4 9 4-9 4zM7 10v5c0 1.7 2.2 3 5 3s5-1.3 5-3v-5",
    you: "M12 8m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M4 21c0-4 3.6-7 8-7s8 3 8 7",
  };
  if (!agents.length) return;

  // ----- cards -----
  const stages = $("stages");
  GROUPS.forEach(([g, label]) => {
    const st = el("div", {class: "stage " + g}), cards = el("div", {class: "cards"});
    st.append(el("h3", {}, label), cards);
    agents.filter(a => a.group === g).forEach(a => {
      const b = el("button", {class: "agent", id: "ag-" + a.id, "data-status": a.status, type: "button", "aria-pressed": "false"});
      const ico = el("span", {class: "ico"});
      const svg = svgEl("svg", {width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.7,
                                "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"});
      svg.append(svgEl("path", {d: ICONS[a.id] || ICONS.data}));
      ico.append(svg);
      const head = el("div", {style: "min-width:0"});
      const state = el("div", {class: "state"});
      state.append(el("span", {class: "sdot"}), document.createTextNode(STATE[a.status] || a.status));
      head.append(el("div", {class: "name"}, a.name), state);
      const top = el("div", {class: "top"}); top.append(ico, head);
      b.append(top, el("div", {class: "doing"}, a.doing), el("div", {class: "when"}, "Last message: " + a.last));
      b.addEventListener("click", () => select(selected === a.id ? null : a.id));
      cards.append(b);
    });
    stages.append(st);
  });

  // ----- wires between cards -----
  const svg = $("wires"), top = $("wires-top"), board = $("board");
  let paths = {};
  function anchor(r, side, box) {
    const x = r.left - box.left, y = r.top - box.top;
    return {left: [x, y + r.height / 2], right: [x + r.width, y + r.height / 2], top: [x + r.width / 2, y], bottom: [x + r.width / 2, y + r.height]}[side];
  }
  function drawWires() {
    svg.replaceChildren(); top.replaceChildren(); paths = {};
    const box = board.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    top.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    const wide = box.width >= 960, showLabels = wide;
    edges.forEach(e => {
      const A = $("ag-" + e.from), B = $("ag-" + e.to);
      if (!A || !B) return;
      const ra = A.getBoundingClientRect(), rb = B.getBoundingClientRect();
      const ca = [ra.left + ra.width / 2, ra.top + ra.height / 2], cb = [rb.left + rb.width / 2, rb.top + rb.height / 2];
      const dx = cb[0] - ca[0], dy = cb[1] - ca[1];
      let p1, p2, c1, c2;
      if (wide && ARCS.has(e.from + ">" + e.to)) {
        p1 = anchor(ra, "top", box); p2 = anchor(rb, "top", box);
        const lift = Math.min(p1[1], p2[1]) - 26;
        c1 = [p1[0], lift]; c2 = [p2[0], lift];
      } else if (Math.abs(dx) > Math.abs(dy) * 1.2) {
        p1 = anchor(ra, dx > 0 ? "right" : "left", box); p2 = anchor(rb, dx > 0 ? "left" : "right", box);
        const k = Math.max(30, Math.abs(p2[0] - p1[0]) * 0.45) * Math.sign(dx);
        c1 = [p1[0] + k, p1[1]]; c2 = [p2[0] - k, p2[1]];
      } else {
        p1 = anchor(ra, dy > 0 ? "bottom" : "top", box); p2 = anchor(rb, dy > 0 ? "top" : "bottom", box);
        const k = Math.max(24, Math.abs(p2[1] - p1[1]) * 0.45) * Math.sign(dy || 1);
        c1 = [p1[0], p1[1] + k]; c2 = [p2[0], p2[1] - k];
      }
      const d = `M${p1[0]},${p1[1]} C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
      const path = svgEl("path", {d, class: "wire", "data-from": e.from, "data-to": e.to});
      svg.append(path);
      paths[e.from + ">" + e.to] = path;
      // small arrowhead at the target end
      const L = path.getTotalLength(), q = path.getPointAtLength(Math.max(0, L - 7)), end = path.getPointAtLength(L);
      const ang = Math.atan2(end.y - q.y, end.x - q.x), s = 5;
      const pts = [[end.x, end.y], [end.x - s * Math.cos(ang - 0.5), end.y - s * Math.sin(ang - 0.5)], [end.x - s * Math.cos(ang + 0.5), end.y - s * Math.sin(ang + 0.5)]];
      svg.append(svgEl("polygon", {points: pts.map(p => p.join(",")).join(" "), fill: css("--axis"), class: "head", "data-from": e.from, "data-to": e.to}));
      if (showLabels) {
        const mid = path.getPointAtLength(L / 2);
        const t = svgEl("text", {x: mid.x, y: mid.y - 5, "text-anchor": "middle", class: "wlabel"});
        t.textContent = e.label; top.append(t);
      }
    });
    applySelection();
    markHot();
  }
  function markHot() {
    const fresh = msgs.filter(m => m.fresh);
    Object.values(paths).forEach(p => p.classList.remove("hot"));
    fresh.forEach(m => { const p = paths[m.from + ">" + m.to] || paths[m.to + ">" + m.from]; if (p) p.classList.add("hot"); });
  }

  // ----- pulses -----
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function pulse(m) {
    return new Promise(resolve => {
      const fwd = paths[m.from + ">" + m.to], back = paths[m.to + ">" + m.from], path = fwd || back;
      const target = $("ag-" + m.to);
      const color = css((KIND[m.kind] || KIND.info)[1]);
      const done = () => {
        if (target) { target.style.setProperty("--glow", color); target.classList.remove("recv"); void target.offsetWidth; target.classList.add("recv"); }
        resolve();
      };
      if (!path || reduce) { setTimeout(done, reduce ? 150 : 300); return; }
      const L = path.getTotalLength(), dot = svgEl("circle", {r: 5, fill: color, stroke: css("--surface"), "stroke-width": 2, class: "pulse"});
      dot.style.color = color;
      top.append(dot);
      const t0 = performance.now(), dur = 900;
      (function step(t) {
        const k = Math.min(1, (t - t0) / dur), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        const pt = path.getPointAtLength((fwd ? e : 1 - e) * L);
        dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y);
        if (k < 1) requestAnimationFrame(step); else { dot.remove(); done(); }
      })(t0);
    });
  }
  let playing = false;
  async function replay() {
    if (playing) return;
    playing = true; $("replay").textContent = "Playing…";
    let list = msgs.filter(m => m.fresh);
    if (!list.length) list = msgs.slice(-10);
    for (const m of list) {
      const li = document.querySelector(`#feed li[data-i="${msgs.indexOf(m)}"]`);
      if (li) { li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1400); }
      await pulse(m);
    }
    playing = false; $("replay").textContent = "▶ Replay last update";
  }
  $("replay").addEventListener("click", replay);

  // ----- feed and selection -----
  let selected = null;
  function renderFeed() {
    const feed = $("feed"); feed.replaceChildren();
    const list = msgs.map((m, i) => [m, i]).filter(([m]) => !selected || m.from === selected || m.to === selected).reverse();
    $("feed-title").textContent = selected ? `${byId[selected].name}: messages` : "Conversation";
    if (!list.length) {
      feed.append(el("li", {}, selected ? "No messages from or to this agent yet." :
        "No messages yet. When the market is open, every update's conversation appears here."));
      return;
    }
    list.forEach(([m, i]) => {
      const li = el("li", {"data-i": String(i)});
      const d = new Date(m.ts);
      li.append(el("span", {class: "t"}, d.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + "\n" +
        d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", hour12: false})));
      const route = el("div", {class: "route"});
      const [klabel, kvar] = KIND[m.kind] || KIND.info;
      const tag = el("span", {class: "ktag"}); const kd = el("span", {class: "kdot"}); kd.style.background = css(kvar);
      tag.append(kd, document.createTextNode(klabel));
      route.append(el("b", {}, (byId[m.from] || {name: m.from}).name), document.createTextNode("→"), el("b", {}, (byId[m.to] || {name: m.to}).name), tag);
      if (m.fresh) route.append(el("span", {class: "ktag"}, "this update"));
      li.append(route, el("div", {class: "msg"}, m.text));
      li.querySelector(".t").style.whiteSpace = "pre-line";
      feed.append(li);
    });
  }
  function applySelection() {
    agents.forEach(a => {
      const c = $("ag-" + a.id); if (!c) return;
      const linked = !selected || a.id === selected || edges.some(e => (e.from === selected && e.to === a.id) || (e.to === selected && e.from === a.id));
      c.classList.toggle("selected", a.id === selected); c.classList.toggle("dim", !linked);
      c.setAttribute("aria-pressed", String(a.id === selected));
    });
    svg.querySelectorAll(".wire, .head").forEach(w => {
      const on = !selected || w.dataset.from === selected || w.dataset.to === selected;
      w.classList.toggle("dim", !on);
    });
    const det = $("detail");
    if (selected) {
      const a = byId[selected]; det.hidden = false; det.replaceChildren();
      [["Job", a.job], ["Now", a.doing], ["Status", STATE[a.status] || a.status], ["Last message", a.last]].forEach(([k, v]) => {
        const r = el("div", {class: "row"}); r.append(el("b", {}, k + ": "), document.createTextNode(v)); det.append(r);
      });
    } else det.hidden = true;
  }
  function select(id) { selected = id; applySelection(); renderFeed(); }

  // ----- header, legend -----
  const working = agents.filter(a => a.status === "working").length, problems = agents.filter(a => a.status === "problem").length;
  $("team-sub").textContent = `${working} working · ${problems} with a problem · as of ${fmtTime(D.now)} (the desk updates every 15 minutes on GitHub)`;
  const chip = $("market-chip"); const dot = el("span", {class: "sdot"});
  dot.style.background = css(T.market_open ? "--good" : "--axis");
  chip.append(dot, document.createTextNode(T.market_open ? "Market open" : "Market closed: agents are resting"));
  const lg = $("legend");
  Object.values(KIND).forEach(([label, v]) => { const s = el("span"); const d = el("span", {class: "kdot"}); d.style.background = css(v); s.append(d, document.createTextNode(label)); lg.append(s); });

  renderFeed();
  window.__drawWires = drawWires;
  window.__replayTeam = replay;
})();

// ===================== the view toggle =====================
(function views() {
  const dash = $("view-dash"), team = $("view-agents"), bd = $("btn-dash"), bt = $("btn-agents");
  let first = true;
  function show(v) {
    const agents = v === "agents";
    dash.hidden = agents; team.hidden = !agents;
    bd.setAttribute("aria-pressed", String(!agents)); bt.setAttribute("aria-pressed", String(agents));
    try { localStorage.setItem("desk-view", v); } catch (e) { /* storage may be blocked */ }
    if (agents) {
      requestAnimationFrame(() => { window.__drawWires && window.__drawWires(); if (first) { first = false; setTimeout(() => window.__replayTeam && window.__replayTeam(), 400); } });
    } else drawCharts();
    if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  }
  bd.addEventListener("click", () => show("dash"));
  bt.addEventListener("click", () => show("agents"));
  let start = location.hash === "#agents" ? "agents" : location.hash === "#dash" ? "dash" : null;
  if (!start) { try { start = localStorage.getItem("desk-view"); } catch (e) { start = null; } }
  show(start === "agents" ? "agents" : "dash");
  let w = innerWidth;
  addEventListener("resize", () => { if (!team.hidden && Math.abs(innerWidth - w) > 10) { w = innerWidth; window.__drawWires(); } });
  // Stay current: reload every 5 minutes while the page is open and visible.
  setInterval(() => { if (document.visibilityState === "visible") location.reload(); }, 5 * 60 * 1000);
})();
</script>
</body>
</html>
"""
