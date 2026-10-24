# Minecraft as a host (Java Edition, Apple Silicon Mac)

Researched Oct 23, 2026, for the BRIEF's open question "Minecraft version, mod loader, modpack, hook loading method, milestones". Unlike Factorio, Minecraft can run locally: Nazeem's Mac (arm64, macOS 26.6.2) already has the official launcher app installed, with no game or Java downloaded yet (checked on the machine).

Tags: **[verified]** = read on a primary page or API (Modrinth, CurseForge, official wikis, GitHub README). **[search]** = from a search summary only. **[unverified]** = from memory or inference; the spike should test it.

## Bottom line

- **Best host config: NeoForge 1.21.1 + a hand-picked tech pack (Create, Mekanism, Applied Energistics 2), with Minescript (Python scripting) as the hook host.** The tech mods with the richest GUIs have not left 1.21.1 yet: Create and Mekanism's newest NeoForge builds are for 1.21.1 only **[verified, Modrinth API]**.
- **The newest line is 26.3 (Fabric API, NeoForge, Minescript 5.0, AE2 and CC: Tweaked all ship for it)**, but Create and Mekanism do not. 26.3 is still a good place for a plumbing spike, because it has Muto and modded GUIs from AE2 and CC: Tweaked.
- **The fastest hook loop is not Java.** A Python script run through Minescript is re-read each time it runs, so a hook iteration costs no rebuild and no restart. Java mods stay as the deep-access fallback.
- **Reading modded GUI state is feasible**: Minescript exposes `screen_name`, `container_get_items` and a reflection API into any Java class. That is the "probe" tool John found so valuable, already built.
- **Grade from the world.** Advancements, statistics and inventory contents are readable from files or RCON, never from the agent's words.

## 1. Version, loader and reload

### Versions (Oct 2026)

| Fact | Status |
|---|---|
| Java Edition now uses year-based names. 26.1 and 26.2 (June 16) are out; **26.3 "Wilderness Bound" was due Sept 15**. Modrinth lists Fabric API, AE2, CC: Tweaked, Minescript and mcpfabric builds for 26.3, and Fabric API 26.4 snapshots, so 26.3 is the latest release | verified indirectly (Modrinth); no Mojang page read |
| 26.x needs **Java 25** (Fabric docs, NeoForge 26.1 post). NeoForge 26.1 needs Gradle 9.1+ and ModDevGradle 2.0.x. Obfuscation was removed in 26.1, so mods use Mojang names | verified |
| 1.21.1 needs Java 21 | unverified (well known) |
| NeoForge 26.1 posted as the likely next "stable modding version", replacing 1.21.1, but that is one maintainer's personal guess; the team is short-staffed | verified (NeoForge news) |

### Loader choice

| Loader | State | Fit |
|---|---|---|
| **NeoForge** | Active on 1.21.1 and 26.x. Where Create, Mekanism, KubeJS, ATM-style packs live | Best for the modded content. No built-in hot reload |
| **Fabric** | Fastest to update (Fabric API 26.3 and 26.4 snapshots exist). Fabric 26.x uses Mojang names, no Yarn. Create has no Fabric build newer than 1.20.1 on Modrinth | Best dev loop (hotswap docs, Muto), thin tech content |
| **Forge** | Weakest bet. One search result says it shipped 26.3 builds | Skip |

### Reloading without a restart

