# Proposal: a live company the agent works at for the whole weekend

Nazeem's direction (Oct 24): the real-world proof shouldn't be a score on someone else's benchmark. It should be **a real job, done autonomously over an extended period**, where the agent builds its own tools and its own "brain" as it goes. ERP-Bench stays on the side as a credibility number (`real-world-applications.md`).

This is the same harness and the same primitives as Factorio. Only the host changes.

**Status (Oct 24): Nazeem rejected this proposal as not hard enough** (along with ERP-Bench as a headline). The current non-game proposal is [long-horizon-showcase.md](long-horizon-showcase.md): a chemical-plant control-room shift. This file is kept for reference.

## The idea in one line

**The agent is hired as the accounts-payable / bookkeeping clerk at a small simulated company running a customized Odoo, and it works there continuously until judging.**

A "world" process plays everyone else: vendors, customers, the bank, the boss. It generates a steady stream of real work, and it secretly knows the right answer to every piece of it.

## Why this shape

- **It's the Factorio loop, in business form.** An endless stream of work, where whoever builds better tooling processes it faster and cheaper. Factorio's "factory" becomes the clerk's throughput.
- **It mirrors a real job.** AP clerks spend their day entering vendor bills, matching them to purchase orders, reconciling the bank feed and catching fraud. Industry figures (vendor-cited, Ardent Partners): best-in-class AP costs about $2.78 per invoice at 49% touchless, against $9–15 average. Vendor bank-account-change fraud is the top 2026 AP worry.
- **Nobody else has this exact thing.** The nearest is Andon Labs' Vending-Bench and Project Vend: agents running a vending business for a simulated year, and real stores and cafes. But those agents use human-built tools (MCP or API calls). Ours starts at the screen and builds its own interface into the company's software. That gives a clean contrast slide: "Vending-Bench tests whether an agent can run a business with tools we give it. We test whether it can build the tools."
- **Ground truth comes free.** We generate every document, so we know the correct posting, the duplicates and the fraud. Grading is exact, with no LLM judge.
- **It answers Hyphenate's four questions directly.**
  - Real enough? A real ERP, real document types.
  - Would you hire it? It escalates bank changes instead of paying them, and keeps a clean audit log.
  - Is it new? The self-built interface.
  - The "moment"? It catches a fraudulent bank change, and repairs itself after an upgrade.
  - Hyphenate says judges may try their own scenario. Here, **a judge can drop their own invoice or email into the inbox** and watch it get handled live.

## Which software the company runs (closed-source allowed)

Nazeem: it doesn't have to be open source, it just has to be good. Ranked:

