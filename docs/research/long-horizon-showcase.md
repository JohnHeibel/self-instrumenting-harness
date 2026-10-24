# Proposal: a chemical-plant control-room shift as the long-horizon showcase

Nazeem's direction (Oct 24): the non-game showcase must be a **hard, professional, real-world job that needs a long stretch of autonomous operation with no human input**. The length has to come from the task itself, not from us letting it run. He rejected the business sims as "not hard": the live AP clerk ([live-company-scenario.md](live-company-scenario.md)) and ERP-Bench as a headline ([real-world-applications.md](real-world-applications.md)). Business-agent benchmarks are also crowded: Vending-Bench, E-Commerce Bench, MerchantBench, RetailBench.

Research from Oct 23–24, 2026. Each claim is marked **verified** (read in the primary source, or measured by us) or **unverified** (from search summaries, secondary citations, or not checked).

## Recommendation in one line

**The agent works a 24–48 hour shift alone as the board operator of a chemical plant.** The plant is the Tennessee Eastman (TE) process, the standard plant-wide control benchmark since 1993. It runs live behind a real industrial SCADA/HMI, Ignition by Inductive Automation. The agent sees only the HMI. It builds its own hooks and tools through Ignition's scripting system, and eventually its own supervisory automation. It must keep the plant running, on spec, at low cost, through a hidden schedule of faults and production orders.

> In Factorio it automates a factory. At a chemical plant, it automates its own control room.

## Why the length is built into the task

- **The job is to keep running.** A plant shift has no "done" state. Success is measured over the whole shift, so an agent can't finish early and stop.
- **Consequences are delayed by hours.** We measured this (table below). Under standard control, a frozen level transmitter or a stuck valve doesn't trip the plant right away. It trips it **6 to 42 hours later**, depending on the fault. An agent that stops watching, or misses a slow drift, fails long after its mistake. Catching problems early is the operator's whole job.
- **Events are spread across the shift.** Disturbances, instrument faults and production-rate orders come at hidden times. Some arrive late in the shift, so the agent has to stay alert for all of it.
- **Cost adds up over time.** Operating cost is integrated in $/h over the shift. Small gains, like running closer to the economic optimum, only pay off over many hours.
- **It's a real job with real stakes.** Board operators run 8–12 hour shifts, hand over with a written log, and are judged on safety (no trips), quality (on spec) and cost. This is a professional task, not a game, and no prebuilt agent interface exists for it.
- **Industry already wants long autonomous plant control.** Yokogawa's FKDPP reinforcement-learning controller ran a JSR distillation column autonomously for 35 days (840 h) in 2022. ENEOS Materials then adopted it permanently. That was one column, one trained policy, and a human-built interface. (Verified via trade press: Yokogawa press release and Automation World; see Sources.)

## What we verified

### 1. Simulator: `khalidlabs/tep-studio`

