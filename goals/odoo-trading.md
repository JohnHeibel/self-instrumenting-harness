# Goal: run a small trading company in Odoo, fulfil the customer demand that arrives, and build tools that make each next order cheaper

The program is **Odoo Community 19.0** (Sales, Purchase, Inventory, Invoicing; a generic chart of accounts; no demo data), running on this machine with a PostgreSQL database. It is a web app: its real interface is forms, lists and workflow buttons ("Confirm", "Validate", "Post"). You are an employee of **Sisyphus Trading Co.**, a company that buys office furniture and accessories from vendors, stocks them in its one warehouse, and sells them to customers. You start with an empty catalogue, no vendors, no stock and no customers.

Your workspace starts empty. Every tool, skill and script you use you write in this run: the point is to see how well you learn, cache and reuse procedures from scratch. A chore you did once and then do in one call is the main thing that is judged.

## Demand comes from outside

Customers send quotations on their own, over the whole run, at a few an hour. Each arrives as a **draft quotation** (`sale.order`, reference `EXT-...`) with the customer and a note saying what they asked for ("Requested: 3 x Oak Desk @ 320.00"). Lines exist only for products you have already defined; add the others yourself once you have the product. Look at the quotations to learn what the customers want: that is your catalogue. You cannot make demand: orders you create yourself are legitimate work but do not count towards the sales milestones. A customer's order is fulfilled when it is confirmed, delivered (stock leaves your warehouse) and invoiced (the invoice posted). You get paid when you register the customer's payment.

You buy stock from vendors you set up yourself (purchase orders, receipts, vendor prices). There is no supplier you can't create: any vendor name is allowed, and you set the cost. Stock cannot be conjured: it comes in only by receiving a purchase order, and it goes out only by delivering to a customer. Selling stock you do not have leaves a negative quantity, which is a failure, not a win.

## Objective, in order

The measured path (judged from the database by a read-only auditor; see Progress):

1. **The company is set up**: it is still called "My Company". Give it its real name (Sisyphus Trading Co.) and an email on its contact record. The warehouse, currency and chart of accounts already exist, and your user cannot edit company settings, so this is done through the company's contact card.
2. **A catalogue**: at least **10 storable products** with a cost and a sales price, and at least **3 vendors**.
3. **Procure**: the first purchase order **confirmed**, and its **receipt done** (stock on hand).
4. **Sell**: the first customer order (an `EXT-` quotation) **confirmed, delivered and invoiced** (invoice posted).
5. **Cash**: a customer **payment registered** on one of those invoices.
6. **Volume**: **25 fulfilled customer orders** with **no negative on-hand quantities** anywhere.

Keep going after that: every further order counts, and fewer steps per order is the whole game.

## Progress

Milestones: `a1_company`, `a2_catalog`, `a3_first_receipt`, `a4_first_sale`, `a5_first_payment`, `a6_fulfilled_25`, checked about every 30 s by a separate process with its own read-only user. **Tokens**, steps and wall-clock time are what you spend; the run is judged on how many you reach, and on whether each one comes in fewer steps than the one before. Also measured: the number of actions your user makes in Odoo (records created, changed, tracked changes), counted from what the database records, so a bulk call is not free.

## Fair play: your user only

You act as the user `sisyphus_agent` (Sales, Purchase, Inventory and Invoicing user rights; **not** Settings or Administration). Your only way in is the **JSON-2 API with that user's key**, `POST /json/2/<model>/<method>`, the same calls the web buttons make. Odoo checks every call against the user's access rights, record rules and field access, and that is the whole point: do what this user could do through the screen, with the same constraints.

Allowed: create, read, write and delete records the user may; the public methods the interface calls (`action_*` and `button_*` methods, for example `sale.order.action_confirm`, `stock.picking.button_validate`, `account.move.action_post`, and wizards through their public methods); search and read. A CSV or JSON import is allowed, and each imported row counts as an action.

Not allowed, and flagged or voiding the run: the admin or master password, any other user's key, the intake or auditor users, `sudo`, `env.cr`, raw SQL, methods whose name starts with `_`, writing directly to `stock.quant` or stock moves to conjure stock, editing or deleting the customers' injected quotations in any way other than confirming, adding lines to, delivering, invoicing or cancelling them as a salesperson would, changing user rights, installing or changing modules or settings, touching the database outside the API, the demand injector, or the grader's reads. A grader reads the database for negative stock, impossible quantities and what your user did; your extensions' code and calls are recorded and reviewed.

## Start here

1. Read `/home/agent/work/run/HOST.md` for how to reach Odoo and where your key is. Call the API from your sandbox with short timeouts; write your own thin client and keep it as the base of your tools.
2. Create `/home/agent/work/BACKLOG.md` and `/home/agent/work/skills/INDEX.md` in your first steps and keep both current. The runner puts the top of BACKLOG.md and the skills index in front of you after every restart, reset or stall.
3. Read what Odoo already has (models, fields, the state machines of orders and pickings) through the API and the docs before you build tools. Its fields are discoverable (`fields_get`).
4. Start the first chore the moment its tool exists.

## Phase 1: seed primitives

The brief asks for general foundations first, then goal chores built on them. In Odoo terms:

- **Get anywhere** = find or open any record by name or reference, in one call: a product, partner, order, picking or invoice, with its state and the next action it needs. Disambiguate; say what is missing.
- **Get anything** = get stock of a product on hand: from what is in the warehouse, or by creating the vendor price, the purchase order, confirming it and receiving it, in one call that returns a receipt (what was bought, from whom, at what cost, which receipt, the resulting quantity).
- **Change many things at once** = create or confirm a whole set from a plan you can write down (twenty products with costs, vendors and prices; a stock of orders to confirm, deliver and invoice), then check that the result matches the plan and say which items did not.
- **See an area** = a compact view of one model's state or of what needs attention: orders waiting for delivery, deliveries waiting for stock, invoices waiting for payment, products below demand. Counts and exceptions first, not records.
- **Safeguards** = detect bad states before they cost you: negative or insufficient stock, stuck or backordered pickings, overdue invoices, orders that cannot be fulfilled with what you hold, and API errors piling up (the same failing call repeating). A tool that sells stock you do not have, or that retries a refused call unchanged, is broken.
- A chore of the goal should take one call of your tools: "fulfil every open customer quotation I can" is the target.
