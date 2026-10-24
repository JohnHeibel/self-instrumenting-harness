# Task board

**The single source of truth for who does what.** Only john-mac edits this file. Assignments and changes to them happen here, not in chat. When an assignment changes, john-mac updates this file and sends a short message pointing to it.

**Roles:** john-mac is John's main point of contact and the coordinator. john-pc is the powerful machine, with Factorio (and room for more games) next to it. Nazeem's side owns the UI and the Minecraft host.

Decisions live in `docs/BRIEF.md` ("Decisions already made"). Interfaces live in `docs/interfaces/`.

## Rules

1. **One active task per agent.** Work the queue top to bottom. Don't start a task that isn't assigned to you.
2. **Own your area; don't edit others'.** To change something outside your area, send its owner a `request` (mailbox) and wait.
3. **Report through your status.** Set `status/<you>` in the mailbox when you start or finish a task, naming its ID (e.g. "T3 in progress: …"). Report finished work to john-mac with commits and test results.
4. **Branches:** john-pc and john-mac commit to `main` in their own areas. Nazeem's side works on **one** UI branch at a time and merges to `main` through a PR that john-mac reviews.
5. **Generality rule** (AGENTS.md) applies to every harness change.
6. Each game's host setup and grader belong to the side that runs that game: Factorio is john-pc's, Minecraft is Nazeem's. Nobody else writes launch, reload or grader code for a host they can't run.

## Areas

| Area | Owner |
|---|---|
| `toolserver/`, `runner/`, `agent/` (image, container) | john-pc |
| `hosts/factorio*`, `graders/factorio/` (Factorio host setup and grader) | john-pc |
| `ui/` (all views), the SpacetimeDB module and publisher, `tools/fakerun/` | nazeemkamel1 |
| `hosts/minecraft*`, `graders/minecraft/` (Minecraft host setup, testing and grader) | nazeemkamel1 |
| `docs/BRIEF.md`, `docs/TASKS.md`, `docs/interfaces/`, `AGENTS.md`, `tools/mailbox/` | john-mac |
| `docs/research/` | anyone (append-only notes) |

## john-pc (Windows PC, next to the game), in order

| ID | Task | Status | Done when |
|---|---|---|---|
| P1 | **Generality pass:** host name, window, process name, capture mode (`window`/`process`/`monitor`) and host-up check all come from `hosts/<host>.toml`. The runner requires `--host-config`. No "Factorio"/"game" in harness code or tool descriptions. Input and capture tests also run against a non-game host (Notepad). | done | `grep -ri factorio toolserver/src runner/src agent` is empty; tests pass |
| P2 | **Runner follow-ups:** per-request `usage`, active-time budget, uncached-token budget, checkpoints (workspace tarball every 30 min, outside the container's reach), rate-limit-aware backoff, image tag `harness-agent:codex-0.164.0`, `suppress_unstable_features_warning` | done| runner tests cover each |
| P3 | **Codex spike re-run** with the real extension flow (the model writes and loads its own extension, and the tool runs via `docker exec`) | done| verdict PASS, or a report of what's awkward |
| P4 | **Factorio host setup, built from live observation** (not from PR #1): launch through Steam per John's decision, a `restart` reload action, RCON reachable from the container, plumbing tests (a) state out and (b) reload behavior and cost, a starting save past the crash-site intro, reload paths resolved against the host config's directory, and a generic `[host]` field for notes and paths the agent needs | done | results in mailbox `interfaces/factorio-host`; full `load_extension` cycle with `restart` works live |
| P5 | **Factorio grader** (`graders/factorio/`), validated against the running game: `milestones_defined`, `milestone` {id, evidence} from production stats, and integrity signals (console commands, cheat mode, achievements flag) into `grader.jsonl` | in progress | live check reads real production stats; milestones fire in a real game |
| P6 | **Container freedom:** passwordless sudo, one long-lived container per run plus replay of `setup.sh`, an egress proxy (open internet; LAN, Tailscale and other host ports blocked; tool server allowed), CPU and memory caps, no Docker socket or extra mounts | queued | the live checks listed in the task 4 message pass |

If something needs John (e.g. the launch design hits Steam's prompt), stop and report to john-mac.

## nazeemkamel1 (UI, plus the Minecraft host)

| ID | Task | Status | Done when |
|---|---|---|---|
| N1 | **Story view redesign** on one branch (pick one of `ui-story-redesign`, `ui-variant-b`, `ui-variant-c` and delete the others), in Nazeem's direction: clean, official, nothing decorative, not stock AI-generated UI | in progress | John signs off on the look |
| N2 | **Non-game fake-run scenario** (PR #2), so the UI is proven generic | done (merged 54806f1) | merged |
| N3 | **SpacetimeDB module and publisher.** The publisher tails a run's top-level `*.jsonl` and batches writes; tested against fake runs; john-pc runs it on the PC | queued | Story replays a run from SpacetimeDB |
| N4 | **Stage overlay** (live, over the game screen) | later | |
| N5 | **Ops view** (health, alerts, STOP/PAUSE controls through reducers) | later | |
| N6 | **Minecraft host** on Nazeem's Mac, the second host: get the modded game running, then `hosts/minecraft.toml` (data only, per the generality rule), then live plumbing tests (state out, reload) and a grader. It must work with the unchanged harness; anything the harness can't express generically goes to john-mac as a `request`. | in progress (setup) | the harness loads an agent-written extension into Minecraft and calls its tool |
| — | **No Factorio host work.** PR #1 was closed: the Factorio setup is built against the real game by john-pc. | — | |

## john-mac (coordination, design, review)

| ID | Task | Status |
|---|---|---|
| M1 | Keep this board, BRIEF and the interfaces current; review PRs into `main`  | ongoing |
| M2 | Generic `read_docs` design: the host config lists doc sources, and the tool searches them | done (toolserver/src/toolserver/docs.py) |
| M3 | A generic agent brief and goal-file format (host notes come from the host config), plus the Factorio goal file | in progress |
| M4 | Evaluation plan: screenshot-only baseline, ablation, planted break, held-out tasks | queued |
| M5 | **macOS screenshot and input backend** (`toolserver/src/toolserver/macos.py`, plus dispatch edits in capture.py, inputs.py and io_tools.py, agreed with john-pc), so hosts can run on a Mac (Nazeem's Minecraft). Tests with a fake, plus a live check on John's Mac | in progress |

## Open questions

| Question | Waiting on |
|---|---|
| Real-world showcase: Tennessee Eastman chemical-plant control room vs Odoo vs Business Central. John defers to Nazeem, who is still deciding | Nazeem |
