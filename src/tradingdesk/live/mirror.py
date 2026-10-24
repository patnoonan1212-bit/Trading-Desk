"""Mirroring the desk's trades at Tradovate (the Execution Agent's broker side).

The desk keeps deciding exactly as in paper trading, on its simulated "twin" account. After each update, this
module makes the Tradovate account match the twin:

  positions   for each contract, the broker should hold what the twin holds (same month, same size, same side).
              Differences are fixed with market orders. Positions in an old contract month (after a roll) are closed.
  stops       every broker position gets ONE real stop order at the twin's stop price, for the full size, held at
              Tradovate. If the desk goes offline, the stop still protects the position.
  late entry  a new position is only copied if the desk's entry is recent (`broker.entry_window_minutes`) and price
              hasn't run away (`broker.max_entry_slip_r`). Otherwise that trade is skipped at the broker and the
              skip is reported; exits and stop changes are always copied.

Safety net, independent of the twin: the real account's own value is checked every update. If it is down more
than the daily loss limit, everything is closed and the mirror stands aside until the next trading day.

Dry run (the default): nothing is sent. The orders it would send are written down and reported, and an imaginary
broker account is kept so only changes are reported, not the same order every update. No Tradovate account needed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tradingdesk.brokers.tradovate import TradovateClient, TradovateError, tradovate_symbol
from tradingdesk.config import Settings
from tradingdesk.contracts import root_of, spec
from tradingdesk.timeutil import ET

log = logging.getLogger(__name__)


@dataclass
class Target:
    root: str
    contract: str          # Tradovate symbol, e.g. MESZ6
    qty: int               # signed: + long, - short
    stop: float | None
    trade_id: int
    opened_at: pd.Timestamp | None
    entry: float
    risk_points: float
    last_price: float


@dataclass
class MirrorResult:
    actions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # things you should know about (become Watchdog alerts)
    skipped: list[str] = field(default_factory=list)
    halted: str = ""
    ran: bool = False


def targets(engine, data: dict[str, pd.DataFrame]) -> dict[str, Target]:
    """What the twin holds right now, in real contract prices."""
    out = {}
    for t in engine.open_trades:
        if not t.position or t.plan.symbol not in data or data[t.plan.symbol].empty:
            continue
        last = data[t.plan.symbol].iloc[-1]
        adj = float(last["adjustment"])   # 0 for the current contract; kept for safety
        out[t.plan.symbol] = Target(
            root=t.plan.symbol, contract=tradovate_symbol(str(last["contract"])), qty=int(t.side * t.position),
            stop=float(t.stop) - adj if t.stop else None, trade_id=t.record.id, opened_at=t.record.opened_at,
            entry=float(t.avg_entry) - adj, risk_points=abs(float(t.avg_entry) - float(t.plan.stop)), last_price=float(last["raw_close"]))
    return out


class BrokerMirror:
    name = "execution"

    def __init__(self, settings: Settings, state_dir: str | Path, client: TradovateClient | None = None) -> None:
        self.s = settings
        self.b = settings.broker
        self.path = Path(state_dir) / "mirror.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.client = client

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=1, default=str))

    @property
    def label(self) -> str:
        return f"Tradovate {self.b.environment}" + (" (dry run, nothing sent)" if self.b.dry_run else "")

    # ----- broker state: real, or imaginary in a dry run ---------------------------------------------------
    def _read(self, account) -> tuple[dict[str, int], list[dict]]:
        if self.b.dry_run:
            v = self.state.setdefault("virtual", {"positions": {}, "stops": {}})
            stops = [{"id": f"dry-{c}", "contract": c, "type": "Stop", "action": "Sell" if q > 0 else "Buy", "qty": abs(q),
                      "stopPrice": v["stops"].get(c)} for c, q in v["positions"].items() if v["stops"].get(c) is not None]
            return dict(v["positions"]), stops
        pos = {}
        for p in self.client.positions():
            if p.get("accountId") == account["id"] and p.get("netPos"):
                pos[self.client.contract_name(p["contractId"])] = int(p["netPos"])
        orders = [o for o in self.client.working_orders() if o.get("accountId") == account["id"]]
        return pos, orders

    def _market(self, account, contract: str, qty: int, why: str, res: MirrorResult) -> None:
        action = "Buy" if qty > 0 else "Sell"
        cap = self.s.execution.max_contracts_per_order
        if abs(qty) > cap:
            res.problems.append(f"Refused to send {action.lower()} {abs(qty)} {contract}: above the {cap}-contract safety limit.")
            return
        res.actions.append(f"{action} {abs(qty)} {contract} at market ({why}).")
        if self.b.dry_run:
            v = self.state["virtual"]["positions"]
            v[contract] = v.get(contract, 0) + qty
            if v[contract] == 0:
                v.pop(contract)
                self.state["virtual"]["stops"].pop(contract, None)
            return
        self.client.place_order(account, contract, action, abs(qty), "Market")

    def _stop(self, account, contract: str, qty: int, price: float, existing: list[dict], res: MirrorResult) -> None:
        """Exactly one stop for the whole position at `price`."""
        action = "Sell" if qty > 0 else "Buy"
        mine = [o for o in existing if o["contract"] == contract and o.get("type") == "Stop"]
        keep = mine[0] if mine else None
        for extra in mine[1:]:
            res.actions.append(f"Cancel extra stop order on {contract}.")
            if not self.b.dry_run:
                self.client.cancel_order(extra["id"])
        same = keep and keep.get("action") == action and int(keep.get("qty") or 0) == abs(qty) and \
            abs(float(keep.get("stopPrice") or 0) - price) < 1e-9
        if same:
            return
        if keep and keep.get("action") == action:
            res.actions.append(f"Move the {contract} stop to {price:g} for {abs(qty)} contract(s).")
            if not self.b.dry_run:
                self.client.modify_order(keep["id"], abs(qty), "Stop", stop_price=price)
        else:
            if keep:
                res.actions.append(f"Cancel the {contract} stop on the wrong side.")
                if not self.b.dry_run:
                    self.client.cancel_order(keep["id"])
            res.actions.append(f"Place a stop: {action.lower()} {abs(qty)} {contract} if price reaches {price:g}.")
            if not self.b.dry_run:
                self.client.place_order(account, contract, action, abs(qty), "Stop", stop_price=price)
        if self.b.dry_run:
            self.state["virtual"]["stops"][contract] = price

    def _account(self) -> dict | None:
        if self.b.dry_run:
            return {"id": 0, "name": "dry-run"}
        accts = self.client.accounts()
        if self.b.account:
            accts = [a for a in accts if a.get("name") == self.b.account]
        return accts[0] if accts else None

    # ----- the safety net -----------------------------------------------------------------------------------
    def _safety(self, account, now: pd.Timestamp, res: MirrorResult) -> bool:
        """True if the account is down past the daily limit (then everything is closed for the day)."""
        if self.b.dry_run:
            return False
        day = f"{(now.tz_convert(ET) + pd.Timedelta(hours=6)):%Y-%m-%d}"
        if self.state.get("halted_day") == day:
            res.halted = self.state.get("halted_reason", "Daily loss limit reached at the broker.")
            return True
        bal = self.client.cash_balance(account["id"])
        value = bal.get("netLiq") or (float(bal.get("totalCashValue", 0)) + float(bal.get("openPnL", 0)))
        if not value:
            res.problems.append("Couldn't read the Tradovate account value, so the broker-side loss check didn't run.")
            return False
        d = self.state.setdefault("day", {})
        if d.get("day") != day:
            self.state["day"] = d = {"day": day, "start": float(value)}
        change = (float(value) - d["start"]) / d["start"] * 100
        if change <= -self.s.risk.max_daily_loss_pct:
            reason = (f"Safety net: the Tradovate account is down {abs(change):.1f}% today (limit {self.s.risk.max_daily_loss_pct}%). "
                      "Everything was closed; the mirror stands aside until the next trading day.")
            self.state.update(halted_day=day, halted_reason=reason)
            res.halted = reason
            res.problems.append(reason)
            return True
        return False

    # ----- one update ----------------------------------------------------------------------------------------
    def sync(self, engine, data: dict[str, pd.DataFrame], now: pd.Timestamp) -> MirrorResult:
        res = MirrorResult(ran=True)
        if not self.b.dry_run and self.client is None:
            self.client = TradovateClient(self.b.environment)
        try:
            account = self._account()
            if account is None:
                res.problems.append(f"No Tradovate account{' named ' + self.b.account if self.b.account else ''} was found.")
                return res
            halted = self._safety(account, now, res)
            want = {} if halted else targets(engine, data)
            have, orders = self._read(account)
            skipped = set(self.state.get("skipped", []))
            last_bar = max((v.index[-1] for v in data.values() if len(v)), default=now)

            # 1. Positions.
            for root in self.s.contracts:
                t = want.get(root)
                held = {c: q for c, q in have.items() if root_of(c) == root}
                for c, q in held.items():
                    if t is None or c != t.contract:
                        self._market(account, c, -q, "closing: the desk is flat" if t is None else f"contract roll to {t.contract}", res)
                if t is None:
                    continue
                cur = held.get(t.contract, 0)
                if cur == t.qty:
                    continue
                opening = not held   # nothing in any month of this contract; a roll is not a new entry
                if opening:
                    if t.trade_id in skipped:
                        continue
                    age = (last_bar - pd.Timestamp(t.opened_at)).total_seconds() / 60 if t.opened_at is not None else 1e9
                    ran = (t.last_price - t.entry) * (1 if t.qty > 0 else -1) / t.risk_points if t.risk_points else 0
                    if age > self.b.entry_window_minutes or ran > self.b.max_entry_slip_r:
                        skipped.add(t.trade_id)
                        why = f"the desk entered {age:.0f} minutes earlier" if age > self.b.entry_window_minutes else \
                            f"price already ran {ran:.1f}R past the entry"
                        res.skipped.append(f"Didn't copy trade {t.trade_id} ({t.root}) to Tradovate: {why}. It stays paper-only.")
                        continue
                self._market(account, t.contract, t.qty - cur, "copying the desk's position", res)

            # 2. Stops, on what the broker now holds (read again: market orders have filled by now).
            have, orders = self._read(account)
            for c, q in have.items():
                t = want.get(root_of(c))
                if t is not None and t.contract == c and t.stop:
                    price = spec(c).round_to_tick(t.stop, "down" if q > 0 else "up")
                    self._stop(account, c, q, price, orders, res)
                elif not self.b.dry_run:
                    res.problems.append(f"Tradovate holds {q} {c} that the desk doesn't; it should have been closed. Check the account.")
            # Leftover orders on our contracts that protect nothing (e.g. after a position closed).
            for o in orders:
                if root_of(o["contract"]) in self.s.contracts and o["contract"] not in have:
                    res.actions.append(f"Cancel leftover {o.get('type', '')} order on {o['contract']}.")
                    if not self.b.dry_run:
                        self.client.cancel_order(o["id"])
                    elif str(o["id"]).startswith("dry-"):
                        self.state["virtual"]["stops"].pop(o["contract"], None)
            self.state["skipped"] = sorted(skipped)
        except TradovateError as e:
            res.problems.append(str(e))
        finally:
            if not self.b.dry_run and self.b.environment == "demo":
                self.state.setdefault("real_since", now.isoformat())   # for the live-readiness checklist
            if res.problems:
                self.state["last_problem"] = now.isoformat()
            self.state["last_sync"] = now.isoformat()
            self._save()
        return res
