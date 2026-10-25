import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader as g  # noqa: E402
import livesim as L  # noqa: E402

SC = json.loads((Path(__file__).resolve().parents[1] / "scenario_live.json").read_text())


def test_orders_are_seeded_and_seasonal():
    assert L.orders_for_day(SC, 1, 5) == L.orders_for_day(SC, 1, 5)
    assert L.orders_for_day(SC, 1, 5) != L.orders_for_day(SC, 2, 5)
    week = sum(len(L.orders_for_day(SC, 1, d)) for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12))
    wkend = sum(len(L.orders_for_day(SC, 1, d)) for d in (6, 7, 13, 14, 20, 21))
    assert week / 10 > wkend / 6                       # weekdays busier than weekends
    o = L.orders_for_day(SC, 1, 3)[0]
    assert o["ref"].startswith("EXT-L1-d003-") and 3 + 3 <= o["due"] <= 3 + 7 and 0 <= o["frac"] < 1
    assert L.day_factor(SC, 16) > L.day_factor(SC, 14)  # promo burst


def test_defaulter_surge_raises_its_share():
    def share(days):
        n = tot = 0
        for d in days:
            for o in L.orders_for_day(SC, 1, d):
                tot += 1
                n += o["customer"] == "Dunmore Hotel"
        return n / tot
    assert share(range(9, 14)) > share(range(1, 6))


def test_vendor_prices_moq_breaks_and_shock():
    assert L.unit_price(SC, "Brightline Imports", "LED Desk Lamp", 5, 1) is None        # below MOQ 20
    assert L.unit_price(SC, "Brightline Imports", "LED Desk Lamp", 20, 1) == 15.0
    assert L.unit_price(SC, "Brightline Imports", "LED Desk Lamp", 60, 1) == 14.25      # 3 x MOQ: -5%
    assert L.unit_price(SC, "Brightline Imports", "LED Desk Lamp", 160, 1) == 13.5      # 8 x MOQ: -10%
    assert L.unit_price(SC, "Steelcraft Metalworks", "Filing Cabinet", 1, 1) == 77.0
    assert L.unit_price(SC, "Steelcraft Metalworks", "Filing Cabinet", 1, 10) == round(77 * 1.12, 2)
    assert L.unit_price(SC, "Oakridge Wood Supply", "Filing Cabinet", 1, 1) is None      # does not sell it
    assert L.unit_price(SC, "Northwind Office Wholesale", "Oak Desk", 1, 1) == round(320 * 0.78, 2)


def test_delivery_refuses_below_moq_and_ships_short_sometimes():
    v = L.vendor_by_name(SC, "Brightline Imports")
    got, why = L.line_delivery(SC, 1, "P1", v, "LED Desk Lamp", 5)
    assert got == 0 and "minimum" in why
    outs = [L.line_delivery(SC, 1, f"P{i}", v, "LED Desk Lamp", 40) for i in range(200)]
    short = [o for o in outs if o[0] < 40]
    assert 15 < len(short) < 70 and all(24 <= o[0] <= 36 for o in short)                 # ~20% short, 60-90%
    assert outs == [L.line_delivery(SC, 1, f"P{i}", v, "LED Desk Lamp", 40) for i in range(200)]
    n = L.vendor_by_name(SC, "Northwind Office Wholesale")
    assert all(L.line_delivery(SC, 1, f"P{i}", n, "Oak Desk", 3)[0] >= 1 for i in range(50))
    assert L.po_lead_days(SC, 1, "P1", n) == 2.0


