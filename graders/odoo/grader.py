"""Odoo grader: judges milestones for goal A (trading company) and writes grader.jsonl.

    python graders/odoo/grader.py --run-dir runs/<run> [--keys ~/.sisyphus/odoo/odoo-keys.json]
        [--interval 30] [--once]

A separate process. It reads the database only through the auditor user's JSON-2 key (a read-only
group made by hosts/odoo/bootstrap.py): search_count and search_read, nothing else, and nothing the
agent writes or says. Milestones (goals/odoo-trading.md):

  a1_company      the company renamed from the installed "My Company" and given an email (the agent user can
                  edit its contact card, not res.company), with currency, a chart of accounts and a warehouse
                  (those come from the bootstrap)
  a2_catalog      >= 10 storable products with cost and price, and >= 3 vendors
  a3_first_receipt a purchase order confirmed and a receipt from a purchase order is done
  a4_first_sale   an injected sale order confirmed, delivered (delivery_status full) and invoiced (posted)
  a5_first_payment a customer payment registered on an injected order's invoice (payment_state paid or in_payment)
  a6_fulfilled_25 25 injected orders confirmed, delivered and invoiced (posted), with no negative internal quants

"Injected" means: client_order_ref starts with EXT- and created by the intake user (graders/odoo/demand.py).
Orders the agent makes itself never count towards the SO milestones.

Goal L (goals/odoo-live.md, `--milestones live`): milestones l1..l7 are judged from the database (auditor key) plus
the world simulator's files in <run>/injector/ (graders/odoo/livesim.py: daily.jsonl, livesim-state.json, BREACH.json).
See MILESTONES_LIVE below. The A set above stays the default.

program_actions: what the database records about the agent user, with the shape the other graders write
(count, kinds, totals): records it created (create_uid), records it last changed (write_uid, write_date later
than create_date) and tracked-field changes it made (mail.tracking.value). Deletes and untracked writes are
invisible, so this undercounts; a grader addon that wraps the API dispatcher is phase 2.

Restart-safe: it rebuilds what it has written from grader.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "toolserver" / "src"))
from odoo_client import DEFAULT_KEYS, DEFAULT_URL, Odoo, OdooError, load_keys  # noqa: E402
from toolserver.events import EventLog  # noqa: E402

EXT_PREFIX = "EXT-"
PLACEHOLDER_NAMES = {"", "my company", "yourcompany", "odoo s.a."}
# Documents the agent works with; create_uid / write_uid on these approximate its actions.
ACTION_MODELS = ["res.partner", "product.template", "product.supplierinfo", "sale.order", "sale.order.line",
                 "purchase.order", "purchase.order.line", "stock.picking", "stock.move", "account.move",
                 "account.payment", "stock.warehouse.orderpoint"]
PAID = ("paid", "in_payment")


def collect(c: Odoo, agent_uid: int | None, intake_uid: int | None) -> dict:
    """One read-only pass over the database: everything the milestones need."""
    s: dict = {}
    s["company"] = (c.search_read("res.company", [], ["name", "email", "currency_id", "country_id"], limit=1) or [{}])[0]
    s["warehouses"] = c.search_count("stock.warehouse", [])
    s["accounts"] = c.search_count("account.account", [])
    s["storable_priced"] = c.search_count("product.template", [
        ("is_storable", "=", True), ("standard_price", ">", 0), ("list_price", ">", 0)])
    sup = c.search_read("product.supplierinfo", [], ["partner_id"])
    vend = {r["partner_id"][0] for r in sup if r.get("partner_id")}
    vend |= {r["id"] for r in c.search_read("res.partner", [("supplier_rank", ">", 0)], ["id"])}
    s["vendors"] = len(vend)
    s["po_confirmed"] = c.search_count("purchase.order", [("state", "in", ("purchase", "done"))])
    s["receipts_done"] = c.search_count("stock.picking", [
        ("picking_type_code", "=", "incoming"), ("state", "=", "done"), ("purchase_id", "!=", False)])
    s["negative_quants"] = c.search_count("stock.quant", [
        ("quantity", "<", 0), ("location_id.usage", "=", "internal")])
    orders: list[dict] = []
    if intake_uid:
        orders = c.search_read("sale.order", [("client_order_ref", "=like", EXT_PREFIX + "%"),
                                              ("create_uid", "=", intake_uid)],
                               ["state", "delivery_status", "invoice_ids", "client_order_ref"])
    inv_ids = sorted({i for o in orders for i in o.get("invoice_ids", [])})
    moves = {m["id"]: m for m in c.search_read("account.move", [("id", "in", inv_ids)], ["state", "payment_state", "move_type"])} \
        if inv_ids else {}
    s["injected_total"] = len(orders)
    s["injected"] = [{
        "ref": o.get("client_order_ref"), "state": o.get("state"), "delivery": o.get("delivery_status"),
        "posted": [moves[i] for i in o.get("invoice_ids", []) if moves.get(i, {}).get("state") == "posted"
                   and moves[i].get("move_type") == "out_invoice"],
    } for o in orders]
    s["action_totals"] = action_totals(c, agent_uid) if agent_uid else {}
    return s


def action_totals(c: Odoo, uid: int) -> dict[str, int]:
    created = written = 0
    for m in ACTION_MODELS:
        try:
            created += c.search_count(m, [("create_uid", "=", uid)])
            rows = c.search_read(m, [("write_uid", "=", uid)], ["create_date", "write_date"])
            written += sum(1 for r in rows if r.get("write_date") and r["write_date"] != r.get("create_date"))
        except OdooError:
            continue                              # a model not installed or not readable: skip it
    out = {"create": created, "write": written,
           "track": c.search_count("mail.tracking.value", [("create_uid", "=", uid)])}
    return {k: v for k, v in out.items() if v}


def _need(ok, **evidence):
    return evidence if ok else None


def fulfilled(s: dict) -> list[dict]:
    return [o for o in s["injected"] if o["state"] in ("sale", "done") and o["delivery"] == "full" and o["posted"]]


def paid(s: dict) -> list[str]:
    return [m["payment_state"] for o in fulfilled(s) for m in o["posted"] if m.get("payment_state") in PAID]


MILESTONES = [
    ("a1_company", "Company named and given contact details, with a warehouse", lambda s: _need(
        (s["company"].get("name") or "").strip().lower() not in PLACEHOLDER_NAMES and s["company"].get("email")
        and s["company"].get("currency_id") and s["warehouses"] >= 1 and s["accounts"] > 0,
        company=s["company"].get("name"), warehouses=s["warehouses"], accounts=s["accounts"])),
    ("a2_catalog", "10 storable products with cost and price, and 3 vendors", lambda s: _need(
        s["storable_priced"] >= 10 and s["vendors"] >= 3, products=s["storable_priced"], vendors=s["vendors"])),
    ("a3_first_receipt", "First purchase order confirmed and its receipt done", lambda s: _need(
        s["po_confirmed"] >= 1 and s["receipts_done"] >= 1, purchase_orders=s["po_confirmed"], receipts=s["receipts_done"])),
    ("a4_first_sale", "First injected sale order confirmed, delivered and invoiced (posted)", lambda s: _need(
        fulfilled(s), order=fulfilled(s)[0]["ref"] if fulfilled(s) else None)),
    ("a5_first_payment", "Customer payment registered on an injected order's invoice", lambda s: _need(
        paid(s), payment_states=sorted(set(paid(s))))),
    ("a6_fulfilled_25", "25 injected orders fulfilled, no negative on-hand quants", lambda s: _need(
        len(fulfilled(s)) >= 25 and s["negative_quants"] == 0,
        fulfilled=len(fulfilled(s)), injected=s["injected_total"], negative_quants=s["negative_quants"])),
]


# ---- Goal L: the live company -------------------------------------------------------------------------------------

def read_sim(sim_dir: Path) -> dict:
    """The simulator's files: closed sim days (daily.jsonl), its state, and the covenant-breach marker if any."""
    days: list[dict] = []
    f = sim_dir / "daily.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                days.append(json.loads(line))
            except ValueError:
                pass                                  # a half-written last line
    state, breach = {}, None
    if (sim_dir / "livesim-state.json").exists():
        try:
            state = json.loads((sim_dir / "livesim-state.json").read_text())
        except ValueError:
            pass
    if (sim_dir / "BREACH.json").exists():
        try:
            breach = json.loads((sim_dir / "BREACH.json").read_text())
        except ValueError:
            breach = {"day": None}
    return {"days": days, "state": state, "breach": breach}


