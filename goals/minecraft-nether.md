# Goal: a diamond pickaxe first, then the Nether, on peaceful difficulty; learn as much as you can, and build tools that make the next step cheaper

The program is **Minecraft: Java Edition 1.21.1** with NeoForge and the Minescript mod, in a **survival, peaceful-difficulty singleplayer world** (name `sisyphus-mc5`, cheats off) that is already open. You are the only player. You start with nothing in your inventory. Peaceful is the expected setting: do not change it, and do not touch the game mode.

Your workspace starts empty. Every skill, hook and script you use you write in this run: the point is to see how well you learn, cache and reuse procedures from scratch. Learning a procedure once and then calling it in one step is the main thing that is judged.

## Phase 1: Seed primitives (before anything else)

Quality over quantity: the aim is not many tools but a small, coherent, layered system whose basic moves are so robust they never cause trouble late in the run. Before adding a tool, ask whether an existing one should absorb it; consolidate overlapping tools; harden what you have until it is boring and reliable.

The brief's seed primitives, in Minecraft terms. Build these four tools first, with the brief's safeguard layer under them, each returning a receipt (what it did, how far, what changed, where and why it stopped). Keep the in-game part a small, stable set of primitives that catch their own errors; the routes, plans and retries live in your sandbox. Each tool must pass its tests below before Phase 2. Run the tests as real play (they yield wood, stone, iron and a base), so the game is never idle while you build.

### The two highest-value seed primitives here: pathfinding and not drowning

Most lost time and every death so far in these worlds came from movement: walking into water, getting stuck, and digging into flooded caves. Build these two first and make them excellent; almost every later chore (trees, ore, the base, obsidian, the portal) is a trip.

- **Pathfinding.** Plan a route over the block grid before moving, not by walking toward the target and reacting. Treat each block as walkable or not (solid floor, two blocks of headroom, not water, lava, fire or a drop over 3), allow step-ups, jumps and short drops, and fall back to placing a block (bridge or pillar) or breaking one (dig through) when no plain route exists. Plan long trips in segments (waypoints every 16 to 32 blocks), re-plan when the world differs from the plan, detect being stuck (no progress for a few seconds) and try another route before giving up, and return a receipt (where it got to, why it stopped). Remember named places (base, portal, ore spots) and route back to them. Prefer routes that stay out of water entirely.
- **Attention not to drown.** A safeguard that is always on, inside every movement, mining and building job, not only when you think about it. Detect it early from what you can read (head inside water, the air supply falling, standing in water deeper than 1, a block about to be broken with water behind it), and when it fires, stop the job, swim up and to the nearest dry, standing spot, and wait until the air supply is full before resuming. Path around water and treat water as costly; never break a block that has water or lava behind or above it; when mining near aquifers, check each block before breaking it. Test it by walking into deep water, digging toward water, and being underwater at the start of a job.

Record both, their safeguards and their tests in SEED.md first.

**Safety lives in the lowest layer.** The in-game movement and mining primitives themselves refuse to step onto or next to lava or fire, over a drop of more than 3, or into deep water, and refuse to break a block that has lava or water beside, behind or above it. Check the neighbours of every block you would dig or stand in, not only the block itself: digging beside a lava pool opens it onto you. Every higher tool inherits these refusals; a planner or shortcut can never bypass them. Keep a hazard map (lava, water, drops, death sites) and route around it; never walk back into a spot that just hurt you until you have made it safe.

**Hazard tests are the gate.** Before Phase 2, set up each hazard case for real at a safe distance (lava beside the path, lava behind a block you would dig, deep water, a drop over 3) and show the primitives refuse or recover. Keep them as tests and rerun them after every change to movement or mining.


