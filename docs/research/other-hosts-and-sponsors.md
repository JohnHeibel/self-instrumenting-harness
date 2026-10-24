# Later hosts and sponsor tech

## Odoo (real-world anchor, Hyphenate story)

- Bank reconciliation in Community: the OCA module **`account_reconcile_oca`** is published for **18.0 and 19.0**.
  - Beta, AGPL-3, part of OCA/account-reconcile.
  - Depends on Invoicing (`account`), `mail`, and Bank Statement Base.
  - `account_reconcile_oca_queue` (18.0) runs reconciliation as async jobs.
  - This resolves most of the BRIEF's (verify): Community plus OCA covers the reconcile screen, so ERPNext is only a fallback.
- Docker: no ready image with OCA account-reconcile found. Likely the official `odoo:18` image plus the OCA repo mounted under `addons_path` (unverified).
- Extension system: Python modules (models, views, controllers). An agent hook could be:
  - a controller that returns JSON for the current form, or
  - a server action, or
  - a module that logs `write()` calls.
- Module upgrade (`-u module`) is the load step.
- Oracle: Odoo's external JSON-RPC/XML-RPC API reads the same records, so it can grade hook accuracy.

## Minecraft (later host)

- Fabric hotswap only changes method bodies; anything else needs a restart (or a special JDK). Data packs reload with `/reload`, assets with F3+T.
- "Muto" (Fabric, MC 26.3) claims to reload mods from the title screen (unverified).
- NeoForge: no hot-reload story found. Expect restarts per hook iteration.
- Ceiling: Mineflayer (vanilla only). Gemini team: 27h on Mineflayer (see `prior-art.md`).

## KSP 1 (later host)

- C# plugins, loaded at startup, so a restart per iteration.
- Ceiling: kRPC. Prior LLM work:
  - LLMSat (arXiv 2405.01392) used kRPC and found planning doesn't scale with mission complexity.
  - A 2024 study fine-tuned LLMs for KSP rendezvous via RPC.
- Neither built its own interface.

## SpacetimeDB (Spacetime track: must be the core real-time backend)

- 2.0:
  - TypeScript modules. `spacetime dev --template basic-ts` starts a local server, publishes the module, and generates client bindings.
  - Reducers are transactional RPCs.
  - Clients subscribe with `conn.subscriptionBuilder()` and get a live local cache. React/Vue/Svelte/Solid integrations.
- **2.0 breaking change:**
  - Global reducer callbacks no longer fire for other clients' calls.
  - A dashboard should subscribe to table rows or **event tables** instead.
  - Confirmed reads are on by default, so updates arrive after durability.
- Fit: the primitive server inserts rows (events, hooks, coverage samples) through reducers, and the dashboard subscribes. That makes SpacetimeDB the real-time backend, not a bolt-on.

## Hyphenate track

- The company was formerly **Maximor**, which rebranded after 86x YoY growth. It runs AI agents across the office of the CFO (order-to-cash, treasury, GL/close, procure-to-pay) on top of customers' existing ERPs. That's very close to our "customized ERP is a modpack" story.
- Search found no public page for the Cal Hacks 13.0 track itself; the BRIEF's Google Doc link is the source of truth.

## Sources
- account_reconcile_oca: https://apps.odoo.com/apps/modules/18.0/account_reconcile_oca · https://apps.odoo.com/apps/modules/19.0/account_reconcile_oca
- Fabric hotswapping: https://wiki.fabricmc.net/tutorial:hotswapping
- LLMSat: https://www.arxiv.org/pdf/2405.01392
- SpacetimeDB TS quickstart: https://spacetimedb.com/docs/quickstarts/typescript · upgrade notes https://spacetimedb.com/docs/upgrade · FAQ https://spacetimedb.com/docs/intro/faq
- Hyphenate: https://agilebrandguide.com/maximor-rebrands-as-hyphenate-after-86x-yoy-growth-expanding-across-the-office-of-the-cfo/
