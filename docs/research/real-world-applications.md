# Real-world applications and a professional showcase benchmark

Goal: show the same harness working outside games, on professional software, with a realistic benchmark we can run, measure and demo. Research from Oct 24, 2026. Vendor statistics are flagged as such.

## The real-world problem, in one paragraph

Companies run on customized systems of record: ERPs, CRMs, legacy Windows apps. Many of these have no API, or an API that doesn't cover the custom fields, modules and workflows each company added.

Today's options are poor:
- **Hand-built integrations and RPA bots break when the UI changes.** Vendors selling AI replacements claim maintenance eats 30–60% of RPA program cost, and EY is cited for 30–50% of initial RPA projects failing. These are vendor-cited, not independent.
- **Raw computer-use agents are slow and unreliable on enterprise software:**
  - SCUBA (Salesforce): under 5% for open models, up to 39% for closed models.
  - ERP-Bench (below): computer-use scores far below API access.
- **Market validation that this gap is real (YC 2026):**
  - **Minicor** turns workflows on API-less Windows desktop apps into API endpoints with "self-healing" UI automation. It targets healthcare, auto and financial services.
  - **RamAIn** does computer-use agents for legacy portals and desktop apps.
  - Both build automations *ahead of time or from recordings*. Neither instruments the app from the inside while working.

Our pitch: the agent starts like a computer-use agent, then **builds itself an API from inside the app** through the app's own extension system, and **repairs it when the app changes**.

## Recommended showcase: ERP-Bench on Odoo 19

