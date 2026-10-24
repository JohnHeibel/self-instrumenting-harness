# Goal: launch a rocket in Factorio

The program is **Factorio**, in a freeplay game where you are the only player. You start with almost nothing.

**Objective:** launch a **rocket** from a rocket silo, with every part made by automated production. This is a long goal: it takes the whole tech tree, from mining and smelting through oil, the three advanced science packs, the rocket silo and rocket parts.

**Done when** a rocket has launched. Until then, keep scaling production; the bottleneck later is throughput, so tools that build and fix whole production lines in one call pay off more and more.

**Progress** is measured by these steps, in order: first assembler built, red science automated, green science automated, petroleum gas, plastic, advanced circuits, engine units, blue science, production science, utility science, rocket silo, first rocket part, rocket launched. **Tokens** are everything you spend. Building tools that let you take each step in fewer calls is the point of this run.

**Your mod may act for your character, within a player's limits.** Commands your own mod registers (`commands.add_command`, called over RCON) may read anything and may act through your character the way a player could, through a player's controls. For example:
- **Hand and quickbar:** a player places things from the cursor (their hand). Put the item in the cursor first (from the quickbar or the inventory, `cursor_stack`), then place it with `build_from_cursor`, and clear the cursor when done. Set up the quickbar (`set_quick_bar_slot`) with what you build most and pick from it, as a player would.
- **Pace:** at most about 4 actions per second (one build, transfer, recipe change or mining start every 15 ticks or more), and one action at a time. A queue of many actions is fine; it must run at this pace.
- **Crafting:** `begin_crafting` on your player (it takes the normal time and consumes the ingredients).
- **Building:** place only an item you have in your inventory, remove it from the inventory when placed, and only within your character's build reach. Check with `can_place_entity` and the character's reach.
- **Moving items:** move items between your inventory and an entity within reach, keeping totals the same.
- **Movement:** walk with `walking_state` or pathfinding plus walking. No teleporting.
- **Mining:** mine with `mining_state` on things within reach, so it takes the normal mining time. Not `mine_entity`: it mines instantly, and a player can't.
- **Research and recipes:** set research and assembler recipes as a player can.

Not allowed: creating items or entities from nothing, teleporting, instant crafting or mining, reaching beyond the character's reach, changing the map, `cheat_mode`, the map editor, and Lua console commands (`/c`, `/sc`) that change the game. A grader flags console commands, cheat mode and the map editor. Your extensions' code and every call to them are recorded and reviewed for the rest. Any of these voids the run.
