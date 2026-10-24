# Odoo as a test program

## 1. What it is
Odoo is open-source business software: a single web app that bundles the apps a small company runs on. That covers Sales (quotes, orders), Purchase, Inventory (warehouses, stock moves), Manufacturing (bills of materials, work orders), Accounting (invoices, payments, bank reconciliation), CRM, and more. Every app shares one PostgreSQL database. If you confirm a sale order, the program creates a delivery; validating the delivery moves stock; invoicing the order creates journal entries. The interface is mostly forms, lists and workflow buttons ("Confirm", "Validate", "Post"). Odoo is written in Python with a built-in ORM, and its Community edition is licensed LGPLv3 ([Wikipedia](https://en.wikipedia.org/wiki/Odoo), [licenses](https://www.odoo.com/documentation/19.0/legal/licenses.html)).

## 2. Running it locally
- **It is a web app.** The "program" is a browser pointed at `http://localhost:8069`. The agent sees browser screenshots, not a native window.
- **Docker (recommended).** The official image currently lists `20.0`, `19.0` and `18.0`, each with dated tags such as `19.0-20261015`. You run it next to a `postgres` container and mount custom addons at `/mnt/extra-addons` ([Docker Hub](https://hub.docker.com/_/odoo)). Docker gives a reproducible reset: drop the volume and you get a fresh database.
- **Windows installer.** Installers exist for Community and Enterprise, but Odoo calls them only for "testing or running single-user local instances" ([packages](https://www.odoo.com/documentation/19.0/administration/on_premise/packages.html)). They're fine for a quick look, but worse for resettable trials.
- **Version.** Pin a dated `19.0-YYYYMMDD` tag. Odoo 20 came out in late September 2026 and is too new. Version 19 has a year of fixes and supports both the new JSON-2 API and the legacy RPC.
- **Edition.** Use Community, which is free and needs no subscription. Enterprise requires a paid subscription and adds Studio, among other things. Community still includes Sales, Purchase, Inventory, MRP, Invoicing and the Automation Rules engine (`base_automation` is in the Community repo: [GitHub](https://github.com/odoo/odoo/tree/19.0/addons/base_automation)). Full accounting reports and bank-statement import are mostly Enterprise-only, which limits the "close the month" goals below.

## 3. Extension options and "acts like a user"
| Mechanism | Respects business rules? |
|---|---|
| **External API**: JSON-2 `POST /json/2/<model>/<method>` with a bearer API key; XML-RPC/JSON-RPC is deprecated and scheduled for removal in Odoo 22 ([external API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html)) | **Yes.** Each call runs as the key's user, checked against "access rights, record rules, and field access". Only public methods are callable, and methods starting with `_` are not ([security](https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html)). UI buttons call these same public methods (`sale.order.action_confirm`, `stock.picking.button_validate`, `account.move.action_post`). |
| **Custom Python addon** | **Not by default.** Server code can call `sudo()` or raw SQL through `env.cr`, which the docs say means "bypassing all the ORM features". Installing an addon also requires admin. |
| **Server actions / Automation Rules** (Settings → Technical, or Studio in Enterprise) | **Mixed.** "Update record", "Create activity" and email actions go through the ORM. "Execute Code" is arbitrary Python and can `sudo()`. Creating rules needs admin rights ([automation](https://www.odoo.com/documentation/19.0/applications/studio/automated_actions.html)). |

**Proposed rule.** Give the agent a non-admin user with Sales, Purchase, Inventory and Invoicing user rights (not Settings). Its tools may only call the JSON-2 API with that user's key, and only on methods reachable from the UI: CRUD, `action_*` and `button_*` methods. Even if the agent's tool code ships as an addon, it should have no `sudo`, no `env.cr`, and no `_private` calls. A wrapper can enforce this by allow-listing methods. Since the server enforces permissions, the honor system covers only the method allow-list.

## 4. Candidate goals
Each milestone is a read-only `search_count`/`read_group` the grader runs as a separate auditor user.

**A. Trading company (procure → sell → cash)**
1. Company configured (name, currency, fiscal settings) and 1 warehouse.
2. ≥10 storable products with costs and prices, and ≥3 vendors.
3. First purchase order confirmed and its receipt `done`.
4. First sale order confirmed, delivered (`done`) and invoiced (`posted`).
5. Customer payment registered and the invoice's `payment_state = paid`.
6. 25 fulfilled orders with no negative on-hand quants.
7. Reordering rules generate purchase orders automatically, and 100 orders are fulfilled.
8. No overdue receivables older than N days, and vendor bills matched to receipts.

**B. Small manufacturer**
1. Raw materials and finished goods defined.
2. A BoM with ≥3 components, and a work center.
3. First manufacturing order `done`, with components consumed.
4. A multi-level BoM (subassembly) is produced.
5. Make-to-order: a sale order triggers an MO, which triggers purchase orders.
6. 20 finished-good sales delivered on time, with no stockouts.
7. Product cost reflects the BoM roll-up, and the margin is positive.

**C. Books in order**: a ladder of invoices, bills, payments and reconciliation, with every item from the period reconciled. This is weaker on Community because the lock dates and reports are Enterprise-only.

**Why it's hard for an agent.** There are dozens of screens with dense forms, many2one dropdowns and tabs. Wizards pop up (backorders, register payment). State machines punish a step out of order. Most of all, the chores repeat: entering 100 orders by hand would cost a huge number of steps and tokens, so an agent that builds tools gets rewarded.

## 5. Counting user actions
- **Chatter and tracking.** `mail.message` and `mail.tracking.value` record changes to tracked fields on documents with chatter. They're cheap and built in, but miss untracked fields.
- **OCA `auditlog`** ([GitHub](https://github.com/OCA/server-tools/tree/18.0/auditlog)) logs create/write/unlink per user and model. Its 19.0 port is unverified.
- **Best option: a small grader addon.** It overrides `create`, `write` and `unlink` in a mixin, and wraps the JSON-2/RPC dispatcher to log `(user, model, method, n_records, via=api|web)` to a table the agent can't write. Count one action per button/method call, and one per record when calls are batched, so bulk calls aren't free.

## 6. Risks
- **Setup cost.** It's low with Docker (about an hour), but a scripted "company bootstrap" baseline is needed so trials start the same way. Demo data appears to be off by default in 19 (`--with-demo` enables it; [source](https://ca.unraid.net/apps/odoo19-04oaker0972bhb)). Keep it off, or the milestones are pre-satisfied.
- **Too easy.** One RPC loop can confirm 100 orders in seconds. That's legitimate tool-building, but count the actions. The real constraint is that the goal has to require *demand* the agent can't conjure. Have the grader or a scenario script inject customer orders, vendor lead times and price changes over time (the "live company" idea). Then grade on fulfilling external demand, not on records the agent created itself.
- **Too hard.** Accounting configuration (taxes, chart of accounts) can stall a run. Preinstall a localization package (l10n) and the apps the goal needs.
- **CSV import.** Import is a real UI action that goes through the ORM's `load()`, so it respects access rights and constraints. Treat it as legitimate for master data (products, vendors), but count each imported row as an action. Agent-created sale orders or customers don't count toward demand milestones. Workflow transitions can't be done by import anyway.
- **Fresh releases** change the UI yearly, so pin the image digest.

## Recommendation
Odoo is a strong second program. It's real, deterministic, resettable software with a clean permission boundary: a non-admin user plus JSON-2 is exactly "what a user could do". The mechanical cost of its chores also pushes agents to build tools. Use **Community 19.0 in Docker**, goal **A** with injected demand (B as a stretch), and a small grader addon for action counting. Skip accounting-close goals on Community.
