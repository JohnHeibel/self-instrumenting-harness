import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demand as d  # noqa: E402
import grader as g  # noqa: E402


class Fake:
    """Stands in for odoo_client.Odoo: canned answers by model, domains inspected only where it matters."""

    def __init__(self, **data):
        self.data = {"company": {"name": "My Company", "currency_id": [1, "USD"]}, "warehouses": 1, "accounts": 50,
                     "storable": 0, "supplierinfo": [], "vendor_partners": [], "po": 0, "receipts": 0, "negq": 0,
                     "orders": [], "moves": [], "users": {"sisyphus_agent": 7, "sisyphus_intake": 8},
                     "created": 0, "tracking": 0}
        self.data.update(data)
        self.calls = []

    def search_count(self, model, domain=None):
        self.calls.append((model, "search_count"))
        x = self.data
        return {"stock.warehouse": x["warehouses"], "account.account": x["accounts"], "product.template":
                x["created"] if ("create_uid", "=", 7) in domain else x["storable"], "purchase.order": x["po"],
                "stock.picking": x["receipts"], "stock.quant": x["negq"],
                "mail.tracking.value": x["tracking"]}.get(model, x["created"] if ("create_uid", "=", 7) in domain else 0)

    def search_read(self, model, domain=None, fields=None, **kw):
        self.calls.append((model, "search_read"))
        x = self.data
        if model == "res.company":
            return [x["company"]]
        if model == "res.users":
            login = domain[0][2]
            return [{"id": x["users"][login]}] if login in x["users"] else []
        if model == "product.supplierinfo":
            return x["supplierinfo"]
        if model == "res.partner":
            return x["vendor_partners"]
        if model == "sale.order":
            return x["orders"]
        if model == "account.move" and domain[0][0] == "id":
            return [m for m in x["moves"] if m["id"] in domain[0][2]]
        return []


def order(n, state="sale", delivery="full", invoices=(), ref=None):
    return {"id": n, "state": state, "delivery_status": delivery, "invoice_ids": list(invoices),
            "client_order_ref": ref or f"EXT-1-{n:04d}"}


def inv(i, state="posted", pay="not_paid"):
    return {"id": i, "state": state, "payment_state": pay, "move_type": "out_invoice"}


def reached(**data):
    s = g.collect(Fake(**data), 7, 8)
    return {i for i, _, j in g.MILESTONES if j(s)}


def test_fresh_database_reaches_nothing():
    assert reached() == set()


def test_company_needs_a_name_and_an_email():
    ok = {"name": "Sisyphus Trading Co.", "email": "orders@x.invalid", "currency_id": [1, "USD"]}
    assert reached(company=ok) == {"a1_company"}
    assert reached(company={**ok, "email": False}) == set()
    assert reached(company={**ok, "name": "My Company"}) == set()
    assert reached(company=ok, accounts=0) == set()


def test_catalog_needs_ten_products_and_three_vendors():
    assert "a2_catalog" not in reached(storable=10, supplierinfo=[{"partner_id": [1, "a"]}, {"partner_id": [2, "b"]}])
    assert "a2_catalog" in reached(storable=10, supplierinfo=[{"partner_id": [1, "a"]}, {"partner_id": [2, "b"]}],
                                   vendor_partners=[{"id": 3}])
    assert "a2_catalog" not in reached(storable=9, vendor_partners=[{"id": 1}, {"id": 2}, {"id": 3}])


def test_first_receipt_needs_po_and_done_receipt():
    assert "a3_first_receipt" not in reached(po=1)
    assert "a3_first_receipt" in reached(po=1, receipts=1)


def test_sale_milestones_count_only_injected_orders():
    # The grader only asks for EXT- orders by the intake user, so agent-made orders never appear in `orders`.
    s = g.collect(Fake(), 7, 8)
    assert s["injected_total"] == 0
    full = dict(orders=[order(1, invoices=[10])], moves=[inv(10)])
    assert reached(**full) == {"a4_first_sale"}
    assert reached(orders=[order(1, delivery="pending", invoices=[10])], moves=[inv(10)]) == set()
    assert reached(orders=[order(1, state="draft", invoices=[10])], moves=[inv(10)]) == set()
    assert reached(orders=[order(1, invoices=[10])], moves=[inv(10, state="draft")]) == set()
    assert reached(orders=[order(1, invoices=[])]) == set()