| Mechanism | What reloads | Status |
|---|---|---|
| `/reload`, F3+T | Data packs (recipes, loot, tags, advancements), then assets. Seconds | verified (Fabric wiki) |
| **KubeJS** scripts | Server and data scripts on `/reload`; client scripts with F3+T or `/kubejs reload client_scripts`; startup scripts need a restart. JavaScript on a Rhino fork, wraps Minecraft classes. Builds for 1.21.1 (2101.7.2, Sept 2026) and 26.1.2 NeoForge (8.0.7, Oct 15) | verified (kubejs.com, Modrinth) |
| **Minescript** | Python scripts run from chat; the file is read at run time, so edits apply on the next run. It has a Java reflection API. Version 5.0 (Sept 22, 2026) for 26.1 to 26.3 on Fabric and NeoForge; **4.0 for 1.21.1** (Sept 2024). GPL-3.0-only. 77K downloads | verified (Modrinth, README, docs). That scripts re-read per run is inferred |
| **Fabric hotswap** | In a debug run, **method bodies only**. With the JetBrains Runtime plus `-XX:+AllowEnhancedClassRedefinition`, added and removed methods and classes too. JBR 25 builds exist, and Vaadin's guide says to use the `osx-aarch64` build | verified (Fabric wiki; JBR README via search). Not tested on this Mac or inside a launcher instance |
| **Muto** (fy2ne/muto) | Adds, removes and updates Fabric mod jars from the **title screen** without restarting the JVM; isolated classloaders; rollback on init failure. **Real, but beta**: 0.1.0-beta.2 (Oct 10, 2026), 9 downloads, MC 26.3, Fabric Loader 0.16+, Java 25, Apache-2.0. In-world reloads disabled; core mods cannot be swapped | verified (CurseForge page); the author's own claims, nobody has reviewed it |
| NeoForge hot reload | None found | unverified |
| CC: Tweaked | Lua programs inside in-game computers; instant. Only reaches what a peripheral exposes | verified it exists (Modrinth); capability limits unverified |

### What a fast hook loop looks like

Three layers, from fastest to slowest:

1. **Script hooks (seconds).** The agent writes a Python file into the Minescript folder (or a JS file into KubeJS). It reads screens, containers, block entities or any Java field by reflection and prints JSON. A helper process outside the game turns it into a named MCP tool.
2. **Data hooks (seconds).** Advancements, tags and recipes through `/reload`.
3. **Java mod hooks (minutes).** A Gradle-built mod, used only when scripting can't reach something. Pair it with JBR hotswap in a dev run, or with Muto on Fabric 26.3, to cut the restart.

The best-fit existing "bridge mods" are below. All except Minescript are human-built bridges with a fixed tool list, so they are baselines or comparisons, not what our agent builds.

| Mod | What it gives | Notes |
|---|---|---|
| **Minescript** | Scripting host: Python (and Pyjinn from 5.0), reflection, screen and container reads, key and mouse events | Best match for "agent writes hooks live" |
| **mcpfabric** (MIT) | Local HTTP/JSON-RPC bridge plus SSE events, 50+ MCP tools: inventory, entities, screenshot, control, navigation. Builds for 1.21.1 through 26.3 on Fabric **and NeoForge**, updated Oct 16 | No open-GUI or container reading mentioned. 4.4K downloads |
| **InventivetalentDev/minecraft-mcp** (MIT, 14 stars) | Fabric client mod with a local WebSocket server (port 25576), screenshot, **client-side reflection and variable tools**, plus a Paper plugin with reflection. MC 26.1.2 | Proves a reflection "probe" over a socket works |
| MMCP, "mcp server mod", ClaudeCraft MCP, Easy LLM | Small Fabric MCP or WebSocket bridges (1.20.1 to 1.21.4) | [search] |
| Numen AI | Mod acting as an MCP server for Claude Code and Codex, on Fabric, Forge and NeoForge | [search] |

## 2. Reading state out and sending input in

| Channel | What it reaches | Notes |
|---|---|---|
| **RCON** (dedicated server, `enable-rcon` in `server.properties`) | Commands: `data get` on players and block entities, `execute if items`, advancement checks, `save-all` | Server-side only; no client GUI. Not available on an integrated (singleplayer) server. Best for the grader. Protocol details from memory (unverified) |
| **World files** | `advancements/<uuid>.json`, `stats/<uuid>.json`, `playerdata`, region files, parsed offline | The grader needs no game cooperation. Write timing: run `save-all` first (unverified) |
| **Bridge mod over HTTP or WebSocket** | Whatever the mod exposes; mcpfabric and minecraft-mcp expose state, chat and screenshots | Client-side mods see the open `Screen` |
| **Open modded GUI** | In the client, `Minecraft.screen` is an `AbstractContainerScreen` whose menu holds the slots. Minescript's `container_get_items` reads them. Machine-specific values (progress bars, energy, fluid tanks, AE2 terminal rows) are usually not in slots; reach them by reflection on the screen or menu, or on the block entity | Reflection into a Mekanism or AE2 screen is the first thing to test. Slot reading is verified in docs; machine widgets are unverified |
| **Server-side block entities** | In singleplayer the client and the integrated server share one JVM, so a hook can read any block entity directly. On a dedicated server, a server-side hook is needed (KubeJS server scripts or a mod) | Inference |
| **Screenshots on macOS** | `screencapture -l <windowID>` captures one window (window ID from Quartz `CGWindowListCopyWindowInfo`). The old `CGWindowListCreateImage` is deprecated since macOS 14; ScreenCaptureKit (`SCScreenshotManager`, macOS 14+) replaces it. Needs **Screen Recording permission** for the capturing app. Retina windows are 2x pixels | verified for API status (Apple forums); that `screencapture -l` works for Minecraft's OpenGL (Metal-backed) window while covered is unverified |
| **Input on macOS** | Quartz `CGEventPost`, pyautogui or the computer-use tool, all needing **Accessibility permission**. Events go to the focused app, so Minecraft must be frontmost. Minecraft grabs the mouse (GLFW), pauses on lost focus (set `pauseOnLostFocus:false` in `options.txt`), and mouse-look needs relative moves. The "mcp server mod" docs also say to disable "Pause on Lost Focus" | [search] plus inference. The spike should check how well mouse-look and GUI clicks work |
| Minescript input | Key-bind presses, chat, listeners for key and mouse events | Verified in docs; used as a faster, non-pixel path. Policy question: the BRIEF limits actions to player-possible ones, and a bridge-driven keypress is still one |