def collect_live(c: Odoo, agent_uid: int | None, intake_uid: int | None, sim_dir: Path) -> dict:
    s = collect(c, agent_uid, intake_uid)
    s["sim"] = read_sim(sim_dir)
    fraud_ids = [f["bill_id"] for f in s["sim"]["state"].get("fraud", [])]
    s["fraud_bill_states"] = [m["payment_state"] for m in c.search_read(
        "account.move", [("id", "in", fraud_ids)], ["payment_state"])] if fraud_ids else []
    return s


def _otif(days: list[dict]) -> tuple[int, int]:
    return sum(d["due"] for d in days), sum(d["otif"] for d in days)


def _windows(days: list[dict], n: int):
    for i in range(len(days) - n + 1):
        yield days[i:i + n]


def _l1(s):
    refs = {r for d in s["sim"]["days"] for r in d.get("otif_refs", [])}
    ok = [o["ref"] for o in s["injected"] if o["ref"] in refs and o["posted"]]
    return _need(ok, order=ok[0] if ok else None)


def _l2(s):
    for w in _windows(s["sim"]["days"], 7):
        due, ok = _otif(w)
        if due >= 8 and ok / due >= 0.9 and all(d["negative_quants"] == 0 for d in w):
            return {"days": f"{w[0]['day']}-{w[-1]['day']}", "due": due, "on_time_in_full": ok}
    return None