- **Get anywhere:** go to coordinates, the nearest block of a type, or an entity. Handles jumps, steps, pillaring up, bridging gaps, leaving water, and digging through when blocked; checks every slice for lava, drops and drowning. Tests: a point 150+ blocks away over rough terrain; across a lake or river; up a cliff; down into a cave and back out; a target inside solid ground; back to a remembered base.
- **Get anything:** have N of an item, from inventory, a chest, or by gathering, crafting and smelting its whole recipe tree (mining what is needed, placing and using a crafting table and furnace). Tests: a stone pickaxe from an empty inventory; 16 torches; iron ingots when no iron is known yet; an item already in a chest.
- **Change many things at once:** place an arrangement from a written plan (a list of relative blocks), fetching what is missing, then check the result against the plan and report the differences. Tests: a 5x5 floor; a walled 3x3 room with a door; a staircase down 10 blocks. The portal frame later uses it.
- **See an area:** one call returns a compact view of the surroundings: terrain heights, notable blocks, ores and hazards within a radius, and a structure's diff against its plan. Tests: find the nearest trees, water, lava and exposed ore; check a structure you built.

Record every test (tool, test, pass or fail, receipt summary) in `/home/agent/work/SEED.md` and keep it current whenever a tool changes.

**Gate:** Phase 2 starts only when every test passes. After that, any chore that takes more than one call is a candidate to fold into the seed primitives.

## Phase 2: a diamond pickaxe (the first big milestone)

Starts when the Phase 1 gate is passed. Your first big milestone is a **diamond pickaxe**: objective steps 1 to 3 below (stone, iron and a bucket, diamonds, the pickaxe). Put everything into reaching it with the seed primitives before anything Nether-related.

## Phase 3: the Nether path

Starts once you hold a diamond pickaxe: objective steps 4 to 8. Use the seed primitives for every chore here.

## What peaceful changes

Hostile mobs (zombies, skeletons, creepers, spiders, and in the Nether ghasts, blazes and the like) do not spawn, and hunger does not run down. Health regenerates by itself. So you can work on the surface at night and do not need armor, a sword or a shelter. Still fatal: **lava, drowning, falling (more than 3 blocks hurts, more than about 20 kills), fire, suffocation in blocks, and the void**. Drowning in caves and long falls are the usual killers, so the water, lava and fall guards stay in every movement and mining tool. In a fortress or bastion, look before you step: peaceful does not make the terrain safe.

## Objective, in order