Observations to log in `events.jsonl`: reading a screen through Minescript is `channel: hook`; the Mac screenshot is `channel: screenshot`.

## 3. Modpack choice

**State of the content (Modrinth API, Oct 23):**

| Mod | Newest loaders and versions | License |
|---|---|---|
| Create | NeoForge 6.0.10 (Apr 21, 2026), **1.21.1 only**; a Fabric build exists only for 1.20.1 | verified versions; license not read |
| Mekanism | NeoForge 10.7.19 (Apr 10, 2026), **1.21.1 only** | not read |
| Applied Energistics 2 | Fabric, Forge and NeoForge; **1.21.1 through 26.3** | "Multiple" |
| CC: Tweaked | 1.21.1 (1.120.2, Aug 2026) through 26.3 | not read |
| KubeJS | 1.21.1 (Sept 2026) and 26.1.2 | not read |
| Thermal Foundation | Forge and NeoForge, **1.20.1 only** | not read |
| EMI, Jade | EMI to 1.21.1; Jade to 26.3 | MIT; CC-BY-NC-SA-4.0 |

The CurseForge side wasn't checked; Create and Mekanism may differ there (unverified).

**Packs seen:** All the Mods 10 (1.21.1 NeoForge, about 400 mods including Create and Mekanism, 8 GB or more of RAM) [search]; Create+ and Create Ultimate (LGPL-3.0, 1.21.1 NeoForge, on Modrinth); The Mekanist and Industrial Alchemist (1.21.1 NeoForge, CurseForge) [search]. The Modrinth modpack search shows nothing like ATM10 on 26.x.

**Recommendation: hand-pick a small pack of our own** instead of a large pack, so we know every GUI and can load it fast on a laptop:
- NeoForge 1.21.1, Java 21.
- Create, Mekanism, Applied Energistics 2, plus helpers: EMI, Jade, KubeJS, and the bridge (Minescript 4.0, optionally mcpfabric 0.8.0 as a baseline).
- Keep it to a handful of mods (RAM, load time, fewer surprises).
- Why not ATM10: huge (load time, RAM), and its terms (not read) may restrict redistribution.

**Milestones** (judged from state; registry names are from memory, **verify with a registry dump** in the spike):

| # | Milestone | Evidence (read by the grader) |
|---|---|---|
| 1 | Vanilla iron tools | advancement `minecraft:story/smelt_iron` or inventory has `minecraft:iron_pickaxe` |
| 2 | Create: andesite alloy | stat `picked_up` or crafted `create:andesite_alloy`; Create advancements |
| 3 | Create: brass ingot | item `create:brass_ingot` in inventory or a container |
| 4 | Create: precision mechanism | item `create:precision_mechanism` |
| 5 | Mekanism: osmium to steel | `mekanism:ingot_osmium`, then `mekanism:ingot_steel` |
| 6 | Mekanism: Digital Miner built | item `mekanism:digital_miner`, or the block placed (`data get block`) |
| 7 | AE2: ME network with autocrafting | block `ae2:controller` placed, crafting job output (`ae2:logic_processor`) |