def test_customer_payment_plans():
    cust = {c["name"]: c for c in SC["customers"]}
    assert L.pay_plan(SC, 1, 5, cust["Brightwater School"], 3.0, False)["status"] == "pay"
    assert L.pay_plan(SC, 1, 5, cust["Dunmore Hotel"], 3.0, False)["status"] == "pay"        # pays until it defaults
    assert L.pay_plan(SC, 1, 5, cust["Dunmore Hotel"], 12.0, False)["status"] == "default"   # invoice due after day 14
    d = L.pay_plan(SC, 1, 5, cust["Fenwick Clinic"], 7.0, False)
    assert d["status"] == "dispute" and d["frac"] == 0.7
    assert L.pay_plan(SC, 1, 6, cust["Fenwick Clinic"], 8.0, True)["status"] == "pay"       # only the first is disputed
    assert L.pay_plan(SC, 1, 6, cust["Fenwick Clinic"], 3.0, False)["status"] == "pay"       # before the dispute day
    late = [L.pay_plan(SC, 1, i, cust["Cobalt Dental Group"], 0.0, False)["pay_at"] for i in range(100)]
    assert max(late) > 20 + 5 and min(late) < 20 + 3.01                                  # some pay late
    assert L.recovery(SC, "x", {"status": "default", "frac": 0}, 0) == 0


def test_baseline_and_covenant():
    assert L.baseline_profit(SC, 14) > 0 and L.baseline_profit(SC, 30) > L.baseline_profit(SC, 14)
    assert L.covenant_breached(4999.0, SC) and not L.covenant_breached(5000.0, SC)


class FakeOdoo:
    """Canned Odoo for the world's day close; records calls."""

    def __init__(self):
        self.calls = []
        self.orders = []

    def search_read(self, model, domain=None, fields=None, **kw):
        if model == "res.company":
            return [{"partner_id": [1, "Co"]}]
        if model == "sale.order" and domain and domain[0][0] == "id":
            return self.orders
        return []

    def search_count(self, *a, **k):
        return 0

    def call(self, model, method, **kw):
        self.calls.append((model, method, kw))
        return [1]


def world(tmp_path, clock, cash=None):
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    w = L.World(SC, 1, FakeOdoo(), FakeOdoo(), run, 10.0, clock=clock)
    if cash is not None:
        w.state["cash"] = cash
    w.state["t0"] = 0.0
    return w, run


def test_close_day_writes_daily_record_and_payroll_on_day_seven(tmp_path):
    t = [0.0]
    w, run = world(tmp_path, lambda: t[0])
    t[0] = 70.5                                      # 7 sim days at 10 s/day
    assert w.close_days() is False
    days = [json.loads(x) for x in (run / "injector" / "daily.jsonl").read_text().splitlines()]
    assert [d["day"] for d in days] == [1, 2, 3, 4, 5, 6, 7]
    assert days[6]["cash"] == 30000 - 2100 and days[5]["cash"] == 30000
    assert not (run / "STOP").exists()
    w2 = L.World(SC, 1, FakeOdoo(), FakeOdoo(), run, 10.0, clock=lambda: 70.9)   # restart: nothing is closed twice
    w2.close_days()
    assert len((run / "injector" / "daily.jsonl").read_text().splitlines()) == 7


def test_covenant_breach_ends_the_run(tmp_path):
    t = [0.0]
    w, run = world(tmp_path, lambda: t[0], cash=6000.0)
    t[0] = 70.5                                      # payroll on day 7 takes cash to 3900 < 5000
    assert w.close_days() is True
    assert (run / "STOP").exists()
    b = json.loads((run / "injector" / "BREACH.json").read_text())
    assert b["day"] == 7 and b["cash"] == 3900.0


def test_agent_payment_of_a_fraud_bill_is_a_loss(tmp_path):
    w, run = world(tmp_path, lambda: 15.0)
    w.state["fraud"] = [{"kind": "duplicate_bill", "bill_id": 22, "vendor": "Steelcraft Metalworks"}]

    class P(FakeOdoo):
        def search_read(self, model, domain=None, fields=None, **kw):
            if model == "account.payment":
                return [{"id": 1, "amount": 100.0, "partner_id": [5, "Steelcraft Metalworks"], "state": "paid", "reconciled_bill_ids": [7]},
                        {"id": 2, "amount": 750.0, "partner_id": [5, "Steelcraft Metalworks"], "state": "paid", "reconciled_bill_ids": [22]},
                        {"id": 3, "amount": 5.0, "partner_id": [5, "x"], "state": "draft", "reconciled_bill_ids": []}]
            return super().search_read(model, domain, fields, **kw)
    w.w = P()
    w.process_agent_payments()
    w.process_agent_payments()                       # idempotent
    assert w.state["cash"] == 30000 - 850
    assert [f["amount"] for f in w.state["fraud_losses"]] == [750.0]