The measured path (judged from the world's own files; see Progress):

1. Wood, crafting table, stone tools, furnace, a storage chest, 10 distinct items crafted.
2. Iron: iron ingot, iron tools, **bucket**.
3. **Diamonds**, then a **diamond pickaxe** (3 diamonds and 2 sticks). Diamonds are rarest between about y=-59 and y=-50; mine **horizontally** along a level tunnel, with torches, and do not dig straight down.
4. **Obsidian**, at least **10** (a portal frame needs 10 blocks, 4 wide and 5 tall, corners not needed): mine it with the diamond pickaxe where water meets a lava source, or cast it from lava source blocks over water. Only with a bucket, water and the blocks you carry. Work out your own safe procedure, then keep it as a skill.
5. **Flint and steel** (an iron ingot and a flint; flint drops from gravel).
6. Build the portal, **light it**, **enter the Nether**.
7. In the Nether: **netherrack**, **nether quartz**, **glowstone dust**, find a **nether fortress** and a **bastion remnant**. Stretch: ancient debris (diamond pickaxe, y 8 to 22), 25 and 50 distinct items crafted.
8. **Return safely to the Overworld** through the portal, with what you collected.

Breadth also counts: distinct items crafted are measured. Bank it early near the base, where nothing can kill you, with one call that crafts a whole list from what you hold and reports the new items.

## Progress

Milestones, from the world files (advancements, statistics, the saved player, the Overworld's point-of-interest files), written by the game about every five minutes: first wood, crafting table, stone tools, furnace, chest, 10 crafted, iron ingot, iron tools, bucket, 25 crafted, diamond, diamond pickaxe, 1 obsidian, 10 obsidian, flint and steel, portal lit, Nether entered, netherrack, nether quartz, glowstone, fortress found, bastion found, 50 crafted, ancient debris, and returned to the Overworld. **Tokens**, steps and wall-clock time are what you spend; the run is judged on how many of these you reach, and on whether each one comes in fewer steps than the one before.

## Player actions only

Same rules as before. Hooks and scripts may read anything the client knows. What they do to the world must be what a player can do: walk and jump, look around, break blocks you can reach with the right tool at the normal mining time, place blocks and items from your inventory, use blocks, open containers and move items between slots, craft in the 2x2 or crafting-table grid, attack and eat, sleep. The game is the real client at normal speed (discrete actions at about 4 per second).

Not allowed, and flagged or voiding the run: chat or console commands that change the game (`/give`, `/tp`, `/gamemode`, `/difficulty`, `/time`, `/setblock`, `/summon`, and the like), turning on cheats or opening the world to LAN, editing the world, save or game files, spawning or duplicating items or entities, teleporting, instant mining or crafting, reaching beyond a player's reach, and changing the game mode or difficulty (including in the Options menu). Minescript's own commands (`\jobs`, `\killjob`, your scripts) are fine. A grader reads the world files and logs for cheats, the game mode, the difficulty and commands used; your extensions' code and calls are recorded and reviewed.

## Start here
1. Read `/home/agent/work/run/HOST.md` for how Minescript works and what the host allows. Call the game only through your registered tools or `run_script`, so calls are logged.
2. Create `/home/agent/work/BACKLOG.md` and `/home/agent/work/skills/INDEX.md` in your first steps, and keep both current. The runner shows you the top of BACKLOG.md and the index after every restart, reset or stall.
3. Start the first chore the moment its tool exists, and start the next before you think about the one after.

## Keep your hands busy: the backlog

- **BACKLOG.md** is an ordered list of chores, each a kept script or tool call with its `args`, what done looks like, and what to do if it fails. Sections: `## Running`, `## Queue`, `## Decisions` (branches decided in advance), `## Done` (with a line of what you learned). Under about 60 lines.
- **Keep the queue full.** Long chores run in the background with the next queued behind them; before you plan anything, the queue holds at least two chores. Never poll or wait idle: wait on tasks, and plan, write tools and update notes while a task runs.
- **Decisions ahead of time.** A kept dispatcher (an `autopilot`) reads a queue file you edit, runs the next item, stops and says why at the first hazard (low health, lava or water ahead, a drop over 3, a missing item).
- **After a reset:** call `status`, read BACKLOG.md, NOTES.md and the skills index, then start the first item not already running. Do not re-plan from scratch.

## Habits that pay off

- **Kit discipline.** A death drops everything you carry. Keep spare tools and valuables in the base chest and carry only what the next job uses.
- **Mine horizontally.** Vertical shafts end in falls and flooded caves. Check the block ahead and the ones around it for water or lava every slice.
- **Bank breadth early.** Craft your way to 25 distinct items right after the first stone and iron, where nothing can kill you.
- **A long job reports progress** every few seconds, has a count or deadline, writes a heartbeat, and returns a short reason when it stops. Keep reads answerable while a job runs.

## The Nether trip

- Before going: a stone pickaxe or better, 64 cobblestone (or other blocks), a bucket (water evaporates in the Nether, so water cannot save you from lava there; blocks can), food, torches, spare flint and steel, and a way to relight the portal. Leave valuables in the base chest.
- The portal is the way home: note its coordinates, and wall off lava or drops around the arrival platform with blocks before exploring. If the arrival is in a lava cave, bridge out with blocks and do not jump.
- Fortresses and bastions are usually a few hundred blocks away in the Nether (1 Nether block is 8 Overworld blocks). Follow netherrack caves or bridge along a level; place blocks ahead on every step you cannot see the floor of. Do not dig straight down or mine netherrack under you.
- Returning: walk into the portal and stand in it about 4 seconds. If the portal was broken, relight it with flint and steel from your kit. A bed explodes in the Nether; do not use one there.
