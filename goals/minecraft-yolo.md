# Goal: in 2 hours, build a base a human would find beautiful, get diamond tools, and enter the Nether

The program is **Minecraft: Java Edition 1.21.1** with NeoForge and the Minescript mod, in a **survival, peaceful-difficulty singleplayer world** (name `sisyphus-mc7`, cheats off) that is already open. You are the only player. You start with nothing. Peaceful is the expected setting: do not change it or the game mode.

Your workspace starts empty. Every skill, hook and script you use you write in this run.

## The three targets (all judged at the end of the 2 hours)

1. **A base that is visually pleasing to a human.** An actual building, not a hole or a dirt box. Judged from screenshots and the world afterwards against this checklist:
   - Fully enclosed: walls and a roof with no gaps, a proper door, at least 2 windows (glass or panes).
   - Designed: a recognisable shape (e.g. a pitched or stepped roof, a porch, pillars or trim), symmetric or deliberately composed, at least 3 different building blocks used with intent (e.g. a stone base, wood walls, a contrasting roof), not one material everywhere.
   - Lived in: lit inside (torches or lanterns), with a crafting table, a furnace, chests and a bed laid out tidily.
   - Fits its spot: on flat or levelled ground, with a path or entrance area.
   Design it as a written plan first (a block list you can view and check), then build it with your build-from-plan tool and verify the result matches the plan. Take a screenshot of the finished base from outside, in daylight, from a good angle, and save it in your workspace.
2. **Diamond tools.** At least a diamond pickaxe; the full set (pickaxe, axe, shovel, sword) is the stretch. Keep diamonds and diamond tools in a chest in the base whenever you do risky work.
3. **Enter the Nether.** 10 obsidian, flint and steel, a portal (4 wide, 5 tall, corners optional), lit, stepped through. Bonus: come back.

## The clock is the constraint

You have **2 hours of wall-clock time**; every prompt tells you how much is left. Nothing counts after the deadline. Balance building tools, playing, and building the base against that clock: tools only pay if they save time within these 2 hours. A sensible order (your call): seed primitives with safety (about 20 minutes), a simple shelter that becomes the base's core, diamond tools, obsidian and the portal, then finish the base's looks with the time left, or run building jobs in the background while you plan. Do not leave all three half-done: one complete target beats three unfinished ones.

## Seed primitives (first, kept short)

Quality over quantity: the aim is not many tools but a small, coherent, layered system whose basic moves are so robust they never cause trouble late in the run. Before adding a tool, ask whether an existing one should absorb it; consolidate overlapping tools; harden what you have until it is boring and reliable.

The brief's seed primitives, in Minecraft terms. Build these four tools first, with the brief's safeguard layer under them, each returning a receipt (what it did, how far, what changed, where and why it stopped). Keep the in-game part a small, stable set of primitives that catch their own errors; the routes, plans and retries live in your sandbox. Each tool should pass its tests below before you lean on it. Run the tests as real play (they yield wood, stone, iron and a base), so the game is never idle while you build.

### The two highest-value seed primitives here: pathfinding and not drowning

Most lost time and every death so far in these worlds came from movement: walking into water, getting stuck, and digging into flooded caves. Build these two first and make them excellent; almost every later chore (trees, ore, the base, obsidian, the portal) is a trip.

- **Pathfinding.** Plan a route over the block grid before moving, not by walking toward the target and reacting. Treat each block as walkable or not (solid floor, two blocks of headroom, not water, lava, fire or a drop over 3), allow step-ups, jumps and short drops, and fall back to placing a block (bridge or pillar) or breaking one (dig through) when no plain route exists. Plan long trips in segments (waypoints every 16 to 32 blocks), re-plan when the world differs from the plan, detect being stuck (no progress for a few seconds) and try another route before giving up, and return a receipt (where it got to, why it stopped). Remember named places (base, portal, ore spots) and route back to them. Prefer routes that stay out of water entirely.
- **Attention not to drown.** A safeguard that is always on, inside every movement, mining and building job, not only when you think about it. Detect it early from what you can read (head inside water, the air supply falling, standing in water deeper than 1, a block about to be broken with water behind it), and when it fires, stop the job, swim up and to the nearest dry, standing spot, and wait until the air supply is full before resuming. Path around water and treat water as costly; never break a block that has water or lava behind or above it; when mining near aquifers, check each block before breaking it. Test it by walking into deep water, digging toward water, and being underwater at the start of a job.