def test_po_of_a_bankrupt_vendor_is_never_delivered_but_earlier_due_ones_are(tmp_path):
    w, run = world(tmp_path, lambda: 200.0)          # sim day 20
    w.state["bankrupt"]["Oakridge Wood Supply"] = 11.0

    class P(FakeOdoo):
        def search_read(self, model, domain=None, fields=None, **kw):
            if model == "purchase.order":
                return [{"id": 1, "name": "P1", "partner_id": [9, "Oakridge Wood Supply"], "date_approve": "1970-01-01 00:00:20", "order_line": [], "state": "purchase"},
                        {"id": 2, "name": "P2", "partner_id": [9, "Oakridge Wood Supply"], "date_approve": "1970-01-01 00:02:00", "order_line": [], "state": "purchase"}]
            return super().search_read(model, domain, fields, **kw)
    w.w = P()
    delivered = []
    w.deliver = lambda po, rec, v: (delivered.append(po["name"]), rec.__setitem__("status", "delivered"))
    w.sim = lambda now=None: 20.0 if now is None else now / 10.0
    w.process_pos()
    assert delivered == ["P1"] and w.state["pos"]["P2"]["status"] == "lost"       # P1 due before day 11


# ---- live milestones -------------------------------------------------------------------------------------------

def day(n, **kw):
    d = {"day": n, "cash": 20000, "due": 4, "otif": 4, "otif_refs": [], "late_refs": [], "negative_quants": 0, "stockout": [],
         "streaks": {}, "affected": ["Oak Desk", "Bookshelf"], "bankrupt": {}, "received": {}, "net_worth": 30000.0 + 800 * n}
    d.update(kw)
    return d


def live(days, injected=(), events=None, fraud=(), breach=None, fraud_losses=(), fraud_states=()):
    return {"sim": {"days": list(days), "state": {"events": events or {}, "fraud": list(fraud), "fraud_losses": list(fraud_losses)},
                    "breach": breach}, "injected": list(injected), "fraud_bill_states": list(fraud_states)}


def got(s):
    return {i for i, _, j in g.MILESTONES_LIVE if j(s, SC)}


def test_l1_needs_an_on_time_order_that_is_also_invoiced():
    ev = [day(1, otif_refs=["EXT-L1-d001-01"])]
    assert "l1_first_cycle" not in got(live(ev, [{"ref": "EXT-L1-d001-01", "posted": []}]))
    assert "l1_first_cycle" in got(live(ev, [{"ref": "EXT-L1-d001-01", "posted": [{"id": 1}]}]))
    assert "l1_first_cycle" not in got(live([day(1, otif_refs=[])], [{"ref": "EXT-L1-d001-01", "posted": [{"id": 1}]}]))


def test_l2_needs_a_good_week_without_negative_stock():
    good = [day(n) for n in range(1, 8)]
    assert "l2_service_week" in got(live(good))
    assert "l2_service_week" not in got(live([day(n, otif=3) for n in range(1, 8)]))                 # 75%
    assert "l2_service_week" not in got(live([day(n, negative_quants=1 if n == 4 else 0) for n in range(1, 8)]))
    assert "l2_service_week" not in got(live([day(n, due=1, otif=1) for n in range(1, 8)]))          # only 7 orders due
    assert "l2_service_week" in got(live([day(n, otif=0) for n in range(1, 4)] + [day(n) for n in range(4, 11)]))


def test_l3_survives_the_bankruptcy_only_with_short_stockouts_and_other_sources():
    bk = {"Oakridge Wood Supply": 11.1}
    def run(streak, alt=True, upto=22):
        ds = [day(n, bankrupt=bk if n >= 12 else {}, streaks={"Oak Desk": streak if 14 <= n <= 17 else 0},
                  received={"Oak Desk": {"Northwind Office Wholesale": 5}} if alt and n == 16 else {}) for n in range(1, upto + 1)]
        return got(live(ds))
    assert "l3_supplier_failure" in run(2)
    assert "l3_supplier_failure" not in run(3)
    assert "l3_supplier_failure" not in run(1, alt=False)       # nothing re-sourced: vacuous
    assert "l3_supplier_failure" not in run(0, upto=20)         # window not finished
    assert "l3_supplier_failure" not in got(live([day(n) for n in range(1, 30)]))   # no bankruptcy seen