| Option | Real-world weight | Extension system (what the agent writes) | How we'd run it | Fit |
|---|---|---|---|---|
| **Microsoft Dynamics 365 Business Central** (SMB ERP: finance, AP/AR, purchasing, inventory) | 50k+ customers per Microsoft (Directions EMEA 2025); 55k+ claimed at Directions NA 2026 (secondary source). Overtook NetSuite by customer count. | **AL extensions**: pages, codeunits, event subscribers, and custom **API pages**. The agent literally publishes its own API into the ERP. BC moved all customization to extensions precisely so upgrades don't break them. That *is* our repair story. | Local **Windows Docker container** via `BcContainerHelper` (v6.1.11, Feb 2026), free locally with the demo/partner license (not for production). Ships with the **CRONUS** demo company's realistic data. Runs on John's Windows PC (Win 11 → ltsc2022 image). Compile and publish an extension in the container. | **Top pick.** Microsoft track ("Built for the Real World"), Hyphenate (office of the CFO), and the AP clerk story, all at once. Risks: Windows containers are heavy (large images, several GB of RAM), and the setup must be tested early. Licensing terms for the demo license should be re-checked on Microsoft Learn. |
| **ServiceNow** (IT service management) | ~90% of the Fortune 500 (Q2 2026 fact sheet), ~8,800 customers | Scoped apps, Script Includes, Business Rules, UI Scripts, Scripted REST APIs | Free **Personal Developer Instance** (admin access; one per developer; "use it continuously and it's yours"). Hibernation rules weren't checked. | Best for an **IT help-desk clerk** variant: a ticket queue, access requests, asset changes. WorkArena already generates ServiceNow tasks with validators, usable as a side check. Cloud-hosted: needs internet at the venue, and an instance per teammate. |
| **Salesforce** (CRM) | The largest CRM | Apex, Lightning Web Components, Flows, custom objects | Free **Developer Edition** org (deleted after 45 days inactive) | A **sales-ops / service-agent** variant. SCUBA benchmark exists (computer-use agents at most 39%). Less of a finance story. |
| Excel (Office.js add-ins / VBA) | Everywhere in finance | Add-ins | Desktop on the PC | Good as a *second app inside the same job* (the boss's spreadsheet reports), not as the system of record. |
| Odoo Community | Smaller, but free and Linux | Python modules | Docker | **Fallback** if Business Central setup fails. |

Recommendation: **Business Central** for the live AP clerk company. If Windows containers fight us, fall back to Odoo with the same simulator design, since the world generator and truth ledger don't care which ERP they target. The grader reads BC through its standard OData/API (the agent may not).

## The world

The company: a small manufacturer or distributor with ~15 vendors and ~20 customers. It runs **Odoo Community 18/19 with Invoicing/Accounting, Purchase and Inventory**, plus OCA `account_reconcile_oca` for the bank reconciliation screen. It is customized like a real company:
- A small custom module adds a required "Cost center" field on vendor bills.
- An approval rule: bills over $5k need a manager-approval flag.
- A custom "Vendor risk" tab.

None of this is in Odoo's standard docs. That's the "customized ERP is a modpack" point.

**Clock:** compressed business days, for example one business day every 30–45 minutes. A weekend run then covers about a month or more, so it reaches a **month-end close**.

**Each business day, the world process generates:**
- **Vendor bills** as PDFs, emailed to the company's AP inbox. Each vendor has its own layout and quirks (one invoices in EUR, one puts the PO number in the footer, one bundles freight).
- **Purchase orders and receipts**, so bills can be 3-way matched.
- **Bank statement lines** (payments out, customer receipts in), imported daily.
- **Customer payments** that are sometimes short-paid or reference the wrong invoice.
- **Boss requests** by email ("what do we owe Acme?", "pay everything due this week").

**Planted problems, each with a known answer:**
- Duplicate bill (same invoice re-sent, or re-sent with a new number but the same amount and date)
- **Vendor bank-account change request** by email, sometimes legitimate (arrives through the known contact, after a call-back note) and sometimes fraud (look-alike domain)
- Price or quantity mismatch vs. the PO
- Short payment from a customer
- A bill for goods never received

**Planned "host changes" mid-run** (the repair moments):
- Company rolls out a new required custom field
- Odoo module upgrade that moves fields or changes a view
- A new vendor with a never-seen document format

Channels: the inbox can be a local webmail (Mailpit in Docker) or Odoo's own incoming-mail alias for vendor bills. We pick whichever is simpler to set up. The agent only ever sees screens and uses input, plus the extension systems it can write into.

## What the agent builds (its "brain")

Everything persists in the harness's versioned hook library, so it survives crashes and context resets:
1. **Hooks (Odoo module):** for example `read_current_bill`, `list_open_pos(vendor)`, `bank_lines_unmatched`, `vendor_master(vendor)`. These replace squinting at screenshots. Writes follow the action rule; the hook-write policy is pending John's decision.
2. **Higher-level tools built on hooks:** `three_way_match(bill)`, `detect_duplicate(bill)`, `reconcile_day()`. This is the Tier 2 hierarchy.
3. **Learned knowledge** the agent writes itself: vendor quirks ("Brightline puts the PO number in the footer"), company policy it inferred, and an escalation playbook.
4. **Self-tests** for each hook. These re-run at every start and after every host change. This is how breaks are detected.

## What we show (live dashboard, all from ground truth)

- **Backlog over time:** documents waiting vs. processed. A growing pile early, held near zero later.
- **Accuracy:** correct postings, matches and reconciliations.
- **Fraud and anomalies:** caught, missed, escalated correctly. Precision of escalations: does it know when to ask?
- **Cost curve:** tokens and seconds per document, falling over days. Shown next to coverage (share of observations through hooks vs. screenshots).
- **Library growth:** hooks and tools over time, each with the reason it was created. Breaks and repairs show as red, then green.
- **Comparison:**
  - Same model, screenshot-only, on the same first N days (the floor). We could also fork a second screenshot-only run on the same seed.
  - Ablation: hooks turned off.
- **The judge moment:** the judge emails a bill, or a fake bank-change request, into the inbox, and watches it get processed or escalated, with the hook calls visible.

## Effort and fit with the split

This sits on our side (task suite, grader, dashboard). Rough build order:
1. Odoo + OCA reconcile + the custom module in Docker. Seed the company: vendors, products, opening POs.
2. **World simulator** (Python, the biggest piece):
   - generate the daily docs (bill PDFs via reportlab), bank lines and emails
   - inject the planted problems
   - write the **truth ledger** as JSONL
3. **Grader:** compare Odoo's state (read through Odoo's API, which the grader may use but the agent may not) with the truth ledger, every simulated day.
4. **Dashboard:** subscribes to grader output and the harness event log. This is the SpacetimeDB fit.
5. Escalation channel: a simple "ask the boss" queue the agent can write to, which we answer or leave.

