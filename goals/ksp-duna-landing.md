# Goal: land a kerbal on Duna and bring them home

The program is **Kerbal Space Program**, in a sandbox game: every part is unlocked, and money and science don't matter. The game's example craft are not included, so you design and build your own.

**Objective:** land a crewed vessel on **Duna**, then bring that kerbal back alive to the surface of **Kerbin** (landed or splashed down).

**Done when** a kerbal who landed on Duna is back on Kerbin's surface.

**Progress** is measured by these steps, in order: first launch (above 1 km), space (above 70 km), orbit of Kerbin (periapsis above 70 km), leaving Kerbin's sphere of influence, Duna encounter, orbit of Duna, landing on Duna (any craft), a kerbal on Duna, and that kerbal back on Kerbin. A grader reads them from the game's own state. Steps already reached earlier in this run stay reached. **Tokens** are everything you spend. Building tools that let you take each step in fewer calls is the point of this run: a transfer window, an interplanetary burn, an aerobraked capture or a landing that your own code plans and flies is cheaper than one you steer step by step.

Duna and Kerbin line up for a transfer only every two years or so of game time; waiting for a window with time warp is fine.

**Your plugin may fly for you, within a player's limits.** A plugin you write may read anything in the game, and may act through the same controls a player has. How you do that is up to you; these are the limits:
- **Flight controls:** throttle, pitch, yaw, roll and translation, as a player's keyboard or joystick sets them, any number of times per second (they are continuous, like holding a key). SAS and its modes, RCS, brakes, gear, lights and action groups, as the player toggles them.
- **One pair of hands for everything else.** Discrete actions (staging, a part's right-click action, placing or removing a part in the editor, a maneuver node, a time-warp change, switching vessels, EVA and boarding, launching, recovering) happen one after another, at most about 4 per second.
- **Building:** in the editor, with the parts a player can pick, attached where the editor allows. Saving and loading your own craft files is fine.
- **Time warp:** within the game's normal limits (on-rails warp only where the game allows it).
- **Reverting a flight and quicksave/quickload** are the game's own features, and allowed.

Some API calls do things a player can't (for example setting a vessel's orbit, position or velocity, or a part's resources); check what a call does before acting with it.

Not allowed: the cheats in the debug menu (Alt+F12: infinite fuel, no crash damage, set orbit and the rest), setting orbits, positions or velocities directly, adding fuel or other resources, changing parts' stats or the game's physics or config files, spawning vessels in flight, and editing save files. A grader flags the cheats, orbital energy that rises more than the vessel's engines allow, and vessels that appear in flight from nothing. Your plugins' code and every call to them are recorded and reviewed for the rest. Any of these voids the run.