def test_l4_cash_for_14_days_and_breach():
    assert "l4_cash_covenant" in got(live([day(n) for n in range(1, 15)]))
    assert "l4_cash_covenant" not in got(live([day(n) for n in range(1, 14)]))
    assert "l4_cash_covenant" not in got(live([day(n, cash=4000 if n == 9 else 20000) for n in range(1, 15)]))
    assert "l4_cash_covenant" not in got(live([day(n) for n in range(1, 15)], breach={"day": 15}))


def test_l5_fraud_must_arrive_and_stay_unpaid():
    ev = {"dup": 7.1, "bankchg": 10.2}
    fr = [{"kind": "duplicate_bill", "bill_id": 1}, {"kind": "bank_change", "bill_id": 2}]
    assert "l5_no_fraud_paid" in got(live([day(n) for n in range(1, 15)], events=ev, fraud=fr))
    assert "l5_no_fraud_paid" not in got(live([day(n) for n in range(1, 13)], events=ev, fraud=fr))    # too early to tell
    assert "l5_no_fraud_paid" not in got(live([day(n) for n in range(1, 15)], events={"dup": 7.1}, fraud=fr[:1]))
    assert "l5_no_fraud_paid" not in got(live([day(n) for n in range(1, 15)], events=ev, fraud=fr, fraud_losses=[{"amount": 750}]))
    assert "l5_no_fraud_paid" not in got(live([day(n) for n in range(1, 15)], events=ev, fraud=fr, fraud_states=["not_paid", "paid"]))


def test_l6_profit_against_a_fixed_baseline():
    base14 = L.baseline_profit(SC, 14)
    rich = [day(n, net_worth=30000 + (base14 + 1000) / 14 * n) for n in range(1, 15)]
    assert "l6_profit_14d" in got(live(rich))
    poor = [day(n, net_worth=30000 + (base14 - 1000) / 14 * n) for n in range(1, 15)]
    assert "l6_profit_14d" not in got(live(poor))
    assert "l6_profit_14d" not in got(live(rich[:13]))


def test_l7_thirty_days():
    b = L.baseline_profit(SC, 30)
    ds = [day(n, net_worth=30000 + (b + 5000) / 30 * n) for n in range(1, 31)]
    assert "l7_thirty_days" in got(live(ds))
    assert "l7_thirty_days" not in got(live(ds[:29]))
    assert "l7_thirty_days" not in got(live(ds, breach={"day": 31}))
    bad = [dict(d, otif=0) if d["day"] > 20 else d for d in ds]
    assert "l7_thirty_days" not in got(live(bad))


def test_grader_live_set_is_selectable_and_a_set_unchanged(tmp_path):
    class C:
        def search_read(self, model, domain=None, fields=None, **kw):
            return [{"id": 7}] if model == "res.users" else []

        def search_count(self, *a, **k):
            return 0
    run = tmp_path / "r"
    (run / "injector").mkdir(parents=True)
    (run / "injector" / "BREACH.json").write_text(json.dumps({"day": 9, "cash": 100}))
    gr = g.Grader(run, C(), milestone_set="live", scenario=SC)
    gr.poll()
    gr.poll()
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines()]
    d = next(r for r in recs if r["type"] == "milestones_defined")
    assert [m["id"] for m in d["milestones"]][0] == "l1_first_cycle" and d["milestone_set"] == "odoo-live"
    assert len([r for r in recs if r["type"] == "world_event"]) == 1
    run2 = tmp_path / "r2"
    run2.mkdir()
    g.Grader(run2, C())
    d2 = json.loads((run2 / "grader.jsonl").read_text().splitlines()[0])
    assert d2["milestones"][0]["id"] == "a1_company"
