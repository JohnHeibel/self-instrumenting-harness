# Goal: launch a rocket in Factorio

The program is **Factorio**, in a freeplay game where you are the only player. You start with almost nothing.

**Objective:** launch a **rocket** from a rocket silo, with every part made by automated production. This is a long goal: it takes the whole tech tree, from mining and smelting through oil, the three advanced science packs, the rocket silo and rocket parts.

**Done when** a rocket has launched. Until then, keep scaling production; the bottleneck later is throughput, so tools that build and fix whole production lines in one call pay off more and more.

**Automated means fed by machines.** A production line is automated only when its raw materials arrive without your hands: mining drills to furnaces to assemblers, linked by belts and inserters (chests are fine as buffers between machines). Filling a chest or a machine by hand to get things started is allowed, but a line you keep supplying by hand is not automated and doesn't count. The grader checks each automated milestone's supply chain back to mining drills (or pumps, for fluids).

**Progress** is measured by these steps, in order: first assembler built, red science automated, green science automated, petroleum gas, plastic, advanced circuits, engine units, blue science, production science, utility science, rocket silo, first rocket part, rocket launched. **Tokens** are everything you spend. Building tools that let you take each step in fewer calls is the point of this run.

**Your mod may act for your character, within a player's limits.** Commands your own mod registers (`commands.add_command`, called over RCON) may read anything and may act through your character the way a player could. How you do that is up to you; these are the limits:
- **One pair of hands, not one thing at a time.** Hand actions (placing, picking up, moving items, setting a recipe, starting to mine) happen one after another, at most about 4 per second (one every 15 ticks or more). Everything a player does at the same time is fine at the same time: walking while the crafting queue runs, crafting while mining, machines working while you do anything. A queue of many actions is fine; it must run at this pace.
- **Building:** place things the way a player does, from the item in hand, using items you have (they leave your inventory), within your character's build reach, only where they fit.
- **Crafting:** takes the recipe's normal time and consumes its ingredients.
- **Moving items:** between your inventory and an entity within reach, keeping totals the same.
- **Movement:** your character walks (or drives). No teleporting.
- **Mining:** within reach, taking the normal mining time.
- **Research and recipes:** set as a player can.

Some API calls do things a player can't (for example `mine_entity` mines instantly, and `teleport` moves the character); check what a call does before acting with it.

Not allowed: creating items or entities from nothing, teleporting, instant crafting or mining, reaching beyond the character's reach, changing the map, `cheat_mode`, the map editor, and Lua console commands (`/c`, `/sc`) that change the game. A grader flags console commands, cheat mode and the map editor. Your extensions' code and every call to them are recorded and reviewed for the rest. Any of these voids the run.
