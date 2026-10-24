# Goal: reach the Nether on peaceful difficulty, learn as much as you can, and build tools that make the next step cheaper

The program is **Minecraft: Java Edition 1.21.1** with NeoForge and the Minescript mod, in a **survival, peaceful-difficulty singleplayer world** (name `sisyphus-mc2`, cheats off) that is already open. You are the only player. You start with nothing in your inventory. Peaceful is the expected setting: do not change it, and do not touch the game mode.

Your workspace starts empty. Every skill, hook and script you use you write in this run: the point is to see how well you learn, cache and reuse procedures from scratch. Learning a procedure once and then calling it in one step is the main thing that is judged.

## What peaceful changes

Hostile mobs (zombies, skeletons, creepers, spiders, and in the Nether ghasts, blazes and the like) do not spawn, and hunger does not run down. Health regenerates by itself. So you can work on the surface at night and do not need armor, a sword or a shelter. Still fatal: **lava, drowning, falling (more than 3 blocks hurts, more than about 20 kills), fire, suffocation in blocks, and the void**. Run 1 (normal difficulty) died 18 times, 6 of them by drowning in caves and 1 by a long fall; the water, lava and fall guards stay in every movement and mining tool. In a fortress or bastion, look before you step: peaceful does not make the terrain safe.

## Objective, in order