Each can be read by RCON (`execute if items`, `data get`), the world's `advancements` and `stats` JSON, or an offline parse of playerdata. **Licensing and redistribution:** do not commit the game or mod jars. Ship a packwiz or Modrinth manifest that downloads from Modrinth, and check each mod's license before publishing. Minescript is GPL-3.0-only (fine to use; redistributing the jar means offering source). The Minecraft EULA forbids redistributing the game. The BRIEF's "confirm licenses for Minecraft on the demo machine" still applies; the launcher is installed, so the account question is Nazeem's.

## 4. Running it on the Mac

| Question | Answer |
|---|---|
| Launcher | Official launcher is installed. **Prism Launcher** has native Apple Silicon builds (Homebrew cask `prismlauncher`) and is easier to script (instances, mod folders, per-instance JVM args). Prism needs a real Microsoft login once; offline play after that [search] |
| Java | The launcher downloads its own runtime. For dev, install Temurin 25 (aarch64) for 26.x (the Fabric docs recommend it), and Java 21 for 1.21.1. No JDK is installed now. Gradle comes from each template's wrapper, not a system install |
| Building a mod | NeoForge: ModDevGradle (2.0.x on 26.1). Fabric: Fabric Loom. Both ship a `runClient` task. Rebuild time is the pilot's "minutes" for Java mods (a measurement for the spike) |
| Headless vs client | A headless dedicated server works for RCON and grading, but **screenshots need the client**. Run both: dedicated server on localhost, plus one client that joins it |
| Where hooks attach | Client-side hooks (open screens, rendering, input, screenshots) attach to the client. Server-side hooks (block entities, RCON, advancements) attach to the server. In singleplayer both live in one JVM, which makes reflection easiest, but there is no RCON, so the grader reads world files |
| Resources | Apple Silicon runs Minecraft natively; a small tech pack should fit in 6 to 8 GB of heap (unverified). Keep the client's window at a fixed size and `pauseOnLostFocus:false` |
| Permissions on macOS 26 | Screen Recording and Accessibility for whichever app takes screenshots and sends input |

## 5. Prior art

| Item | What it is | Contrast |
|---|---|---|
| **Voyager** (2023) | Skill library of JavaScript on Mineflayer | Skills over a human-built API, vanilla |
| **Gemini 4 Argon team** (arXiv 2610.22319) | Four agents, Mineflayer, 27h, vanilla Ender Dragon (see `prior-art.md`) | Human-built interface, vanilla |
| **MineDojo** (2022) | Simulator and internet-scale knowledge base for Minecraft agents | [unverified here, from memory]. Vanilla benchmark |
| **Mindcraft** (mindcraft-bots) | LLMs driving Mineflayer bots; targets 1.21.6 to 1.21.11; evaluation tasks and MineCollab; can run LLM-written code (off by default) | Vanilla, Mineflayer |
| **Human-built MCP bridge mods** | mcpfabric, minecraft-mcp, MMCP, Numen: an LLM plays through a fixed tool list. minecraft-mcp even has reflection tools | These are the human-built ceiling for "mod bridge" access; not an agent building its own |
| **Universal Modder, minecraft-agent-skills (Jahrome907), forge-1.12.2-agent-kit** | Agents help humans write mods | The agent writes mods for a person, not hooks for its own run |
| Orak, PillagerBench, MineExplorer | Minecraft agent benchmarks | Vanilla |

Searches found **no agent that writes its own hooks into modded Minecraft to play it, other than John's pilot**, and no 2026 paper on modded Minecraft agents. This was two extended searches, so treat it as "not found", not "doesn't exist".

## 6. Recommendation and first spike

### Host config

| Item | Choice |
|---|---|
| Version and loader | **Minecraft 1.21.1, NeoForge, Java 21** (content). Optional second config for a plumbing comparison: 26.3 with Fabric, Muto, AE2, CC: Tweaked |
| Pack | Hand-picked: Create, Mekanism, AE2, EMI, Jade, KubeJS, Minescript |
| Setup | Prism instance plus a localhost dedicated server (same mod set) with RCON on |
| Extension system the agent writes into | **Minescript Python scripts and KubeJS scripts** (live). Java mod via Gradle only if scripting can't reach something |
| Reload | Script: next run. Data: `/reload`. Java: rebuild plus restart (or JBR hotswap) |
| Grader read path | RCON plus `advancements/` and `stats/` JSON plus playerdata, on the server. Agent can't see any of it |
| Milestones | The table in section 3 |
| Policy | Actions limited to what a player can do; log any `op`, RCON, `/give` or `/gamemode` use as a violation |