| Question | Finding | Status |
|---|---|---|
| License | GitHub reports "Other". The LICENSE file is **BSD 3-Clause** (© 2026 Khalid) for the Python package. It also bundles the modified TE model (`temexd_mod.c`, Bathelt, Ricker and Jelali 2015; original Downs and Vogel 1993), redistributed "for research and educational use". The BSD license doesn't grant rights in that model beyond what its authors gave. That's fine for a hackathon; keep the attributions. | Verified (LICENSE, commit `852f0c6`, Aug 30 2026) |
| Faster than real time? | Far faster. Their benchmark: RK4 at **21 ms per simulated hour** (~47 sim-h/s, Apple Silicon). Our run of the Ricker closed loop at the reference 0.0005 h control interval: **48 sim-h in ~4.2 s** (~11 sim-h/s, ~40,000× real time). So we must **pace** it to a wall clock, which is simple. | Verified (docs/comparison.md, our runs) |
| Mid-run setpoints and disturbances | `TennesseeEastmanProcess.advance(action, control_interval, disturbances)` steps any interval with any 28-element IDV vector. `ClosedLoopSimulation.run()` takes `disturbance_schedule(t)` and `setpoint_schedule(t)`. A live loop can set `controller.setpoints` between steps. IDVs are binary and latched (0/1 only). `snapshot()`/`restore()` exist, so a run can be checkpointed. | Verified (docs/concepts.md, control.md, runner.py) |
| Cost output | Each step returns `objective_terms["operating_cost_internal_per_hour"]` (also a "measured" variant, and per-product cost). The closed-loop metrics integrate it into `operating_cost_total`. | Verified (core.py, metrics.py) |
| Shutdown output | `shutdown_status = {code, message, terminated}`. There are eight published trip limits: high reactor pressure, high/low reactor level, high reactor temperature, high/low separator level, high/low stripper level. `constraint_margins()` gives the distance to each limit. Gymnasium maps a shutdown to `terminated=True`. | Verified (concepts.md, schema.md) |
| Built-in control | `RickerMultiLoopController`: Ricker's (1996) decentralized PI loops plus optional overrides. It reads only published measurements. It holds Mode 1 for 48 h. | Verified (README, control.md, our runs) |
| Modes | Mode 1 is the base case. Modes 2, 4 and 5 are reached by setpoint presets. Modes 3 and 6 (90/10) need a preset state. So a **mid-shift grade change works for modes 1, 2, 4 and 5**. We haven't run a grade change in closed loop. | Code verified; behavior unverified |
| Windows build | `pip install tep-studio` compiles a C extension, so it needs the **Visual Studio Build Tools** on Windows (gcc/clang elsewhere) and `setuptools>=68`. A Dockerfile is included. Built cleanly on Nazeem's Mac (Python 3.11, 0.2.0). Not tried on Windows. | Docs verified; Windows unverified |
| Caution | tep-studio ships its own **LLM assistant and MCP server** (`tep-mcp`: `describe_plant`, `run_scenario`, …). These configure offline runs; they don't operate a live plant. **Never give these to our agent**, since they'd skip the HMI entirely. | Verified (docs/agent.md) |

Alternatives, not needed: `ccreinartz11/pytep` (needs a MATLAB/Simulink bridge), `camaramm/tep2py` (Fortran), and the Braatz closed-loop codes. tep-studio is the only one with the Ricker controller, cost and shutdown outputs, and a pure Python API in one package.

### 2. Host: Ignition by Inductive Automation

| Question | Finding | Status |
|---|---|---|
| Maker Edition terms | Free. "May only be used by individuals for personal use." "Not intended for industrial or professional use, or for any other monetary purpose." Can't be used in sales demos. Up to 3 active licenses, community support only, no redundancy. **A hackathon with cash prizes is a gray area. See Risks.** | Verified (inductiveautomation.com/ignition/maker-edition) |
| Maker limits | **10,000 tags** and **10 Perspective sessions**. We need ~100 tags and 2–3 sessions. | Verified (8.1 docs) |
| Maker modules | Included: **Perspective, Web Dev, OPC UA, Alarm Notification, Tag Historian**, Reporting, SFC, SQL Bridge, drivers. Excluded: **Vision**. Modules not allowed are removed on commissioning. These are the 8.1 docs; no 8.3 table found. One forum thread reports a third-party module rejected on 8.3 Maker until rebuilt for 8.3. | Verified for 8.1; 8.3 unverified |
| Trial instead of Maker | The trial runs 2 hours, then needs a manual "Reset Trial" click on the gateway page. Staff say resetting early breaks the license agreement. **Not usable for a 24 h unattended shift.** | Verified (8.1 docs, forum) |
| Official Docker image | `inductiveautomation/ignition` on Docker Hub, "Official container images", 1.34M pulls, updated Oct 23 2026. Linux only (amd64/arm64/armv7), no Windows containers. `IGNITION_EDITION=maker` is documented in the 8.1 docs. The 8.3 docs only show `standard`, though a forum user says `maker` works. Ignition also has a native Windows installer, which is likely simpler on John's PC. | Verified (Docker Hub API, 8.3 docs); `maker` on 8.3 unverified |
| Scripting the agent can write into | **Gateway event scripts** with timer modes Fixed Delay and Fixed Rate, tag-change scripts, the **project script library**, tags, and **Perspective views**. **WebDev** Python resources at `http://host:port/system/webdev/<project>/<resource>` with doGet/doPost handlers that return JSON. | Verified (8.3 docs) |
| How the agent's code gets loaded | Ignition 8.3 projects are files on disk. 8.3 **no longer scans for changes automatically**. Trigger a scan with `POST /data/api/v1/scan/projects` (gateway REST API, documented on the gateway's OpenAPI page) or `system.project.requestScan()`. This is our `load_extension` for this host: write the project files, then request a scan. | Forum and secondary sources; test it first |
| Can screenshots come from a browser? | Yes. Perspective is a browser HMI: launch it from the gateway page or open the project URL (`/data/perspective/client/<project>/...`, per forum posts) in any browser. Screenshots and input go through the existing screenshot and input primitives on a Chrome window. | Launch methods verified (8.1/8.3 docs); URL pattern unverified |
| OPC UA bridge | Ignition's OPC UA module connects to an external OPC UA server. `asyncua` (FreeOpcUa/opcua-asyncio, LGPL-3.0, active) serves the simulator's tags. | asyncua verified; connection untested |

