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


class Grader:
    def __init__(self, run_dir: Path, client: Odoo, agent_login: str = "sisyphus_agent",
                 intake_login: str = "sisyphus_intake") -> None:
        self.run_dir, self.c = run_dir, client
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
        defined = [{"id": i, "name": n} for i, n, _ in MILESTONES]
        if self.defined != defined:
            self.log.write("milestones_defined", milestones=defined, milestone_set="odoo-trading")

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
            elif t == "program_actions":
                self.action_totals = {k: int(v) for k, v in (r.get("totals") or {}).items()}

    def _uid(self, login: str) -> int | None:
        if self.uids.get(login) is None:
            rows = self.c.search_read("res.users", [("login", "=", login)], ["id"], limit=1)
            self.uids[login] = rows[0]["id"] if rows else None
        return self.uids[login]

    def poll(self) -> dict | None:
        try:
            s = collect(self.c, self._uid(self.agent_login), self._uid(self.intake_login))
        except OdooError as e:
            self._status(False, str(e))
            return None
        self._status(True, "Odoo readable as the auditor")
        self._actions(s["action_totals"])
        for mid, _, judge in MILESTONES:
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
    a = ap.parse_args()
    keys = load_keys(a.keys)
    g = Grader(a.run_dir.resolve(), Odoo(keys["auditor"]["key"], keys.get("db", "odoo"), a.url))
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
