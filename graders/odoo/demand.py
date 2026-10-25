"""Demand injector: puts customer quotations into Odoo over time, as the intake user.

    python graders/odoo/demand.py [--scenario graders/odoo/scenario.json] [--rate-per-hour N]
        [--max-orders N] [--seed N] [--once] [--count K] [--run-dir runs/<run>]

The "customer side" of the company: a separate user (sisyphus_intake, Sales: own documents, key in the keys
file) creates draft sale orders (quotations) that the agent has to confirm, deliver and invoice. The agent
does not control it and must not touch it. What the scenario file defines: customers, the catalogue the
customers ask for (name, list price, quantity range), and the average rate. Order i is fixed by the seed
(customer, items, quantities, and the gap before it), so two runs with the same seed and scenario see the
same demand.

Each injected order carries client_order_ref EXT-<seed>-<i> (4 digits), origin "customer demand", and is
created by the intake user; the grader counts an order as demand only when both hold. Lines: an item that
already exists as a product (matched by exact name, active) becomes a real order line at the scenario's list
price; an item the agent has not defined yet is written into the order's note ("Requested: 3 x Oak Desk @
320.00") and the agent adds the line itself once it has the product. So demand exists before the catalogue.

Restart-safe: the next index is the number of EXT- orders the intake user already created, and the gap
sequence is replayed from the seed, so a restarted injector carries on. It also appends one JSON line per order to
<run-dir>/injector/demand.log (a subfolder, so it stays out of the event log), and always prints it. Stop with a STOP file in the run dir or Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from odoo_client import DEFAULT_KEYS, DEFAULT_URL, Odoo, OdooError, load_keys  # noqa: E402


def order_plan(sc: dict, seed: int, i: int) -> dict:
    """Order i of the scenario, from the seed alone."""
    rng = random.Random(f"{seed}:{i}")
    n = rng.randint(1, max(1, sc.get("max_lines", 3)))
    items = rng.sample(sc["items"], min(n, len(sc["items"])))
    return {"i": i, "customer": rng.choice(sc["customers"]),
            "lines": [{"name": it["name"], "price": it["price"], "qty": rng.randint(*it["qty"])} for it in items]}


def gap_seconds(sc: dict, seed: int, i: int, rate_per_hour: float) -> float:
    """Gap before order i (exponential, mean 3600/rate): a steady drizzle with some clumping."""
    return random.Random(f"{seed}:gap:{i}").expovariate(rate_per_hour / 3600.0)


class Injector:
    def __init__(self, c: Odoo, sc: dict, seed: int, intake_login: str = "sisyphus_intake") -> None:
        self.c, self.sc, self.seed, self.prefix = c, sc, seed, sc.get("ref_prefix", "EXT-")
        rows = c.search_read("res.users", [("login", "=", intake_login)], ["id"], limit=1)
        if not rows:
            raise OdooError(f"no user {intake_login}")
        self.uid = rows[0]["id"]

    def injected(self) -> int:
        return self.c.search_count("sale.order", [("client_order_ref", "=like", self.prefix + "%"),
                                                  ("create_uid", "=", self.uid)])

    def partner(self, name: str) -> int:
        rows = self.c.search_read("res.partner", [("name", "=", name), ("customer_rank", ">=", 0)], ["id"], limit=1)
        if rows:
            return rows[0]["id"]
        return self.c.call("res.partner", "create", vals_list=[{"name": name, "is_company": True, "customer_rank": 1}])[0]

    def inject(self, i: int) -> dict:
        plan = order_plan(self.sc, self.seed, i)
        lines, notes = [], []
        for ln in plan["lines"]:
            prod = self.c.search_read("product.product", [("name", "=", ln["name"])], ["id"], limit=1)
            notes.append(f"Requested: {ln['qty']} x {ln['name']} @ {ln['price']:.2f}")
            if prod:
                lines.append([0, 0, {"product_id": prod[0]["id"], "product_uom_qty": ln["qty"], "price_unit": ln["price"]}])
        ref = f"{self.prefix}{self.seed}-{i:04d}"
        oid = self.c.call("sale.order", "create", vals_list=[{
            "partner_id": self.partner(plan["customer"]), "client_order_ref": ref, "origin": "customer demand",
            "note": "\n".join(notes), "order_line": lines}])[0]
        return {"order_id": oid, "ref": ref, "customer": plan["customer"], "lines": len(lines), "requested": len(notes)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path, default=HERE / "scenario.json")
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--rate-per-hour", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--max-orders", type=int, default=0, help="stop after this many in total (0: no limit)")
    ap.add_argument("--once", action="store_true", help="inject one order now and exit")
    ap.add_argument("--count", type=int, default=1, help="with --once: how many")
    ap.add_argument("--run-dir", type=Path)
    a = ap.parse_args()
    sc = json.loads(a.scenario.read_text())
    seed = sc["seed"] if a.seed is None else a.seed
    rate = a.rate_per_hour or sc["rate_per_hour"]
    keys = load_keys(a.keys)
    inj = Injector(Odoo(keys["intake"]["key"], keys.get("db", "odoo"), a.url), sc, seed)

    def emit(rec: dict) -> None:
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec})
        if a.run_dir:
            (a.run_dir / "injector").mkdir(parents=True, exist_ok=True)
            with (a.run_dir / "injector" / "demand.log").open("a") as f:
                f.write(line + "\n")
        print(line, flush=True)

    if a.once:
        for _ in range(a.count):
            emit(inj.inject(inj.injected() + 1))
        return 0
    while True:
        i = inj.injected() + 1
        if a.max_orders and i > a.max_orders:
            return 0
        wait = gap_seconds(sc, seed, i, rate)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if a.run_dir and (a.run_dir / "STOP").exists():
                return 0
            time.sleep(min(5, max(0, end - time.monotonic())))
        try:
            emit(inj.inject(i))
        except OdooError as e:
            emit({"error": str(e)})
            time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