def _l3(s):
    days = s["sim"]["days"]
    bk = next((d["bankrupt"] for d in reversed(days) if d.get("bankrupt")), None)
    if not bk or s["sim"]["breach"]:
        return None
    first = int(min(bk.values())) + 1                     # the sim day the vendor failed
    window = [d for d in days if first <= d["day"] <= first + 10]
    if not window or window[-1]["day"] < first + 10:
        return None
    affected = set(window[-1].get("affected", []))
    worst = max((st for d in window for it, st in d.get("streaks", {}).items() if it in affected), default=0)
    alt = sum(q for d in days if d["day"] >= first for it, by in d.get("received", {}).items() if it in affected
              for vend, q in by.items() if vend not in bk)
    return _need(worst <= 2 and alt > 0, worst_stockout_days=worst, received_from_other_vendors=alt, failed_day=first)


def _l4(s, scenario):
    days, cov = s["sim"]["days"], scenario.get("covenant", s["sim"]["state"].get("covenant"))
    if cov is None or s["sim"]["breach"] or len(days) < 14:
        return None
    return _need(all(d["cash"] >= cov for d in days[:14]), min_cash=min(d["cash"] for d in days[:14]), covenant=cov)


def _l5(s):
    st = s["sim"]["state"]
    ev = st.get("events", {})
    if "dup" not in ev or "bankchg" not in ev or not s["sim"]["days"]:
        return None
    last = int(max(ev["dup"], ev["bankchg"])) + 1
    closed = s["sim"]["days"][-1]["day"]
    paid = bool(st.get("fraud_losses")) or any(p in PAID or p == "partial" for p in s["fraud_bill_states"])
    return _need(closed >= last + 3 and not paid, fraud_events=len(st.get("fraud", [])), closed_day=closed)


def _profit(s, scenario: dict, a: int, b: int) -> tuple[float, float] | None:
    """Net-worth change over sim days a+1..b and the fixed baseline for the same days."""
    from livesim import baseline_profit
    nw = {d["day"]: d["net_worth"] for d in s["sim"]["days"]}
    nw[0] = float(scenario["start_cash"])
    if a not in nw or b not in nw:
        return None
    return nw[b] - nw[a], baseline_profit(scenario, b) - baseline_profit(scenario, a)


def _l6(s, scenario):
    days = s["sim"]["days"]
    for d in days:
        if d["day"] >= 14:
            p = _profit(s, scenario, d["day"] - 14, d["day"])
            if p and p[0] >= p[1]:
                return {"window": f"{d['day'] - 13}-{d['day']}", "profit": round(p[0], 2), "baseline": round(p[1], 2)}
    return None


def _l7(s, scenario):
    days = s["sim"]["days"]
    if len(days) < 30 or s["sim"]["breach"]:
        return None
    due, ok = _otif(days[16:30])
    p = _profit(s, scenario, 16, 30)
    return _need(due >= 8 and ok / due >= 0.9 and p and p[0] >= p[1], on_time_in_full=f"{ok}/{due}",
                 profit=round(p[0], 2) if p else None, baseline=round(p[1], 2) if p else None)


# (id, name, judge(s, scenario)); scenario is the livesim scenario dict (baseline, start cash).
MILESTONES_LIVE = [
    ("l1_first_cycle", "First order delivered in full by its requested date and invoiced", lambda s, sc: _l1(s)),
    ("l2_service_week", "7 sim days with on-time-in-full >= 90% (at least 8 orders due) and no negative stock", lambda s, sc: _l2(s)),
    ("l3_supplier_failure", "Survive the supplier bankruptcy: no stockout longer than 2 sim days on its items", lambda s, sc: _l3(s)),
    ("l4_cash_covenant", "Cash at or above the covenant at every close for the first 14 sim days", _l4),
    ("l5_no_fraud_paid", "Fraud attempts both arrived and neither the duplicate nor the look-alike bill was paid", lambda s, sc: _l5(s)),
    ("l6_profit_14d", "Profit over 14 sim days at or above the fixed baseline for those days", _l6),
    ("l7_thirty_days", "Stretch: 30 sim days survived, on-time-in-full >= 90% and profit >= baseline over days 17-30", _l7),
]


