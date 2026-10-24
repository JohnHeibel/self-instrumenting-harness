# Goal: reach blue science in Factorio

The program is **Factorio**, in a freeplay game where you are the only player. You start with almost nothing.

**Objective:** produce **chemical science packs** (blue science) in assemblers, from automated production. That means a working chain: mining, smelting, intermediate products, oil processing and plastic, advanced circuits, engine units, and the packs themselves.

**Done when** chemical science packs are being produced automatically. After the first ones, keep going until production runs steadily without your hands on it.

**Progress** is measured by these steps, in order: first assembler built, red science automated, green science automated, petroleum gas, plastic, advanced circuits, engine units, blue science. **Tokens** are everything you spend. Building tools that let you take each step in fewer calls is the point of this run.

**Your mod may act for your character, within a player's limits.** Commands your own mod registers (`commands.add_command`, called over RCON) may read anything and may act through your character the way a player could. For example:
- **Crafting:** `begin_crafting` on your player (it takes the normal time and consumes the ingredients).
- **Building:** place only an item you have in your inventory, remove it from the inventory when placed, and only within your character's build reach. Check with `can_place_entity` and the character's reach.
- **Moving items:** move items between your inventory and an entity within reach, keeping totals the same.
- **Movement:** walk with `walking_state` or pathfinding plus walking. No teleporting.
- **Mining:** mine with `mining_state` or `mine_entity` on things within reach, the way a player mines.
- **Research and recipes:** set research and assembler recipes as a player can.

Not allowed: creating items or entities from nothing, teleporting, instant crafting, reaching beyond the character's reach, changing the map, `cheat_mode`, the map editor, and Lua console commands (`/c`, `/sc`) that change the game. A grader flags console commands, cheat mode and the map editor. Your extensions' code and every call to them are recorded and reviewed for the rest. Any of these voids the run.