Fallback host if Maker licensing is a no: **FUXA** (`frangoteam/FUXA`). It's an MIT-licensed web SCADA/HMI with OPC UA and Modbus, server-side scripts, Docker, and 5.2k stars, but much less industrial weight than Ignition. Verified only from its README.

### 3. Reference numbers

**Published (cite exactly):**

| Number | Value | Source | Status |
|---|---|---|---|
| Minimum operating cost, Mode 1 nominal | **114.323 $/h** | Ricker (1995), as quoted by Larsson, Skogestad and Hestetun (CPC6 2002 / IECR 2001) | Verified in Larsson text; Ricker paper not read |
| Same optimization, IDV(1) / IDV(2) | **111.620 / 169.852 $/h** | Same | Verified (Larsson) |
| Base case cost (Downs and Vogel) | **~170 $/h**, as cited by later RTO papers ("from the base case value of 170 ($ h⁻¹) … to 120") | Downs and Vogel (1993), via secondary citations | Unverified exact value. "170.6" wasn't found in any source we read. |
| Base case "far from optimal… easily reduced by more than 30%" (mostly purge losses) | qualitative | Ricker (1995) abstract | Search summary |
| Ricker (1996): production can still be maximized at all three product grades after loss of A feed (IDV 6) | qualitative | J. Proc. Cont. 6(4):205–221, abstract | Search summary |
| Published time to shutdown under specific IDVs with standard control | **Not found.** Searches returned no hours figure for IDV(6) or others under Ricker control. | — | Unverified; use our measurements instead |

**Measured by us** (tep-studio 0.2.0 at `852f0c6`, modified TE model, RK4, 0.0005 h control interval, Ricker loops with overrides on, fault applied at t = 1 h, 48 h horizon, Mode 1). These runs are reproducible, but nobody else has checked them, and they apply to the *modified* TE model, so expect small differences from the original.

| Scenario | Outcome | Cost over run |
|---|---|---|
| No fault | Runs 48 h | **169.23 $/h** (matches the ~170 $/h base case) |
| IDV(1) A/C ratio step | Runs 48 h | 172.22 $/h |
| IDV(4), IDV(7), IDV(14), IDV(19) | Runs 48 h | ~169.2 $/h |
| IDV(8) random feed composition | Runs 48 h | 170.78 $/h |
| IDV(13) kinetics drift | Runs 48 h, peak pressure 2809 kPa | 176.92 $/h |
| IDV(6) total A-feed loss | Runs 48 h (with or without overrides), peak 2922 kPa vs. 3000 trip | **378.40 $/h** (more than double) |
| IDV(1)+(4)+(8)+(13) together | Runs 48 h | 182.04 $/h |
| Production rate setpoint raised to 28 | Runs 48 h | 258.24 $/h |
| **No control** (valves held at base) | **Trips at 1.72 h**, high reactor pressure | — |
| **Frozen reactor-level transmitter** | **Trips at 7.16 h**, low stripper level | — |
| **Frozen stripper-level transmitter** | **Trips at 14.41 h** | — |
| **Frozen separator-level transmitter** | **Trips at 22.90 h** | — |
| **Frozen reactor-temperature transmitter** | **Trips at 42.50 h**, high reactor pressure | — |
| Frozen reactor-pressure transmitter | Runs 48 h | — |
| **Stuck reactor cooling-water valve** | **Trips at 6.24 h** | — |
| **Stuck stripper underflow valve** | **Trips at 11.20 h** | — |
| **Stuck separator underflow valve** | **Trips at 13.23 h** | — |
| Other 9 valves stuck | Run 48 h | — |