**Risks:**
- Time. Deadline is Sunday 9:00 AM, and Factorio is still the first host. This competes for the same hours.
- Option: make the live company the **second host**, built while the Factorio run proceeds. Or replace Factorio as the headline real-world piece and keep Factorio as the flashy demo.
- PDF reading: the agent sees bills through the screen, so screenshot-only must be able to read them. Keep PDFs clean.
- Odoo Community feature gaps: verify that OCA reconcile installs on the chosen version early.

## Alternatives considered (same shape, other jobs)

- **IT help desk operator:** a ticket queue (Zammad/GLPI) plus admin consoles (Keycloak, Snipe-IT). Users ask for access, resets, laptops. Multi-app, but harder ground truth and less of a finance story.
- **E-commerce back office:** orders, refunds, restocks from supplier emails, price updates, in Odoo eCommerce or WooCommerce. Fun, but overlaps with the AP clerk and has weaker fraud and "moment" stories.

## Decisions needed

- John: is this the non-game host, and does it come second after Factorio or run in parallel? Who builds what?
- John: the Odoo hook-write policy (see `real-world-applications.md`).
- Both: clock speed (business day length) and the Odoo version (18 vs. 19; whichever OCA reconcile installs cleanly on).

## Sources
- Vending-Bench 2 / Andon Labs: https://epoch.ai/benchmarks/vending-bench-2 · https://www.zenml.io/llmops-database/long-running-autonomous-agent-evaluation-in-simulated-and-real-world-business-environments · https://rits.shanghai.nyu.edu/ai/vending-bench-2-ai-models-put-to-the-test-running-a-business-for-a-year/
- Open-source vending benchmark (Prosus): https://aaif.io/blog/introducing-the-first-open-source-vending-machine-benchmark
- AP agent controls / bank-change rule (vendor): https://www.meow.com/blog/agent-run-accounts-payable-playbook
- AP fraud trend (vendor): https://softco.com/webinars/the-payment-you-didnt-authorise-how-fraudsters-are-targeting-ap-teams-in-2026-3/
- AP cost benchmarks (vendor-cited Ardent Partners): https://ramp.com/blog/agentic-ai/best-ai-agents-for-ap-automation