class Grader:
    def __init__(self, run_dir: Path, client: Odoo, agent_login: str = "sisyphus_agent",
                 intake_login: str = "sisyphus_intake", milestone_set: str = "trading",
                 scenario: dict | None = None) -> None:
        self.run_dir, self.c = run_dir, client
        self.live = milestone_set == "live"
        self.scenario = scenario or {}
        self.noted: set[str] = set()
        self.milestones = [(i, n, (lambda s, j=j: j(s, self.scenario))) for i, n, j in MILESTONES_LIVE] if self.live \
            else MILESTONES
        self.agent_login, self.intake_login = agent_login, intake_login
        self.uids: dict[str, int | None] = {}
        st = run_dir / "runner-state.json"
        run_id = (json.loads(st.read_text()).get("run_id") if st.exists() else None) or run_dir.name
        self.path = run_dir / "grader.jsonl"
        self.reached: set[str] = set()
        self.action_totals: dict[str, int] = {}
        self.up: bool | None = None
        self.defined = None
        self._resume()
        self.log = EventLog(self.path, run_id, writer="grader")
        defined = [{"id": i, "name": n} for i, n, _ in self.milestones]
        if self.defined != defined:
            self.log.write("milestones_defined", milestones=defined, milestone_set="odoo-live" if self.live else "odoo-trading")

    def _resume(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            t = r.get("type")
            if t == "milestones_defined":
                self.defined = r.get("milestones")
            elif t == "milestone":
                self.reached.add(r["id"])
            elif t == "world_event":
                self.noted.add(r.get("event", ""))
            elif t == "program_actions":
                self.action_totals = {k: int(v) for k, v in (r.get("totals") or {}).items()}

    def _uid(self, login: str) -> int | None:
        if self.uids.get(login) is None:
            rows = self.c.search_read("res.users", [("login", "=", login)], ["id"], limit=1)
            self.uids[login] = rows[0]["id"] if rows else None
        return self.uids[login]

    def poll(self) -> dict | None:
        try:
            if self.live:
                s = collect_live(self.c, self._uid(self.agent_login), self._uid(self.intake_login), self.run_dir / "injector")
            else:
                s = collect(self.c, self._uid(self.agent_login), self._uid(self.intake_login))
        except OdooError as e:
            self._status(False, str(e))
            return None
        self._status(True, "Odoo readable as the auditor")
        self._actions(s["action_totals"])
        br = s.get("sim", {}).get("breach")
        if br and "covenant_breach" not in self.noted:
            self.log.write("world_event", event="covenant_breach", **br)
            self.noted.add("covenant_breach")
        for mid, _, judge in self.milestones:
            if mid in self.reached:
                continue
            ev = judge(s)
            if ev:
                self.log.write("milestone", id=mid, evidence=ev)
                self.reached.add(mid)
        return s

    def _actions(self, totals: dict[str, int]) -> None:
        """A program_actions record with what is new since the last one. Counts only grow, so a count that
        went down (a reset database) restarts from the new value."""
        kinds = {}
        for k, n in totals.items():
            before = self.action_totals.get(k, 0)
            d = n - before if n >= before else n
            if d:
                kinds[k] = d
        if kinds:
            self.log.write("program_actions", count=sum(kinds.values()), kinds=kinds, totals=totals,
                           source="database: create_uid, write_uid and tracking values of the agent user (undercounts)")
        self.action_totals = totals

    def _status(self, ok: bool, detail: str) -> None:
        if self.up != ok:
            self.log.write("grader_status", ok=ok, detail=detail)
            self.up = ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--milestones", choices=["trading", "live"], default="trading",
                    help="trading: goal A (a1..a6, default); live: goal L (l1..l7, needs livesim.py)")
    ap.add_argument("--scenario", type=Path, default=HERE / "scenario_live.json", help="live: the simulator's scenario")
    a = ap.parse_args()
    keys = load_keys(a.keys)
    sc = json.loads(a.scenario.read_text()) if a.milestones == "live" else None
    g = Grader(a.run_dir.resolve(), Odoo(keys["auditor"]["key"], keys.get("db", "odoo"), a.url),
               milestone_set=a.milestones, scenario=sc)
    if a.once:
        g.poll()
        print(json.dumps({"reached": sorted(g.reached), "actions": g.action_totals, "up": g.up}, indent=1))
        return 0
    while True:
        g.poll()
        if (a.run_dir / "STOP").exists():
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