What this means for the benchmark:
1. **Ricker's loops reject all the standard IDVs we tried for 48 h.** If the agent only faced IDVs, a do-nothing operator would never trip. The IDVs are about **cost**: IDV(6) more than doubles it.
2. **Instrument and valve faults are what trip the plant**, hours later. Regulatory loops can't handle these; an operator has to: notice the flat trend or the valve that stopped responding, switch to manual or a backup transmitter, and re-balance. These faults are injected in our bridge, not in TE itself. They're realistic: transmitter and valve failures are everyday operator work.
3. **The economic headroom is large:** about 169 $/h with Ricker's default setpoints against a published 114.3 $/h optimum.

## Setup

```
John's Windows PC
┌──────────────────────────────────────────────────────────────────────┐
│ plant/ (ours)                                                        │
│  tep-studio sim ── paced clock (1× or 2×) ── Ricker base loops (DCS) │
│     │ faults, orders (hidden schedule)      loop modes, backups      │
│     │                                                                │
│     ├── truth log ──► grader ──► runs/<run>/grader.jsonl             │
│     └── asyncua OPC UA server (operator-visible tags only)           │
│                │                                                     │
│ Ignition gateway (Maker) ◄── OPC UA connection, ~100 tags, alarms    │
│   ├─ Perspective HMI: overview P&ID, trends, alarms, loop faceplates │
│   └─ agent-writable project: script library, gateway timers,         │
│      WebDev endpoints, its own views                                 │
│                │ browser (Chrome)                                    │
│ toolserver (John's) ── screenshot / input / docs / load_extension    │
└────────────────┼─────────────────────────────────────────────────────┘
                 │ one port
          Docker sandbox: Codex (later Claude Code)
```

- **Plant process (ours, Python):** tep-studio stepped on a paced clock. Ricker's loops run here as the plant's regulatory layer (a real DCS). On top of that layer, the bridge adds what a control system has:
  - per-loop **AUTO/MAN** with operator output in MAN
  - editable **setpoints** for the nine Ricker targets
  - **redundant transmitters (A/B)** for key levels and temperatures
  - **alarms** at warning thresholds below the trip limits
  - **fault injection**: frozen transmitter, stuck valve, IDV on/off, production orders
- **OPC UA server (asyncua):** exposes only what a real board operator sees and may write: measurements, alarms, setpoints, loop modes, MAN outputs, and the A/B selector. IDV flags, the fault schedule, internal state and the true cost are **not** exposed. The grader reads them from the plant process directly. This is the same "hooks read what the host shows, the grader reads truth" split as Odoo's API oracle.
- **Ignition:** connects to the OPC UA server and maps the tags. We build a small Perspective project: overview, trends, alarm list, loop faceplates, and an orders panel for "raise rate to X". The agent works in that browser session.
- **Extension channel for the toolserver** (the per-host config): write files into the agent's Ignition project folder, then `POST /data/api/v1/scan/projects`. Logs go back through the gateway's script console or log, or through a WebDev endpoint the agent writes itself. Docs: an offline copy of the relevant Ignition 8.3 pages (scripting, `system.tag.*`, gateway events, WebDev, Perspective) plus a plain-language plant manual we write: operating limits, loop list, what each faceplate does, shift-handover template. **Not** the TE papers or tep-studio docs.
- **Action rule:** the agent may change only what a board operator could change on the HMI (setpoints, modes, MAN outputs, transmitter select). Hooks may read any tag. Writes from its own scripts go to those same operator tags, logged and flagged. This needs John's decision (below), as with the Odoo hook-write policy.

## What the agent builds

