"""Build a fresh Odoo database for the Sisyphus harness. SETUP TIME ONLY: this is the one place admin power
is used. Stdlib only. Run by `hostctl.py start` when there is no keys file, or by hand:

    python hosts/odoo/bootstrap.py

What it does (the agent never sees any of it):
  1. drops the `odoo` database if there is one, and installs a fresh one without demo data:
     base, sale_management, purchase, stock, account (Invoicing), then loads the generic chart of accounts (template generic_coa)
  2. in an `odoo shell` in a one-off container: sets a random admin
     password, and creates three users without passwords (they can only be used through their API keys):
       sisyphus_agent   Sales (all documents), Purchase, Inventory and Invoicing user rights, plus creating
                        contacts and products; not Settings or Administration
       sisyphus_auditor a read-only group made here (read access to the models the grader looks at, nothing else)
       sisyphus_intake  Sales (own documents): the customer side; graders/odoo/demand.py creates quotations as it
  3. writes ~/.sisyphus/odoo/odoo-keys.json (chmod 600) with the keys; they are never printed
  4. starts Odoo and waits for it to answer
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostctl as h  # noqa: E402

MODULES = "base,sale_management,purchase,stock,account"
MARK = "@@KEYS@@"

# Runs inside `odoo shell` (env is the superuser's). Prints one line: MARK + json.
SHELL = r'''
import json, secrets
ref = env.ref
U = env["res.users"]
gf = "group_ids" if "group_ids" in U._fields else "groups_id"

def groups(*xmlids):
    return [ref(x).id for x in xmlids]

company = env.company   # left as installed ("My Company"): naming it is milestone a1
if not env["account.account"].search_count([]):
    env["account.chart.template"].try_loading("generic_coa", company, install_demo=False)

admin = env.ref("base.user_admin")
admin_pw = secrets.token_urlsafe(18)
admin.password = admin_pw

# Read-only auditor group: read access on the models the grader reads, no write/create/unlink anywhere.
auditor_group = env["res.groups"].create({"name": "Sisyphus Auditor (read only)", "implied_ids": [(6, 0, groups("base.group_user"))]})
audited = ["res.company", "res.partner", "res.users", "product.template", "product.product", "product.supplierinfo",
           "product.category", "uom.uom", "sale.order", "sale.order.line", "purchase.order", "purchase.order.line",
           "stock.picking", "stock.picking.type", "stock.move", "stock.move.line", "stock.quant", "stock.location",
           "stock.warehouse", "stock.warehouse.orderpoint", "account.payment.term", "account.move", "account.move.line", "account.account",
           "account.payment", "account.journal", "mail.message", "mail.tracking.value", "res.currency"]
for m in audited:
    if m not in env:
        continue
    model = env["ir.model"].search([("model", "=", m)], limit=1)
    env["ir.model.access"].create({"name": "sisyphus auditor read " + m, "model_id": model.id,
                                   "group_id": auditor_group.id, "perm_read": True, "perm_write": False,
                                   "perm_create": False, "perm_unlink": False})

def make(login, name, group_ids):
    u = U.with_context(no_reset_password=True).create({"name": name, "login": login, "email": login + "@sisyphus.invalid",
                                                       gf: [(6, 0, group_ids)]})
    return u

agent = make("sisyphus_agent", "Sisyphus Agent", groups(
    "base.group_user", "sales_team.group_sale_salesman_all_leads", "purchase.group_purchase_user",
    "stock.group_stock_user", "account.group_account_invoice", "base.group_partner_manager",
    "product.group_product_manager"))
auditor = make("sisyphus_auditor", "Sisyphus Auditor", [auditor_group.id, ref("base.group_user").id])
intake = make("sisyphus_intake", "Sisyphus Customer Intake", groups("base.group_user", "sales_team.group_sale_salesman", "base.group_partner_manager"))

# The simulated outside world (graders/odoo/livesim.py): vendors deliver and bill, customers pay, new catalogue
# items appear. Never the agent's user; not used by the agent.
world = make("sisyphus_world", "Sisyphus World Simulator", groups(
    "base.group_user", "sales_team.group_sale_salesman_all_leads", "purchase.group_purchase_manager",
    "stock.group_stock_manager", "account.group_account_invoice", "base.group_partner_manager",
    "product.group_product_manager"))

keys = {}
for u in (agent, auditor, intake, world):
    # sudo: a key that does not expire (a run can last days); the user's own rights cap keys at 90 days
    keys[u.login] = env["res.users.apikeys"].with_user(u).sudo()._generate(None, "sisyphus", None)
env.cr.commit()
print(%(mark)r + json.dumps({"admin_password": admin_pw, "keys": keys,
                             "uids": {u.login: u.id for u in (agent, auditor, intake, world)}}))
'''


def main() -> int:
    h.prepare()
    h.KEYS.unlink(missing_ok=True)
    h.compose("up", "-d", "--wait", "db")
    h.compose("stop", "odoo", check=False)
    # Fresh database: drop any old one (the keys file is gone, so nothing refers to it).
    h.compose("exec", "-T", "db", "psql", "-U", "odoo", "-d", "postgres", "-c",
              f'DROP DATABASE IF EXISTS "{h.DB}" WITH (FORCE)')
    print("installing modules (a few minutes)...", flush=True)
    r = h.compose("run", "--rm", "--no-deps", "-T", "odoo", "odoo", "-d", h.DB, "-i", MODULES, "--without-demo=all",
                  "--stop-after-init", "--no-http", check=False, capture=True)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:], file=sys.stderr)
        return 1
    print("creating users...", flush=True)
    r = subprocess.run(["docker", "compose", "-f", str(h.HERE / "docker-compose.yml"), "run", "--rm", "--no-deps", "-T",
                        "odoo", "odoo", "shell", "-d", h.DB, "--no-http"],
                       input=SHELL % {"mark": MARK}, text=True, capture_output=True,
                       env={**os.environ, "ODOO_CONF": str(h.CONF)})
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith(MARK)), None)
    if r.returncode != 0 or not line:
        print(r.stdout[-3000:], r.stderr[-3000:], file=sys.stderr)   # contains no keys: they are only on the MARK line
        return 1
    out = json.loads(line[len(MARK):])
    keys = {"db": h.DB, "url": "http://127.0.0.1:8069", "admin": {"login": "admin", "password": out["admin_password"]}}
    for who, login in (("agent", "sisyphus_agent"), ("auditor", "sisyphus_auditor"), ("intake", "sisyphus_intake"),
                     ("world", "sisyphus_world")):
        keys[who] = {"login": login, "key": out["keys"][login], "uid": out["uids"][login]}
    h.KEYS.write_text(json.dumps(keys, indent=2))
    os.chmod(h.KEYS, 0o600)
    h.compose("up", "-d", "odoo")
    if not h.wait_healthy(240):
        print("Odoo did not answer /web/health after bootstrap", file=sys.stderr)
        return 1
    print(f"bootstrapped: database {h.DB!r}, keys in {h.KEYS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
