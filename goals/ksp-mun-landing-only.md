# Goal: land a kerbal on the Mun

The program is **Kerbal Space Program**, in a sandbox game: every part is unlocked, and money and science don't matter. You start at the Space Center with four kerbals and no craft. The game's example craft are not included, so you design and build your own.

**Objective:** land a crewed vessel on the **Mun**, upright on its landing legs, intact, with its kerbal alive. Bringing them home is not part of this run.

**Done when** a crewed vessel is landed on the Mun and stays upright on its legs: its long axis close to the local vertical (within about 20°), not tipped over or lying on its side. A craft that touches down and falls over does not count; check it from the game's state after it has settled.

**Progress** is measured by these steps, in order: first launch (above 1 km), space (above 70 km), orbit of Kerbin (periapsis above 70 km), Mun encounter, orbit of the Mun, landing on the Mun (any craft), and a kerbal on the Mun. A grader reads them from the game's own state. **Tokens** are everything you spend. Building tools that let you take each step in fewer calls is the point of this run: a launch, an orbit, a transfer or a landing that your own code flies is cheaper than one you steer step by step.

**Your plugin may fly for you, within a player's limits.** A plugin you write may read anything in the game, and may act through the same controls a player has. How you do that is up to you; these are the limits:
- **Flight controls:** throttle, pitch, yaw, roll and translation, as a player's keyboard or joystick sets them, any number of times per second (they are continuous, like holding a key). SAS and its modes, RCS, brakes, gear, lights and action groups, as the player toggles them.
- **One pair of hands for everything else.** Discrete actions (staging, a part's right-click action, placing or removing a part in the editor, a maneuver node, a time-warp change, switching vessels, EVA and boarding, launching, recovering) happen one after another, at most about 4 per second.
- **Building:** in the editor, with the parts a player can pick, attached where the editor allows. Saving and loading your own craft files is fine.
- **Time warp:** within the game's normal limits (on-rails warp only where the game allows it).
- **Reverting a flight and quicksave/quickload** are the game's own features, and allowed.

Some API calls do things a player can't (for example setting a vessel's orbit, position or velocity, or a part's resources); check what a call does before acting with it.

Not allowed: the cheats in the debug menu (Alt+F12: infinite fuel, no crash damage, set orbit and the rest), setting orbits, positions or velocities directly, adding fuel or other resources, changing parts' stats or the game's physics or config files, spawning vessels in flight, and editing save files. A grader flags the cheats, orbital energy that rises more than the vessel's engines allow, and vessels that appear in flight from nothing. Your plugins' code and every call to them are recorded and reviewed for the rest. Any of these voids the run.