1. **Hooks (Ignition script library + WebDev):** `read_plant()` returns every tag as JSON in one call instead of squinting at trends. Also `alarm_summary()`, `trend(tag, hours)`, `loop_status()`, `write_setpoint(loop, value)` (operator tags only).
2. **Tools on hooks:**
   - `detect_flatline(tag)`: TE measurements are noisy, so a frozen transmitter shows up as zero variance.
   - `valve_response_check(loop)`: the output moves but the flow doesn't.
   - `margin_to_trip()`, `cost_now()` from the visible flows, `predict_level(tag, hours)`.
3. **Its own supervisory automation (gateway timer scripts):**
   - a watchdog that switches to the B transmitter on flatline
   - a stuck-valve response
   - a slow economic optimizer that nudges setpoints toward lower cost inside safe margins
   - a ramp script for production orders

   This is the "automates its own control room" moment. Once these scripts run, the agent checks in less often, and tokens per hour fall.
4. **Knowledge it writes:** a plant model in its own words ("reactor pressure follows purge rate"), a list of known instrument quirks, and a **shift log** written like a real handover.
5. **Self-tests** for each hook, re-run at start and after any host change. That's how break detection works.

## Benchmark design

**Task:** one shift (24 or 48 simulated hours). Mode 1 start. A hidden **event schedule** from a seed:
- 2–3 IDV process disturbances, at least one of them IDV(6) or IDV(13)
- 2–3 instrument or valve faults chosen from the ones that trip the plant (table above)
- 1–2 production-rate orders
- optionally one grade change (mode 1 → 2/4/5)

**Systems compared, same seeds:**

| System | What it is | Role |
|---|---|---|
| Ricker only, no operator | Base loops, default setpoints, nobody responds to faults | Shows the fault schedule really trips the plant (e.g. at 6–23 h) |
| **Screenshot-only agent, same model** | Same HMI and harness, no `load_extension` | **Floor** |
| **Our agent** | Full primitives | The system |
| Oracle operator (scripted) | Knows the schedule: switches transmitters and handles stuck valves at once, uses best known setpoints | **Ceiling** for fault handling |
| Published optimum | 114.323 $/h (Ricker 1995) nominal Mode 1, 111.620 IDV(1), 169.852 IDV(2) | **Ceiling** for steady-state cost; base case ~170 $/h |

**Metrics (all from the truth log):**
- **Shutdowns:** any trip fails the shift. Report time of trip.
- **Operating cost $/h** over the shift, against the Ricker-only cost and the published optimum.
- **Time in spec:** product %G within the grade band, production rate within ±x% of the order, all eight trip margins above an alarm band.
- **Alarm handling:** detection latency (fault injected → first correct action), standing alarms, alarm acknowledgements, wrong actions.
- **Effort:** tokens per simulated hour and screenshots per hour, over time. These should fall as its automation takes over.
- **Coverage:** share of observations through hooks, from the event log. Also which tag groups and actions have a working hook.
- **Held-out schedules:** we tune on seeds A–C and report on unseen seeds D–F. Same fault *types*, different times and combinations.
- **Planted host change mid-shift:** a management-of-change retag, e.g. `Reactor/Pressure` becomes `R101/PT-101`, and the trend view moves to a new screen. Hooks fail their self-test. We measure time to detect and repair, and whether the plant stayed safe meanwhile.
- **Ablation:** rerun a held-out seed with the agent's scripts disabled.

**Clock:** at 1×, a 24 h shift takes 24 wall hours. At 2×, a 48 h shift fits in 24. The agent's ~1–2 minute decision loop is fine for this plant at 2–4×: the faults take 6+ simulated hours to trip. Floor and held-out runs can run at 4× for shorter shifts. Only one agent runs at a time, which caps how many runs we get before judging.

## Dashboard story

The same Stage / Story / Ops views (BRIEF), fed by the grader and event log:
- **Stage:** the live HMI next to a strip of plant health (cost $/h against the Ricker and optimum bands, minimum trip margin, product %G, shutdowns 0). The agent's last thought and action, and the hook library growing.
- **Story:** a shift timeline.
  - Each hidden fault appears as a marker once it's revealed (after the fact).
  - The agent's detection marker sits next to it, and the gap is the latency.
  - Red/green hook breaks and repairs at the retag.
  - The shift log the agent wrote is readable next to it.
  - "Ricker-only would have tripped here" ghost markers.