### Open question to answer first

Does Minescript 4.0 on 1.21.1 do what 5.0 does on 26.x? 5.0 requires Pyjinn, a Java-hosted Python. If 4.0's reflection or screen API falls short, move the content to a newer pack or to a Java mod.

### First spike (about an hour on Nazeem's Mac)

1. Install Temurin 21 (and 25 if trying 26.3), Prism, a NeoForge 1.21.1 instance with the mod set above. Dump the registry names for the milestone items.
2. Launch the client, open a world, run `screen_name()` inside a Mekanism machine or AE2 terminal screen, and a hand-written script that prints `container_get_items()` as JSON. Pass if modded slots come out.
3. Read a **machine value that isn't a slot** (progress, energy, fluid) by reflection on the screen or menu, as a second hand-made hook. Pass if the number matches what the screenshot shows.
4. **Reload test:** edit the script (add a field), re-run it, and time edit-to-new-output. Do the same for a KubeJS `/reload` and for a Java mod rebuild (`./gradlew build` plus a restart) as the baseline. Record all timings in a table.
5. **Capture and input test:** `screencapture -l <windowID>` of the Minecraft window while another window is on top; a click in a GUI through Quartz events. Note which permissions and focus settings it needs.
6. **Grader test:** against a localhost server, get an advancement and an item count by RCON, and the same from `advancements/*.json`. Pass if they agree.
7. If anything fails in step 2 or 3, repeat on 26.3 Fabric with Minescript 5.0 and AE2.

Output for John: a table of the seven pass/fail results and the timings, plus the scripts, ready to become the first hooks.

## Unverified, to check

- That Mojang shipped 26.3 on Sept 15 (only third-party and Modrinth evidence).
- Whether Create or Mekanism have 26.x ports on CurseForge or in GitHub branches (GitHub API calls timed out).
- Minescript 4.0's behavior on 1.21.1, and whether scripts re-read per run.
- Reflection into machine GUI values, `screencapture -l` of a covered OpenGL window, and background mouse input.
- JBR hotswap inside a launcher-started instance, and Gradle rebuild times.
- Licenses of Create, Mekanism, AE2 and the large packs; ATM10's redistribution terms.
- The milestone registry names, and RCON and world-file details (from memory).

## Sources
- Muto: https://www.curseforge.com/minecraft/mc-mods/muto
- Fabric hotswapping: https://wiki.fabricmc.net/tutorial:hotswapping
- NeoForge for 26.1: https://neoforged.net/news/26.1release/
- Minescript: https://github.com/maxuser0/minescript · https://modrinth.com/mod/minescript · https://minescript.net/downloads · docs https://raw.githubusercontent.com/maxuser0/minescript/main/docs/README.md
- KubeJS: https://kubejs.com/
- mcpfabric: https://modrinth.com/mod/mcpfabric · minecraft-mcp: https://github.com/InventivetalentDev/minecraft-mcp · others via https://modrinth.com/mod/mmcp, https://modrinth.com/mod/mcp-server-mod
- Modrinth API (project and version queries for create, mekanism, ae2, cc-tweaked, kubejs, fabric-api, minescript, mcpfabric): https://api.modrinth.com/v2/
- JetBrains Runtime and enhanced redefinition (Vaadin guide, JBR README): https://vaadin.com/docs/latest/configuration/live-reload/hotswap-agent
- Screen capture APIs: https://developer.apple.com/forums/thread/740493 · https://developer.apple.com/forums/thread/849969
- Mindcraft: https://github.com/mindcraft-bots/mindcraft
- Fabric Java install (Java 25, Temurin): https://docs.fabricmc.net/players/installing-java/macos
- Prism Launcher: https://formulae.brew.sh/cask/prismlauncher
- Related: Universal Modder https://github.com/rehan-remade/universal-modder · Numen AI https://www.curseforge.com/minecraft/mc-mods/numen-ai · minecraft-agent-skills https://github.com/Jahrome907/minecraft-agent-skills
- Our own notes: `moddedbench-lessons.md`, `prior-art.md`, `other-hosts-and-sponsors.md`