Record both, their safeguards and their tests in SEED.md first.

**Safety lives in the lowest layer.** The in-game movement and mining primitives themselves refuse to step onto or next to lava or fire, over a drop of more than 3, or into deep water, and refuse to break a block that has lava or water beside, behind or above it. Check the neighbours of every block you would dig or stand in, not only the block itself: digging beside a lava pool opens it onto you. Every higher tool inherits these refusals; a planner or shortcut can never bypass them. Keep a hazard map (lava, water, drops, death sites) and route around it; never walk back into a spot that just hurt you until you have made it safe.

**Hazard tests come first.** Early on, set up each hazard case for real at a safe distance (lava beside the path, lava behind a block you would dig, deep water, a drop over 3) and show the primitives refuse or recover. Keep them as tests and rerun them after every change to movement or mining.


- **Get anywhere:** go to coordinates, the nearest block of a type, or an entity. Handles jumps, steps, pillaring up, bridging gaps, leaving water, and digging through when blocked; checks every slice for lava, drops and drowning. Tests: a point 150+ blocks away over rough terrain; across a lake or river; up a cliff; down into a cave and back out; a target inside solid ground; back to a remembered base.
- **Get anything:** have N of an item, from inventory, a chest, or by gathering, crafting and smelting its whole recipe tree (mining what is needed, placing and using a crafting table and furnace). Tests: a stone pickaxe from an empty inventory; 16 torches; iron ingots when no iron is known yet; an item already in a chest.
- **Change many things at once:** place an arrangement from a written plan (a list of relative blocks), fetching what is missing, then check the result against the plan and report the differences. Tests: a 5x5 floor; a walled 3x3 room with a door; a staircase down 10 blocks. The portal frame later uses it.
- **See an area:** one call returns a compact view of the surroundings: terrain heights, notable blocks, ores and hazards within a radius, and a structure's diff against its plan. Tests: find the nearest trees, water, lava and exposed ore; check a structure you built.

Record every test (tool, test, pass or fail, receipt summary) in `/home/agent/work/SEED.md` and keep it current whenever a tool changes.

**Budget:** aim to have the seed primitives, including build-from-plan and the safety layer with its hazard tests, working within about the first 20 to 25 minutes, then use them for everything. Fold any chore that takes more than one call into them as you go.

## What peaceful changes

Hostile mobs (zombies, skeletons, creepers, spiders, and in the Nether ghasts, blazes and the like) do not spawn, and hunger does not run down. Health regenerates by itself. So you can work on the surface at night and do not need armor, a sword or a shelter. Still fatal: **lava, drowning, falling (more than 3 blocks hurts, more than about 20 kills), fire, suffocation in blocks, and the void**. Drowning in caves and long falls are the usual killers, so the water, lava and fall guards stay in every movement and mining tool. In a fortress or bastion, look before you step: peaceful does not make the terrain safe.

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
- **Materials for the base.** Gather building variety along the way (logs of a couple of kinds, stone, sand for glass) so the base doesn't stall.
- **A long job reports progress** every few seconds, has a count or deadline, writes a heartbeat, and returns a short reason when it stops. Keep reads answerable while a job runs.

## The Nether trip

- Before going: a stone pickaxe or better, 64 cobblestone (or other blocks), a bucket (water evaporates in the Nether, so water cannot save you from lava there; blocks can), food, torches, spare flint and steel, and a way to relight the portal. Leave valuables in the base chest.
- The portal is the way home: note its coordinates, and wall off lava or drops around the arrival platform with blocks before exploring. If the arrival is in a lava cave, bridge out with blocks and do not jump.
- Fortresses and bastions are usually a few hundred blocks away in the Nether (1 Nether block is 8 Overworld blocks). Follow netherrack caves or bridge along a level; place blocks ahead on every step you cannot see the floor of. Do not dig straight down or mine netherrack under you.
- Returning: walk into the portal and stand in it about 4 seconds. If the portal was broken, relight it with flint and steel from your kit. A bed explodes in the Nether; do not use one there.