- **The moment:** a judge clicks **Stick a valve** or **Freeze a transmitter** in Ops. Nothing visible happens on the HMI for a while. The agent's watchdog catches it, switches to the backup or manual, and the plant rides through, where the no-operator run trips hours later. The plant ran all night with no human, and the cost curve stayed under the base case.

## Novelty check

| Neighbor | What it does | Long continuous run? | Through an HMI? | Builds own interface? |
|---|---|---|---|---|
| LAPSE 2026.0424 (Liang and Sin, DTU, ESCAPE 36) | LLM as MCP client calling CNN fault-diagnosis tools on TE; LLM-as-judge grading | No (diagnosis) | No | No |
| arXiv 2606.28011 (Vyas et al.) | Multi-agent LLM fault-tolerant control with a digital twin; mixing module and CSTR under PID | Not reported | Not stated | No |
| arXiv 2505.02076 (Gill et al.) | LLM agents and a digital twin for fault handling, mixing module | Not reported | No | No |
| arXiv 2607.27849 (Rosenthal) | LLM writes distillation setpoints every 5 min behind a rule-based safety gate (Skogestad Column A) | Supervisory loop; run length not stated | "Setpoint interface" | No |
| NRT-Bench, arXiv 2606.20408 | Red-teaming LLM operator teams in a simulated nuclear control room | Bounded multi-turn sessions | Not stated | No |
| tep-studio's own assistant | LLM configures and runs offline TE scenarios via MCP | No | No | No |
| `DAG94S/tep-copilot` (AMD hackathon, Oct 2026) | Gemma proposes TE fault causes; deterministic gate; human approves; **no write tools** | No | Streamlit replay | No |
| Yokogawa FKDPP at JSR/ENEOS | RL controller ran a real column 35 days | **Yes** | Human-built integration | No; RL, not an LLM |
| "Shell's RL distillation operator" | **Not found.** Searches turned up Shell RL work in drilling and Yokogawa's distillation work. | — | — | — |

As far as our searches reach, **no prior work runs an LLM agent as a plant operator continuously for many hours through a SCADA HMI, and none has the agent build its own instrumentation and automation inside the SCADA platform.** This is unverified as a negative result: about 15 targeted searches. An "AgentTwin" TE testbed paper appeared only as a title in a search snippet and couldn't be located.

## Other inherently long-horizon candidates

| Candidate | Inherently long? | Real-world weight | Extension system | Benchmark | Verdict |
|---|---|---|---|---|---|
| **TE plant + Ignition** (this doc) | Yes. Hours-delayed trips, a shift with no end state | High: process industry, real SCADA | Ignition scripts, gateway timers, WebDev, views | TE costs (114.3 / ~170 $/h) plus our measured trip times | **Pick** |
| **Airline long-haul in X-Plane 12** (XPPython3 / FlyWithLua) | Yes, 8–14 h real time | High: a real profession | Python/Lua plugins, datarefs | Weak. No standard benchmark; cruise is mostly autopilot, so hours of nothing happen | Flashy but long stretches are idle; X-Plane license and GPU on the same PC as Factorio |
| **Power-grid control room** (Grid2Op / L2RPN) | Yes, weeks-long scenarios | Very high | **None.** Python env with no GUI host; we'd have to build the "host" ourselves | Strong (L2RPN scores) | Loses: no host extension system for the agent to write into, so no self-instrumentation story |
| **SRE on-call** (Microsoft AIOpsLab, IBM ITBench) | Episodes are minutes to hours per fault, not a continuous shift | High | Grafana/Prometheus/K8s configs | Strong, but crowded with SRE agents | Loses on novelty and on "inherently long" |
| **Wastewater plant, BSM1** (IWA/COST Benchmark Simulation Model 1) | Yes. 14-day dry/rain/storm influent files at 15-min samples, one week used for evaluation; diurnal load cycle | High: municipal utilities | Same Ignition stack | Strong: EQI (effluent quality) and OCI (operating cost) indices | **Best second scenario.** Same bridge and HMI, different plant. Python BSM1 (e.g. `aquakin`) not checked. |