The measured path (judged from the world's own files; see Progress):

1. Wood, crafting table, stone tools, furnace, a storage chest, 10 distinct items crafted.
2. Iron: iron ingot, iron tools, **bucket**.
3. **Diamonds**, then a **diamond pickaxe** (3 diamonds and 2 sticks). Diamonds are rarest between about y=-59 and y=-50; mine **horizontally** along a level tunnel, with torches, and do not dig straight down.
4. **Obsidian**, at least **10** (a portal frame needs 10 blocks, 4 wide and 5 tall, corners not needed). Two ways: mine it with the diamond pickaxe from where water meets a lava source (pour water from the bucket over a lava pool; a still lava source turns to obsidian, flowing lava to cobblestone), or cast it: place the frame's shape out of lava source blocks over water. Either way, only with a bucket, water and the blocks you carry. Work out your own safe procedure, then keep it as a skill.
5. **Flint and steel** (an iron ingot and a flint; flint drops from gravel).
6. Build the portal, **light it**, **enter the Nether**.
7. In the Nether: **netherrack**, **nether quartz**, **glowstone dust**, find a **nether fortress** and a **bastion remnant**. Stretch: ancient debris (diamond pickaxe, y 8 to 22), 25 and 50 distinct items crafted.
8. **Return safely to the Overworld** through the portal, with what you collected.

Breadth also counts: the number of distinct items you have crafted is measured. Bank it early near the base (peaceful makes it safe): write one skill that crafts a whole list from the logs, cobblestone, coal, sticks and iron you hold, in one call, and reports which new items it produced.

## Progress

Milestones, from the world files (advancements, statistics, the saved player, the Overworld's point-of-interest files), written by the game about every five minutes: first wood, crafting table, stone tools, furnace, chest, 10 crafted, iron ingot, iron tools, bucket, 25 crafted, diamond, diamond pickaxe, 1 obsidian, 10 obsidian, flint and steel, portal lit, Nether entered, netherrack, nether quartz, glowstone, fortress found, bastion found, 50 crafted, ancient debris, and returned to the Overworld. **Tokens**, steps and wall-clock time are what you spend; the run is judged on how many of these you reach, and on whether each one comes in fewer steps than the one before.

## Player actions only

Same rules as before. Hooks and scripts may read anything the client knows. What they do to the world must be what a player can do: walk and jump, look around, break blocks you can reach with the right tool at the normal mining time, place blocks and items from your inventory, use blocks, open containers and move items between slots, craft in the 2x2 or crafting-table grid, attack and eat, sleep. The game is the real client at normal speed (discrete actions at about 4 per second).

Not allowed, and flagged or voiding the run: chat or console commands that change the game (`/give`, `/tp`, `/gamemode`, `/difficulty`, `/time`, `/setblock`, `/summon`, and the like), turning on cheats or opening the world to LAN, editing the world, save or game files, spawning or duplicating items or entities, teleporting, instant mining or crafting, reaching beyond a player's reach, and changing the game mode or difficulty (including in the Options menu). Minescript's own commands (`\jobs`, `\killjob`, your scripts) are fine. A grader reads the world files and logs for cheats, the game mode, the difficulty and commands used; your extensions' code and calls are recorded and reviewed.

## Start here

1. Read `/home/agent/work/run/HOST.md` for how Minescript works. Write a small bridge script for the game (see the host notes) and an extension that wraps it, load it with `load_extension`, and call the game only through your registered tools or `run_script` so the calls are logged. Give every call a short timeout.
2. Create `/home/agent/work/BACKLOG.md` and `/home/agent/work/skills/INDEX.md` in your first steps (formats below), and keep both current. The runner puts the top of BACKLOG.md and the skills index in front of you after every restart, reset or stall.
3. Start the first chore the moment its tool exists, and start the next before you think about the one after.

## Keep your hands busy: the backlog

The program is only progressing while something is acting on it. Thinking, reading notes and writing code all leave the game idle, and in run 1 most wall-clock time went to gaps between steps. So there is always a plan written down and always something running.

- **BACKLOG.md** is an ordered list of chores, each one a kept script (or tool call) with its `args`, what done looks like, and what to do if it fails. Three sections: `## Running` (task ids and what they are), `## Queue` (the next chores in order, each already started or ready to start), `## Decisions` (branches decided in advance: "if the cave floods, then...", "when 3 diamonds are in hand, then craft the pickaxe, then go for obsidian"). Keep it short, under about 60 lines; move finished items to `## Done` with a line of what you learned.
- **Use the harness queue.** A long chore (a walk, a mining run, smelting, a crafting sequence, a trip to the chest) runs as `run_script(..., background=True)`; queue the next one behind it with `after="tN"` so a line of chores runs back to back while you plan. Before you plan anything, make sure the queue holds at least two chores. If something fails, what is queued behind it is held: fix it, then `task(id, resume=True)`, or drop it.
- **Never poll and never wait idle.** Wait with `wait(on=[...])` (it returns early on a notice) or set a rule with `watch` for what you would otherwise check. Planning, writing the next tool and updating notes happen while a task runs. A task that needs the hands waits its turn, so you can line up work that touches the game even while something else is acting.
- **Decisions ahead of time.** When a chore ends, the next one should already be running. Turn the common branches into a kept dispatcher script (an `autopilot`): it reads a queue file you edit (for example `state/queue.json`: script, args, precondition, on-failure), runs the next item, checks safety each slice, writes a heartbeat, and calls `notify()` when it stops or needs you. Run it as a background task so it keeps the hands busy between your steps; edit the file while it runs. It stops and says why at the first hazard (low health, lava or water ahead, a drop over 3, an item it lacks).
- **After a reset or restart:** call `status` first, then read BACKLOG.md, NOTES.md and the skills index, then start the first item that is not already running. Do not re-plan from scratch. Write a status note at the top of NOTES.md before any long operation and at every milestone.

## Build reusable tools, in this order of leverage

Cache every procedure you do twice, and script it at the third repeat. Each of these is a general tool with parameters, kept as a skill and listed in the index, not a one-off:

- **State**: a heartbeat (position, dimension, health, hotbar, key inventory counts) written every slice to `state/heartbeat.json`; nearby blocks and entities; the open screen and container contents.
- **Movement and pathing**: walk to a point (with a stuck detector that gives up with a reason), climb with stairs, bridge a gap, cross water safely, pillar up, mine a staircase down in short safe steps. Every step looks ahead, below and beside for water, lava, a drop over 3 and a fall.
- **Horizontal mining**: a level tunnel of a given length, with torches at intervals and ore pick-up on the sides, checking each block and the block beyond it for water or lava before breaking it (a dug block can release an aquifer). Vein mining of exposed ore. Run 1 died in vertical shafts, so branch-mine horizontally at the right y (iron around y=16 or exposed on hillsides, diamonds near y=-58).
- **Inventory and kit management**: count items; make room; keep a *kit* (pickaxe, spare tools, bucket, blocks, torches, food) and put everything else in the base chest before a trip; fetch from the chest by name. Do not carry diamonds and ingots into risky ground you do not need them in.
- **Crafting by recipe**: one `craft(item, n)` that works out the sub-recipes from what you hold, uses the 2x2 or table grid, and returns what was made. Smelting by batch (a furnace takes 10 s per item; start it, do something else, collect).
- **Base and storage**: a base with a table, furnace, chests and a bed at one spot, and tools that go home, deposit, restock and come back.
- **Fluids and obsidian**: pick up and place water and lava with the bucket safely; make obsidian; build the portal frame from a list of block positions; light it.
- **Nether travel**: move in the Nether on netherrack with a block-bridging walker, never walking toward an unseen drop or lava; mark the portal's coordinates and the way home in the heartbeat.

## Lessons from the earlier run (how to learn, not code to copy)

- **Kit discipline.** Every death dropped the whole kit and cost a rebuild; the iron tools were lost seven minutes after they were made. Keep spare tools and valuables in the base chest and carry only what the next job uses.
- **Mine horizontally.** Vertical shafts and big `tunnel_to` drops caused the fall death and several drownings. Check the block ahead and the ones around it for water or lava every slice.
- **Bank breadth early.** Craft your way to 25 distinct items right after the first stone and iron, where nothing can kill you.
- **Start with context handoff on** (the runner does it): keep NOTES.md and the skills index good enough that a fresh session can carry on. Skills carried the last run through four resets.
- **A long job reports progress** every few seconds, has a count or deadline, writes a heartbeat, and returns a short reason when it stops. A wedged job on a one-queue bridge blocked every read for minutes: keep reads answerable while a job runs.
- **Wait inside a step, not in many short polls**; and use background tasks so the waiting is also work.
- **Look with tools, not screenshots.** A screenshot costs a step and a large image. Take one to learn something your tools cannot tell you yet, then extend the tools.

## The Nether trip

- Before going: a stone pickaxe or better, 64 cobblestone (or other blocks), a bucket (water evaporates in the Nether, so water cannot save you from lava there; blocks can), food, torches, spare flint and steel, and a way to relight the portal. Leave valuables in the base chest.
- The portal is the way home: note its coordinates, and wall off lava or drops around the arrival platform with blocks before exploring. If the arrival is in a lava cave, bridge out with blocks and do not jump.
- Fortresses and bastions are usually a few hundred blocks away in the Nether (1 Nether block is 8 Overworld blocks). Follow netherrack caves or bridge along a level; place blocks ahead on every step you cannot see the floor of. Do not dig straight down or mine netherrack under you.
- Returning: walk into the portal and stand in it about 4 seconds. If the portal was broken, relight it with flint and steel from your kit. A bed explodes in the Nether; do not use one there.