def test_payment_milestone():
    got = reached(orders=[order(1, invoices=[10])], moves=[inv(10, pay="in_payment")])
    assert got == {"a4_first_sale", "a5_first_payment"}
    assert "a5_first_payment" not in reached(orders=[order(1, invoices=[10])], moves=[inv(10, pay="partial")])


def test_25_fulfilled_and_no_negative_quants():
    orders = [order(n, invoices=[100 + n]) for n in range(1, 26)]
    moves = [inv(100 + n) for n in range(1, 26)]
    assert "a6_fulfilled_25" in reached(orders=orders, moves=moves)
    assert "a6_fulfilled_25" not in reached(orders=orders, moves=moves, negq=1)
    assert "a6_fulfilled_25" not in reached(orders=orders[:24], moves=moves[:24])


def test_milestones_logged_once_and_restart_safe(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    fake = Fake(company={"name": "Sisyphus Trading Co.", "email": "a@b.invalid", "currency_id": [1, "USD"]}, created=5, tracking=2)
    gr = g.Grader(run, fake)
    gr.poll()
    gr.poll()
    g.Grader(run, fake).poll()                        # a restarted grader repeats nothing
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines()]
    assert [r["id"] for r in recs if r["type"] == "milestone"] == ["a1_company"]
    assert len([r for r in recs if r["type"] == "milestones_defined"]) == 1
    acts = [r for r in recs if r["type"] == "program_actions"]
    assert len(acts) == 1 and acts[0]["kinds"]["track"] == 2 and acts[0]["kinds"]["create"] > 0
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))


def test_program_actions_are_deltas(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    fake = Fake(tracking=3)
    gr = g.Grader(run, fake)
    gr.poll()
    fake.data["tracking"] = 5
    gr.poll()
    fake.data["tracking"] = 5
    gr.poll()
    acts = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines() if '"program_actions"' in x]
    assert [a["kinds"]["track"] for a in acts] == [3, 2]


def test_api_down_is_a_status_not_a_crash(tmp_path):
    class Down(Fake):
        def search_read(self, *a, **k):
            raise g.OdooError("connection refused")
    run = tmp_path / "run"
    run.mkdir()
    assert g.Grader(run, Down()).poll() is None
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines()]
    assert recs[-1]["type"] == "grader_status" and recs[-1]["ok"] is False


def test_demand_plan_is_deterministic_and_seeded():
    sc = json.loads((Path(__file__).resolve().parents[1] / "scenario.json").read_text())
    assert d.order_plan(sc, 1, 5) == d.order_plan(sc, 1, 5)
    assert d.order_plan(sc, 1, 5) != d.order_plan(sc, 2, 5)
    assert d.gap_seconds(sc, 1, 3, 12) == d.gap_seconds(sc, 1, 3, 12)
    p = d.order_plan(sc, 1, 1)
    assert 1 <= len(p["lines"]) <= 3 and p["customer"] in sc["customers"]


def test_injector_tags_order_and_skips_unknown_products():
    sc = {"ref_prefix": "EXT-", "max_lines": 1, "customers": ["Acme"], "items": [{"name": "Oak Desk", "price": 320.0, "qty": [2, 2]}]}
    made = []

    class C:
        def search_read(self, model, domain, fields=None, **kw):
            if model == "res.users":
                return [{"id": 8}]
            if model == "product.product":
                return [{"id": 99}] if have else []
            return []

        def search_count(self, model, domain):
            return 0

        def call(self, model, method, **kw):
            made.append((model, kw["vals_list"][0]))
            return [len(made)]

    have = False
    inj = d.Injector(C(), sc, 1)
    r = inj.inject(1)
    sale = [v for m, v in made if m == "sale.order"][0]
    assert r["ref"] == "EXT-1-0001" and sale["client_order_ref"] == "EXT-1-0001" and sale["order_line"] == []
    assert "Requested: 2 x Oak Desk @ 320.00" in sale["note"]
    have = True
    made.clear()
    inj.inject(2)
    sale = [v for m, v in made if m == "sale.order"][0]
    assert sale["order_line"][0][2]["product_id"] == 99
