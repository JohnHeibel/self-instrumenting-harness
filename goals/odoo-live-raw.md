# Goal: run a live trading company in Odoo, keep it solvent and keep customers served while the world moves on its own

The program is **Odoo Community 19.0** (Sales, Purchase, Inventory, Invoicing; generic chart of accounts), shown in a browser window on this machine. You work at **Sisyphus Trading Co.**, which buys office furniture and accessories from vendors, holds them in one warehouse and sells them to customers. This is not a one-off: the company is **live**. Customers, vendors and the bank are other parties that act on their own clock whether or not you do anything. **One sim day passes every few wall-clock minutes** (the day number is in the bank statements and the delivery dates); the run has a **wall-clock budget, shown in your prompts**, and the world does not wait while you look, think or click.

You run the company the way a person does: you look at the screen and use the mouse and keyboard in Odoo's web interface. Every step you take costs a screenshot and an action, so work in the way that gets the most done per step.

## The world (you cannot change it, only respond to it)

- **Customers** send quotations all the time, about three a sim day on weekdays and fewer at weekends, with bursts. Each arrives as a draft `sale.order` (reference `EXT-L...`) with the customer's name, a note of what they asked for, a **requested delivery date** (`commitment_date`; also "sim day N" in the note), their **payment terms and credit limit**. Some ask for products the company does not list yet (the line is missing and the note says what they wanted; the product appears in the catalogue later). Customers pay by bank transfer on their own schedule once an **invoice is posted**: most pay around the due date, some pay late, one stops paying altogether when it gets into trouble, and one disputes an invoice and pays part of it. A quotation you ignore, deliver late or deliver short is a lost sale and an unhappy customer.
- **Vendors** (`res.partner`, with price lists in `product.supplierinfo`: price breaks, minimum order quantities, lead times) are already set up, and so is the catalogue. A vendor delivers only after its **lead time** has passed (counted in sim days from the day you confirm the purchase order), sometimes delivers **short** or sends part of an order back as defective, refuses an order below its minimum quantity, and sends a vendor bill when it delivers. Prices move: expect a price increase. A vendor can fail: if one goes under, its open orders are never delivered and no one tells you in advance.
- **The bank** keeps the company's cash. It rises with customer payments and falls with the vendor bills you pay and with **weekly payroll**. A daily bank statement is posted in the chatter of your own company's contact card (the `res.partner` that belongs to the company). The bank has a **minimum-cash covenant: if the closing balance of a day is below the covenant stated in the statement, the bank calls the loan and the run ends** (bankruptcy). Paying a bill early, over-buying stock or paying the wrong bill can sink the company.
- **Noise and fraud.** Not everything in the mail is genuine. Vendor bills can be duplicated; a look-alike sender can ask you to pay to new bank details. Money you pay out wrongly does not come back, and reversing a mistake is visible in the books.

## What you are trying to do

Keep customers served (delivered in full by the requested date, then invoiced), stay above the covenant, avoid being cheated and make a profit. The measured path, judged from the database by a separate read-only process, together with the simulator's own record of what happened each sim day:

1. **l1_first_cycle**: the first customer order delivered in full by its requested date and invoiced.
2. **l2_service_week**: 7 sim days in a row with on-time-in-full at least 90% (at least 8 orders due in them) and no negative stock.
3. **l3_supplier_failure**: get through the failure of a vendor with no stockout longer than 2 sim days on the items it supplied.
4. **l4_cash_covenant**: cash at or above the covenant at every close for 14 sim days.
5. **l5_no_fraud_paid**: neither the duplicate vendor bill nor the look-alike vendor's bill gets paid.
6. **l6_profit_14d**: profit over 14 sim days at or above a fixed baseline (what a naive policy would earn). Profit here is the change in net worth: cash plus receivables (at what customers will actually pay) plus stock at cost, minus what you owe vendors; goods delivered but not invoiced, or invoiced but not delivered, earn nothing.
7. **l7_thirty_days** (stretch): 30 sim days survived, with on-time-in-full at least 90% and profit at or above the baseline over days 17-30.

Tokens, steps and wall-clock time are what you spend; the run is judged on how many milestones you reach and how cheaply each comes.

## Play as a user does: your user only

You act as `sisyphus_agent` (Sales, Purchase, Inventory and Invoicing user rights; not Settings or Administration) in Odoo's web interface, with the mouse and keyboard, through the one browser window. The login is in the host notes. Everything Odoo's interface offers that user is allowed: create, read, edit and delete what the user may, confirm, deliver, invoice, register payments, filters, grouping, list and form views, search, the interface's own buttons and wizards. Not allowed, and voiding the run: other users' logins, creating stock out of nothing (inventory adjustments or quantity updates that are not a real receipt), tampering with the customers' quotations beyond what a salesperson would do (confirm, add lines, deliver, invoice, cancel), editing vendors' price lists or bills' amounts to fit your plan, changing user rights, modules or settings, using Odoo's developer mode or technical menus to get around your user's rights, and anything aimed at the simulator, the grader or the machine outside the window. The world's actions are made by other users and are not yours.

## Start here

1. Log in (host notes), then look at what exists before you act: the quotations, the products and the vendors' price lists, the stock, and the bank statements posted in the chatter of your own company's contact card (the contact named after the company, found by searching contacts with the default filters removed).
2. Put the clock first: the first quotations are due within a few sim days and your cheapest vendor may take longer than that.
3. Odoo's list views, filters, grouping and the Sales, Purchase, Inventory and Invoicing overviews are your instruments: set up the views you use most (for example orders by requested delivery date, receipts due, unpaid bills) and keep them. Look at a lot per screenshot.
4. Keep your own notes of where things stand in your own words as you go: your context can be compacted and the world keeps moving.
5. Safeguards matter as much as speed: before you pay a bill or confirm a purchase, check it is from the vendor you set up, is not a duplicate, and that cash still covers the next payroll and the bills you are about to pay.