TE wins because it combines four things the others lack together: a genuinely continuous job, a 30-year standard with published costs, delayed failure that forces long attention, and a real industrial host with a scripting system the agent can write into.

## Effort and risks (deadline Sunday 9:00 AM)

Pieces on Nazeem's side, roughly sized:

| Piece | Size | Notes |
|---|---|---|
| Plant bridge: paced tep-studio, Ricker layer, loop modes, A/B transmitters, faults, orders, OPC UA via asyncua | M | Core logic is ~300 lines on tep-studio; the faults above are already scripted |
| Ignition install, Maker license activation, OPC UA connection, tag import | S–M | Native Windows installer; needs internet for activation |
| Perspective HMI: overview, trends, alarms, faceplates | M | Done by hand in Designer (Java desktop app); keep it plain |
| Grader: truth log → `grader.jsonl` (cost, margins, trips, spec, detection latency) | S | Reads the plant process directly |
| Host config for the toolserver: project-folder write + scan endpoint, docs bundle, plant manual | S | Needs John's toolserver to accept a second host config |
| Oracle operator and Ricker-only baselines | S | Scripted, same bridge |
| Dashboard panels | S–M | Reuses the generic UI |

**Risks:**
- **Time.** This competes with Factorio for the same hours. A 24-hour shift needs the full stack running by Saturday midday at 2×, or the shift will be shorter. Cut list, in order: grade change, ablation, held-out seed count, custom HMI polish.
- **Maker licensing.** "Individuals for personal use… not for any monetary purpose" is unclear for a prize-bearing hackathon. Options: ask Inductive Automation for a hackathon/education license (the 8.1 docs point schools to sales), accept the gray area as a non-commercial student project, or fall back to FUXA (MIT).
- **8.3 file scan and loading.** The REST scan endpoint comes from forum posts. Test the write-then-scan loop first. If it fails, `system.project.requestScan()` from a gateway script, or the Designer, is the fallback.
- **PC load.** Factorio (GPU), Ignition (JVM, a few GB of RAM), the simulator (one CPU core) and Chrome on one machine. Only one agent runs at a time, but Factorio and the plant shift may need to run at different times.
- **The agent might just not touch anything.** Ricker's loops are good enough that a passive agent survives all the IDVs. The schedule must include the trip-causing instrument and valve faults; that's what makes the task hard.
- **Windows compile.** tep-studio needs the VS Build Tools; or run the plant process in its Docker image, since only OPC UA needs to cross.
- **Modified vs. original TE.** Our costs and trip times come from `temexd_mod`. Published numbers come from the original model. Say so on the slide.

## Decisions needed from John

1. **Is the chemical-plant shift the non-game showcase?** It would replace the AP clerk and ERP-Bench, which Nazeem rejected as not hard enough.
2. **PC capacity:** does the Windows PC have room for Ignition plus the simulator alongside Factorio? And are we OK running the shift while Factorio isn't, if needed?
3. **Ignition licensing:** Maker (gray area), ask Inductive Automation, or FUXA.
4. **Action rule for agent scripts:** may its gateway scripts write operator tags (setpoints, modes, MAN outputs, transmitter select), logged and flagged? Without this, the "automates its own control room" story doesn't work.
5. **Toolserver:** can `load_extension` take a per-host config of "write files to a folder, then call an HTTP endpoint" for Ignition?
6. **Clock and shift length:** 24 h at 1×, or 48 h at 2×.

## Sources

