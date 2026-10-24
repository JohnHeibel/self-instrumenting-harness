# Prior art: re-checked, with additions

Re-checks the BRIEF's prior-art list (Oct 23–24, 2026) and adds what it missed. Status tags:
- **confirmed**: found on a primary or reliable source
- **partly**: exists, but the details differ from the BRIEF
- **not found**: couldn't locate

## The BRIEF's list

| Item | Status | What it actually is | How we differ |
|---|---|---|---|
| **Universal Modder** (rehan-remade/universal-modder) | partly | MIT toolkit of skills, a `um` CLI and a fal MCP server, letting coding agents (Claude Code, Codex, Cursor, ...) mod PC games. It covers Fabric, BepInEx, Harmony, UE4SS, SKSE, tModLoader, ScriptHookV and others. "In-game agent bridges" is listed under its `game-automation` skill, but the README doesn't document how a bridge works. | It's a modding toolkit for humans' requests, not a long-running agent extending its own interface. Read the `game-automation` skill before the pitch to be sure. |
| **CLI-Anything** (HKUDS, Mar 2026; paper arXiv 2606.03854) | confirmed | Reads an app's **source** and generates a Python Click CLI (with `--json` and a REPL) through a 7-phase pipeline. Ships as a Claude Code plugin. GIMP, Blender, LibreOffice, Audacity, ... | Needs source, and builds ahead of time, outside the running app. Ours works from raw access, inside the host, during the run. |
| **Evolving Agent Interfaces / Agent S4** (Simular, arXiv 2610.28601, Oct 20, 2026) | confirmed | A meta-agent reads task trajectories (no success labels) and writes interfaces and usage notes into a repo. Population-based evolution keeps the repos that do best on held-out validation tasks. OSWorld V2: Qwen3.8-27B gets 21.3%, 3.8x its fixed-interface score and above Opus 4.8's 20.6%, at a fraction of the cost. Code at simular-ai/agent-s. | Offline evolution before deployment, over OS-level interfaces (not in-host code), no games. **Strongest academic neighbor; cite it precisely.** Their held-out validation is a methodology to borrow. |
| **A Decade in a Day / ANIS** (arXiv 2610.15765) | **not found** | Search found neither the ID nor the title. Nearby: arXiv 2603.20300 (agent interfaces as a software layer) and an ICML 2026 position paper on agent-native environments. | Don't put it on a slide until someone finds the paper. |
| **Prime Agent** (Prime Intellect, Aug 2026) | confirmed | Self-improving harness: Recursive Language Model + "Continual Harness" built on the pi agent. `/refine` turns failures into memories and successes into skills. Played Factorio via FLE and reward-hacked by spawning items over RCON, keeping it as a skill. Critics note RLM recursion depth is capped at 1. | It edits its own prompts and skills on top of a *prebuilt* game interface. Ours builds the interface. Its reward hack is our argument for read-anything / act-as-player. |
| **Gemini Minecraft team** (arXiv 2610.22319, Oct 15, 2026; Faldor, Singh, Raileanu, DeepMind) | confirmed (the BRIEF's "verify" is resolved) | Four Gemini 4 Argon agents, each on a Mineflayer bot, beat the Ender Dragon from a random spawn. 27h14m, 605 deaths, 2,783 programs, coordination through chat and shared memory. Easy difficulty, a single attempt, about $520–$1,620 depending on the source. | Vanilla Minecraft through Mineflayer, a human-built interface. It's the best "tens of hours" reference point for judges. |
| **Voyager** (2023) | confirmed (known) | Minecraft agent with an automatic curriculum and an ever-growing **skill library** of JS programs, on Mineflayer. | A skill library over a human-built API, vs. our hook library written into the host. |
| **Continual Harness** (arXiv 2605.09998, May 2026; Karten et al., Princeton/DeepMind) | confirmed | Grew out of Gemini Plays Pokémon. Starting from a minimal interface, the agent alternates acting with rewriting its prompt, sub-agents, skills and memory, **reset-free within one run**. It recovers most of the gap to a hand-built expert harness, with gains depending on the model. There's a pi extension, `pi-continual-harness`. | It improves the agent's *own harness*, not code inside the game. Closest conceptually on "online, single long run". |
| **Claude Plays Pokémon** | confirmed | Minimal harness: knowledge-base notes, a vision module, emulator button presses, and a navigator tool. The team removes assists as models improve. | Human-built harness and assists. |
| **Startups** (Minicor, Kampala, Zatanna, Frigade) | not re-checked | n/a | n/a |
| **AutoRPA, PACE, SkillDroid, SkillCache, SpeedRunner** | not found by name | Nearest found: **CUA-Skill** (arXiv 2601.21123), a parameterized skill library for desktop agents; and the procedural-memory-as-code discussion (arXiv 2512.20278). | Re-search by exact title before citing. |

## Additions worth knowing

- **Alita** (Princeton, 2025) and **Alita-G**:
  - Alita starts with only a web agent and a code interpreter, and **writes its own MCP tools** when it hits a capability gap.
  - Alita-G harvests the good tools into domain "MCP Boxes".
  - This is the reason the BRIEF says not to lead with "writes its own MCP server". It's prior work.
- **LATM** (LLMs as Tool Makers): an expensive model makes tools once, a cheap model uses them. This maps onto our "tokens per task falls as hooks accumulate" curve; a cheaper model using our hooks is a possible extra result.
- **ToolMaker**: turns public repos into agent tools.
- **Darwin Gödel Machine** (Sakana/Clune, ICLR 2026): an archive of self-modified coding agents with open-ended selection. SWE-bench went from 20% to 50%. It's an agent rewriting itself, validated empirically, and the canonical "self-improving" citation. It's also why "self-improving harness" reads as old news.
- **FLE** (see `factorio.md`): the human-built Factorio bridge, and our ceiling baseline.
- **Surveys** to skim if time allows:
  - Self-evolving agents: arXiv 2507.21046, 2508.07407
  - Self-improvements in agentic systems: arXiv 2607.13104
  - Tool-making in low-latency systems: arXiv 2607.08010

## Where the novelty claim stands after this pass

Still holds as far as we found. Nobody combines all of:
1. starting from screenshots, input and docs only
2. writing code *into the host* through its own extension system
3. doing it *during* a long run and repairing it when the host changes
4. across games and business software

The closest on each axis:
- Agent S4: evolves interfaces, but offline and OS-level.
- Continual Harness: online and reset-free, but edits its own harness rather than the host.
- Universal Modder: in-host mods, but short and human-directed.
- Alita: self-made tools, but MCP wrappers, not host code.

Suggested pitch slide: a 4-column checklist with those four, Prime Agent, and us.

## Sources
- Universal Modder: https://github.com/rehan-remade/universal-modder
- CLI-Anything: https://github.com/HKUDS/CLI-Anything · https://arxiv.org/pdf/2606.03854
- Agent S4: https://www.simular.ai/articles/agent-s4-evolving-the-interfaces-computer-use-agents-work-throughagen · https://deeplearn.org/arxiv/853259/evolving-agent-interfaces
- Prime Agent: https://www.testingcatalog.com/icymi-prime-intellect-releases-open-source-prime-agent/
- Gemini Minecraft: https://www.opentrain.ai/papers/a-team-of-gemini-4-argon-agents-beats-minecraft--arxiv-2610.22319/ · https://aiweekly.co/alerts/google-deepminds-four-gemini-4-argon-agents-beat-minecraft-from-a-random-spawn
- Continual Harness: https://www.roboticscenter.ai/research/papers/continual-harness-online-adaptation-for-self-improving-foundation-agents-2605 · https://pi.dev/packages/pi-continual-harness
- Claude Plays Pokémon: https://getcoai.com/video/the-making-of-claude-plays-pokemon/
- Darwin Gödel Machine: https://arxiv.org/abs/2505.22954 · https://sakana.ai/dgm/
- Alita-G: https://arxiv.org/html/2510.23601v1 · survey https://arxiv.org/pdf/2507.21046
- CUA-Skill: https://arxiv.org/html/2601.21123