**What it is:**
- ERP-Bench (Agentic Labs; arXiv 2605.26321, "Anchor: Mitigating Artifact Drift in Agent Benchmark Generation"; accepted to RLEval '26).
- 300 long-horizon **procurement and manufacturing** tasks in **Odoo 19**, across 29 workflow patterns: buy-and-intake, disruption recovery (supplier cancellation rescue, line outage), make-or-buy, capacity smoothing, multi-stage builds.
- Example task: cover four customer orders with tiered vendor pricelists and min/max quantities, keep a portfolio margin of at least X%, link POs to SOs, set dates, and read vendor Internal Notes for hidden limits. This is what a procurement analyst actually does.

**Why it fits us almost perfectly:**
- **Floor and ceiling are already published, on the same tasks** (pass@5):

  | Model | Coding harness (JSON-2 API + shell) | Browser (Playwright a11y tree) | Computer-use (pixels only) |
  |---|---|---|---|
  | Claude Opus 4.7 | 60.7 | 44.3 | 38.3 |
  | GPT-5.5 | 73.0 | 23.7 | 16.7 |

  That gap between pixels and API (Opus: 38 vs 61; GPT-5.5: 17 vs 73) is exactly what self-instrumentation should close. Our result reads as **"how much of the computer-use to API gap does the agent close by building its own interface?"**
- **State-based verification:**
  - It grades the final Odoo database, not the action trace: constraint satisfaction 25%, optimality vs. a CP-SAT certified optimum 60%, traceability 15%.
  - About 84 rules per task, with hard-zero gates for no-ops.
  - Anti-hacking: prices are re-derived from vendor tiers, not from fields the agent wrote.
  - This is a real answer key, which is what Hyphenate asks for ("proof it works").
- **Runnable:**
  - Repo `agentic-labs/erp-bench`, **CC0 license**.
  - Runs on the Harbor framework, the Terminal-Bench creators' containerized eval runner. It has built-in agents including `claude-code` and `codex`, plus custom agents via `--agent-import-path`.
  - `--env docker` for local runs (Daytona for cloud).
  - One fresh seeded Odoo container per task. `tasks/` (300) and `tasks_ui/` (300 UI variants).
  - It bundles pi-based agents for coding, browser-use and computer-use.
  - A generator can make new tasks at chosen difficulty. **Held-out tasks come free.**
- **Odoo's extension system is plain Python modules.** Hooks are a module with controllers or models that return JSON for the current screen or record. Loading = install/upgrade the module.

**Proposed experiment** (same model, Codex first):
1. **Floor:** computer-use only (screenshots + input), on a subset, for example 20 easy/medium `tasks_ui` tasks. Use the published CU numbers as a sanity check.
2. **Ours:**
   - Same start, plus the right to write and load an Odoo module.
   - Run the tasks *in sequence* in one long session, so the hook library accumulates.
   - Plot reward per task, tokens per task and coverage over the sequence. The expected shape: early tasks look like computer-use; later tasks approach the API ceiling at falling cost.
3. **Ceiling:** coding harness with the JSON-2 API (published numbers, or rerun on the subset).
4. **Held-out:** freshly generated tasks the agent never saw.
5. **Ablation:** our run with its own hooks disabled.
6. **Planted change, the "moment":**
   - Mid-run, upgrade or alter the instance: rename a field, add a required custom field, move a view, or add an approval rule.
   - The hook self-test at session start (or the first failed call) detects the break, and the agent patches its module.
   - The dashboard shows the hook going red, then green.
7. **Customization twist** (the "customized ERP is a modpack" story): add a company-specific custom module, for example a vendor-approval flag or custom fields that tasks depend on. Those things aren't in Odoo's standard docs or ERP-Bench's API instructions.

**Cost and time:**
- Odoo containers are heavy, and tasks are long-horizon. Budget a small subset (10–30 tasks) and run it in the background.
- Harbor + Docker can run on John's PC or any Linux box. It doesn't compete with the Factorio run for a GPU.

## How this reconciles with the BRIEF's Odoo finance scenario

The BRIEF planned a seeded company with planted problems for Hyphenate: short payments, duplicates, a changed vendor bank account. Two options:
- **A. ERP-Bench as the headline result** (procurement/manufacturing), plus a **small custom finance scenario** for the Hyphenate demo moment: duplicate vendor bill, changed bank account flagged. It uses the same hook library, since vendor and PO hooks carry over to bills.
- **B. Build only the finance scenario.** It matches the Hyphenate brief more closely, but we must build the data, answer key and grader ourselves, with no published floor or ceiling.

Recommendation: **A**. ERP-Bench gives credibility (published baselines, solver-certified answers, held-out generation) for almost no build cost. Procure-to-pay is part of the "office of the CFO" Hyphenate sells into. For reconciliation, Odoo Community needs OCA `account_reconcile_oca`; see `other-hosts-and-sponsors.md`.

## Other professional domains (ranked for this hackathon)

| # | Domain / host | Extension system | Who it helps | Existing benchmark | Hackathon fit |
|---|---|---|---|---|---|
| 1 | **ERP: Odoo** | Python modules | Procurement, ops, finance teams on customized ERPs | **ERP-Bench** (above) | Best. Local Docker, CC0, published floor and ceiling. |
| 2 | **Any web SaaS via the browser's extension system** (ServiceNow, Salesforce, internal portals) | Chrome extension / content scripts. The agent writes an extension that reads the DOM and app state and exposes hooks. | IT service desks, sales ops, anyone on SaaS they can't change | WorkArena / WorkArena++ (ServiceNow, 33 / 682 tasks); SCUBA (Salesforce, 300 tasks, CU at most 39%) | Strong generality story ("one extension system covers every web app"). But needs cloud instances (ServiceNow developer instance, Salesforce org), auth and ToS. Good **future-work slide**, or a stretch host. |
| 3 | **Spreadsheets** (Excel / LibreOffice Calc) | Office.js add-ins / VBA; LibreOffice Python macros (UNO) | Analysts, FP&A, accountants | SpreadsheetBench (not re-checked) | Familiar to every judge. A small demo is feasible with LibreOffice in Docker. Less "long-horizon". |
| 4 | **Legacy Windows desktop / SAP GUI** | SAP GUI Scripting (enabled server-side via RZ11); otherwise no extension system, so only UI Automation | Enterprises stuck on desktop systems of record (the Minicor market) | None open | Biggest market, but SAP licensing and no extension system for most legacy apps. **Pitch only.** |
| 5 | **Engineering / GIS** (QGIS, KiCad, FreeCAD) | Python plugins | Engineers, GIS analysts | None found | Fine extension systems, weak benchmark story. |
| 6 | **Healthcare EHR** (OpenEMR) | PHP modules | Clinics | None found | Strong "real world" story, but PHI and safety optics. Avoid as a demo; mention as future work. |
| 7 | **Enterprise IT tools** (EspoCRM, OpenProject, Snipe-IT, iTop) | Varies (PHP/Ruby) | IT and project teams | **EntWorld** (1,756 tasks, SQL-verified, Docker images; GPT-4.1 at 47.6%) | Backup benchmark if ERP-Bench fails to run. Mostly retrieval tasks; the release link wasn't found. |

## Open policy question to settle with John

An Odoo module hook can call the ORM directly, which is API-level power. Under "hooks read anything, actions are what a user could do":
- **Option 1:** hooks *read* via the ORM; *writes* go through UI input.
- **Option 2:** hooks may write, but only by calling the same server methods a button calls (for example `action_confirm`), as the logged-in user (`env.user`, never `sudo()`). That way Odoo's own access rights and business rules apply, and every write is logged and flagged.

Option 2 is more realistic (it's what a human integration developer would write) and is the only way to approach the API ceiling. It's John's call, since it touches the action rule.

## Sources
- ERP-Bench / Anchor: https://arxiv.org/abs/2605.26321 · full text https://arxiv.org/html/2605.26321 · repo https://github.com/agentic-labs/erp-bench · site https://erpbench.ai · Harbor registry https://registry.harborframework.com/datasets/agentic-labs/erp-bench
- Harbor framework: https://mintlify.com/harbor-framework/harbor · https://docs.litellm.ai/docs/projects/Harbor
- EntWorld: https://arxiv.org/html/2601.17722v1
- SCUBA: https://arxiv.org/pdf/2509.26506 · https://www.salesforce.com/blog/scuba-benchmark/
- WorkArena++: https://arxiv.org/pdf/2407.05291v2
- CRMArena-Pro: https://arxiv.org/pdf/2505.18878
- AutomationBench (Zapier): https://primeintellect.ai/blog/zapier-automationbench · https://aiwiki.ai/wiki/automationbench
- Minicor: https://ycombinator.com/companies/minicor · RamAIn: https://yespress.io/ramain-yc-w26
- RPA maintenance figures (vendor): https://ecorpit.com/replace-rpa-computer-use-agents-cost-architecture-2026/ · https://www.lleverage.ai/company/blog/ai-automation-platforms-vs-traditional-rpa-total-cost-comparison-2026
- SAP GUI scripting MCP: https://glama.ai/mcp/servers/vjs1omz0n8