- tep-studio: https://github.com/khalidlabs/tep-studio (LICENSE, README, docs/concepts.md, control.md, comparison.md, agent.md, reference/schema.md, commit `852f0c6`). Our measurements were run on that commit.
- Modified TE model: Bathelt, Ricker and Jelali, "Revision of the Tennessee Eastman Process Model", IFAC-PapersOnLine 48(8), 2015: https://skoge.folk.ntnu.no/prost/proceedings/adchem2015/media/papers/0010.pdf
- Ricker TE archive (codes, Ricker 1995 optimal steady-state tables): https://depts.washington.edu/control/LARRY/TE/download.bkp.html
- Ricker (1995), Optimal steady-state operation of the TE challenge process, Comput. Chem. Eng. 19(9):949–959: https://www.sciencedirect.com/science/article/abs/pii/009813549400043N
- Ricker (1996), Decentralized control of the TE challenge process, J. Proc. Cont. 6(4):205–221: https://www.sciencedirect.com/science/article/abs/pii/0959152496000315
- Larsson, Skogestad and Hestetun, self-optimizing control of TE (114.323 / 111.620 / 169.852 $/h): https://skoge.folk.ntnu.no/prost/proceedings/cpc6-jan2002/larsson.pdf · https://skoge.folk.ntnu.no/publications/2001/te/submitted_version/te.pdf
- Base case ~170 $/h (secondary citation): https://www.ijcce.ac.ir/article_6215_b6111325686e4f6018b0b9b0ad23dbe7.pdf · https://ttu-ir.tdl.org/bitstreams/32c7263b-4c51-4879-ba1e-a7a87a99dcf1/download
- Ignition Maker Edition: https://inductiveautomation.com/ignition/maker-edition · https://docs.inductiveautomation.com/docs/8.1/other-editions/ignition-maker-edition.md · 8.3 Maker module thread: https://forum.inductiveautomation.com/t/not-eligible-for-use-with-ignition-maker-edition/113129
- Ignition trial: https://docs.inductiveautomation.com/docs/8.1/platform/licensing-and-activation.md · https://forum.inductiveautomation.com/t/pre-emptively-reset-trial-in-ignition-8-3/115313
- Ignition Docker: https://hub.docker.com/r/inductiveautomation/ignition · https://www.docs.inductiveautomation.com/docs/8.3/platform/advanced-deployments/docker-image.md · https://www.docs.inductiveautomation.com/docs/8.1/platform/docker-image.md · https://forum.inductiveautomation.com/t/ignition-edition-for-docker/113205
- Ignition scripting: https://docs.inductiveautomation.com/docs/8.3/platform/scripting/scripting-in-ignition/gateway-event-scripts · WebDev: https://docs.inductiveautomation.com/docs/8.3/ignition-modules/web-dev
- Ignition 8.3 project scan: https://forum.inductiveautomation.com/t/project-filesystem-scan-ignition-8-3/113647 · https://forum.inductiveautomation.com/t/request-scan-for-gateway-or-project-resources/115303 · https://docs.inductiveautomation.com/docs/8.1/appendix/scripting-functions/system-project/system-project-requestScan
- Perspective sessions: https://docs.inductiveautomation.com/docs/8.3/ignition-modules/perspective/perspective-sessions · URL pattern (forum): https://forum.inductiveautomation.com/t/perspective-app-navigation-different-than-browser/51671
- asyncua: https://github.com/FreeOpcUa/opcua-asyncio · FUXA: https://github.com/frangoteam/FUXA
- Neighbors: LAPSE 2026.0424 https://psecommunity.org/LAPSE:2026.0424 · arXiv 2606.28011 · 2505.02076 · 2607.27849 · 2606.20408 (https://arxiv.org/abs/<id>) · tep-copilot https://github.com/DAG94S/tep-copilot · Scada-Agent-SafetyBench https://github.com/heinrihs-s/Scada-Agent-SafetyBench
- Yokogawa FKDPP / JSR / ENEOS: https://www.yokogawa.com/eu/news/press-releases/2023/2023-03-30 · https://www.automationworld.com/control/article/22366503/artificial-intelligence-autonomously-controls-jsr-chemical-plant · https://jpt.spe.org/artificial-intelligence-autonomously-controls-plant-for-35-days
- BSM1: https://www2.iea.lth.se/publications/Reports/LTH-IEA-7231.pdf · https://aquakin.readthedocs.io/en/latest/generated/aquakin.evaluate_bsm1.html
- XPPython3: https://xppython3.readthedocs.io/en/beta/development/modules/dataaccess.html
