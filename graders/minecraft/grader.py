"""Minecraft grader: judges milestones from the world's own files and writes grader.jsonl.

    python graders/minecraft/grader.py --run-dir runs/<run> --world sisyphus-mc1 [--interval 20] [--once]
        [--milestones overnight|nether] [--expected-difficulty N]

A separate process; it never reads anything the agent writes or says, and needs no cooperation from
the game. Sources, all read-only, under <instance>/minecraft/saves/<world>/:
  advancements/<uuid>.json   what the player has achieved (Nether, End, dragon, diamonds, ...)
  stats/<uuid>.json          counters: crafted, mined, picked_up, used, killed per item or mob
  level.dat, playerdata/     allowCommands, difficulty, the player's game mode (integrity)
and the game's logs/latest.log (commands the player entered).
The game writes these on autosave (about every 5 minutes), on quit and on world save, so a milestone
can show up that much after it happened. Records (docs/interfaces/event-log.md):
  milestones_defined, milestone {id, evidence}, integrity {signal, active, detail, evidence},
  host_command {command_id, command, parameters, by, custom, tick}, grader_status {ok, detail}
The nether set (goals/minecraft-nether.md) expects peaceful (difficulty 0); any other difficulty, now or
after a change mid-run, raises the difficulty_changed integrity signal. Lit portal comes from the overworld's
poi files, a return trip from the saved player's Dimension (see Grader._dimension).
Restart-safe: it rebuilds what it has already written from grader.jsonl.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "toolserver" / "src"))
import nbt  # noqa: E402
from toolserver.events import EventLog  # noqa: E402

DEFAULT_SAVES = Path.home() / "Library/Application Support/PrismLauncher/instances/sisyphus-neoforge/minecraft/saves"
DEATH_LINE = re.compile(
    r"\[(?P<t>[^\]]+)\] \[Server thread/INFO\] \[net\.minecraft\.server\.MinecraftServer/\]: (?P<who>\S+) (?P<msg>(?:"
    r"drowned|was slain|was shot|was killed|fell |hit the ground|burned to death|went up in flames|tried to swim in lava|"
    r"was blown up|blew up|starved|suffocated|was pricked|withered away|was fireballed|died|was squashed|"
    r"experienced kinetic|was impaled|froze to death|was struck by lightning|walked into fire|discovered the floor|"
    r"was pummeled|was stung|was obliterated|was skewered|didn't want to live).*)$")
CMD_LINE = re.compile(r"\[(?P<t>[^\]]+)\] \[[^\]]*\] .*?: (?P<who>\S+) issued server command: /(?P<cmd>.*)$")
BUILDING = ("cobblestone", "planks", "stone", "dirt", "bricks", "glass", "log", "wool", "deepslate", "sandstone")


def mc(name: str) -> str:
    return name if ":" in name else "minecraft:" + name


def crafted(s, *items):
    return sum(s["stats"].get("minecraft:crafted", {}).get(mc(i), 0) for i in items)


def mined(s, *items):
    return sum(s["stats"].get("minecraft:mined", {}).get(mc(i), 0) for i in items)


def picked(s, *items):
    return sum(s["stats"].get("minecraft:picked_up", {}).get(mc(i), 0) for i in items)


def used(s, *items):
    return sum(s["stats"].get("minecraft:used", {}).get(mc(i), 0) for i in items)


def adv(s, name):
    a = s["adv"].get(mc(name))
    return bool(a and a.get("done"))


def distinct_crafted(s) -> int:
    return sum(1 for v in s["stats"].get("minecraft:crafted", {}).values() if v > 0)


def _need(ok, **evidence):
    return evidence if ok else None


# Ordered. Each judge(state) returns evidence or None. State is {"stats": ..., "adv": ...}.
MILESTONES = [
    ("first_wood", "First wood", lambda s: _need(mined(s, *[f"{w}_log" for w in
        ("oak", "birch", "spruce", "jungle", "acacia", "dark_oak", "mangrove", "cherry")]) >= 1, source="stats.mined *_log")),
    ("crafting_table", "Crafting table", lambda s: _need(crafted(s, "crafting_table") >= 1, source="stats.crafted")),
    ("stone_tools", "Stone tools", lambda s: _need(adv(s, "story/upgrade_tools") or crafted(s, "stone_pickaxe") >= 1,
                                                    source="advancement story/upgrade_tools or crafted stone_pickaxe")),
    ("furnace", "Furnace", lambda s: _need(crafted(s, "furnace") >= 1, source="stats.crafted")),
    ("chest", "Storage chest placed", lambda s: _need(used(s, "chest", "barrel") >= 1, source="stats.used chest/barrel")),
    ("shelter", "Shelter built (heuristic: 40+ building blocks placed and a door or bed placed)", lambda s: _need(
        sum(n for k, n in s["stats"].get("minecraft:used", {}).items() if any(b in k for b in BUILDING)) >= 40
        and used(s, *[f"{w}_door" for w in ("oak", "birch", "spruce", "jungle", "acacia", "dark_oak", "iron")]
                 + [f"{c}_bed" for c in ("white", "red", "blue", "green", "black", "brown", "yellow", "gray")]) >= 1,
        source="stats.used blocks + door/bed")),
    ("iron_ingot", "Iron ingot smelted", lambda s: _need(adv(s, "story/smelt_iron") or picked(s, "iron_ingot") >= 1,
                                                          source="advancement story/smelt_iron")),
    ("iron_tools", "Iron tools", lambda s: _need(adv(s, "story/iron_tools") or crafted(s, "iron_pickaxe") >= 1,
                                                  source="advancement story/iron_tools")),
    ("farm_started", "Crops planted", lambda s: _need(adv(s, "husbandry/plant_seed"), source="advancement husbandry/plant_seed")),
    ("animals_bred", "Animals bred (food source)", lambda s: _need(adv(s, "husbandry/breed_an_animal"),
                                                                    source="advancement husbandry/breed_an_animal")),
    ("crafted_10", "10 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 10, distinct=distinct_crafted(s))),
    ("crafted_25", "25 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 25, distinct=distinct_crafted(s))),
    ("diamond", "Diamonds", lambda s: _need(adv(s, "story/mine_diamond") or picked(s, "diamond") >= 1,
                                             source="advancement story/mine_diamond")),
    ("enchanting_table", "Enchanting table", lambda s: _need(crafted(s, "enchanting_table") >= 1, source="stats.crafted")),
    ("crafted_50", "50 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 50, distinct=distinct_crafted(s))),
    ("nether", "Entered the Nether", lambda s: _need(adv(s, "story/enter_the_nether"), source="advancement story/enter_the_nether")),
    ("blaze_rod", "Blaze rod", lambda s: _need(adv(s, "nether/obtain_blaze_rod") or picked(s, "blaze_rod") >= 1,
                                                source="advancement nether/obtain_blaze_rod")),
    ("eye_of_ender", "First eye of ender", lambda s: _need(crafted(s, "ender_eye") >= 1, source="stats.crafted")),
    ("eyes_12", "12 eyes of ender", lambda s: _need(crafted(s, "ender_eye") >= 12, crafted=crafted(s, "ender_eye"))),
    ("stronghold", "Found the stronghold", lambda s: _need(adv(s, "story/follow_ender_eye"), source="advancement story/follow_ender_eye")),
    ("crafted_75", "75 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 75, distinct=distinct_crafted(s))),
    ("end", "Entered the End", lambda s: _need(adv(s, "story/enter_the_end"), source="advancement story/enter_the_end")),
    ("dragon", "Ender Dragon killed", lambda s: _need(adv(s, "end/kill_dragon") or
                                                      s["stats"].get("minecraft:killed", {}).get("minecraft:ender_dragon", 0) >= 1,
                                                      source="advancement end/kill_dragon")),
]


WOODS = ("oak", "birch", "spruce", "jungle", "acacia", "dark_oak", "mangrove", "cherry")


def best(s, *items):
    """The larger of mined and picked-up counts: either one shows the player got the item."""
    return max(mined(s, *items), picked(s, *items))


def portal_lit(s):
    """A nether_portal point of interest in the overworld's poi files (written when a portal is lit, on
    the game's next chunk save), or the Nether having been entered (which needs a lit portal)."""
    if adv(s, "story/enter_the_nether"):
        return {"source": "advancement story/enter_the_nether"}
    return _need(s.get("portal_poi", 0) > 0, source="overworld poi: nether_portal", records=s.get("portal_poi", 0))


def in_nether(s):
    dim = (s.get("player") or {}).get("Dimension")
    return dim == "minecraft:the_nether" or adv(s, "story/enter_the_nether")


NETHER_MILESTONES = [
    ("first_wood", "First wood", lambda s: _need(mined(s, *[f"{w}_log" for w in WOODS]) >= 1, source="stats.mined *_log")),
    ("crafting_table", "Crafting table", lambda s: _need(crafted(s, "crafting_table") >= 1, source="stats.crafted")),
    ("stone_tools", "Stone tools", lambda s: _need(adv(s, "story/upgrade_tools") or crafted(s, "stone_pickaxe") >= 1,
                                                    source="advancement story/upgrade_tools or crafted stone_pickaxe")),
    ("furnace", "Furnace", lambda s: _need(crafted(s, "furnace") >= 1, source="stats.crafted")),
    ("chest", "Storage chest placed", lambda s: _need(used(s, "chest", "barrel") >= 1, source="stats.used chest/barrel")),
    ("crafted_10", "10 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 10, distinct=distinct_crafted(s))),
    ("iron_ingot", "Iron ingot smelted", lambda s: _need(adv(s, "story/smelt_iron") or picked(s, "iron_ingot") >= 1,
                                                          source="advancement story/smelt_iron")),
    ("iron_tools", "Iron tools", lambda s: _need(adv(s, "story/iron_tools") or crafted(s, "iron_pickaxe") >= 1,
                                                  source="advancement story/iron_tools")),
    ("bucket", "Bucket crafted", lambda s: _need(crafted(s, "bucket") >= 1, source="stats.crafted")),
    ("crafted_25", "25 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 25, distinct=distinct_crafted(s))),
    ("diamond", "Diamonds", lambda s: _need(adv(s, "story/mine_diamond") or picked(s, "diamond") >= 1,
                                             source="advancement story/mine_diamond")),
    ("diamond_pickaxe", "Diamond pickaxe", lambda s: _need(crafted(s, "diamond_pickaxe") >= 1, source="stats.crafted")),
    ("obsidian_1", "Obsidian obtained", lambda s: _need(best(s, "obsidian") >= 1, source="stats.mined/picked_up obsidian")),
    ("obsidian_10", "10 obsidian obtained (enough for a portal frame)",
     lambda s: _need(best(s, "obsidian") >= 10, count=best(s, "obsidian"), source="stats.mined/picked_up obsidian; re-mining placed blocks counts again")),
    ("flint_and_steel", "Flint and steel crafted", lambda s: _need(crafted(s, "flint_and_steel") >= 1, source="stats.crafted")),
    ("portal_lit", "Nether portal lit", portal_lit),
    ("entered_nether", "Entered the Nether", lambda s: _need(adv(s, "story/enter_the_nether"), source="advancement story/enter_the_nether")),
    ("netherrack", "Netherrack mined", lambda s: _need(best(s, "netherrack") >= 1, source="stats.mined/picked_up netherrack")),
    ("nether_quartz", "Nether quartz", lambda s: _need(picked(s, "quartz") >= 1 or mined(s, "nether_quartz_ore") >= 1,
                                                        source="stats picked_up quartz / mined nether_quartz_ore")),
    ("glowstone", "Glowstone", lambda s: _need(picked(s, "glowstone_dust") >= 1 or mined(s, "glowstone") >= 1,
                                                source="stats picked_up glowstone_dust / mined glowstone")),
    ("fortress_found", "Found a nether fortress", lambda s: _need(adv(s, "nether/find_fortress"), source="advancement nether/find_fortress")),
    ("bastion_found", "Found a bastion remnant", lambda s: _need(adv(s, "nether/find_bastion"), source="advancement nether/find_bastion")),
    ("crafted_50", "50 distinct items crafted", lambda s: _need(distinct_crafted(s) >= 50, distinct=distinct_crafted(s))),
    ("ancient_debris", "Ancient debris (stretch)", lambda s: _need(best(s, "ancient_debris") >= 1, source="stats.mined/picked_up ancient_debris")),
    ("returned_overworld", "Back in the Overworld after the Nether (no death in between)",
     lambda s: _need(s.get("returned_overworld", False), source="playerdata Dimension seen the_nether, later overworld, no death between")),
]

MILESTONE_SETS = {"overnight": MILESTONES, "nether": NETHER_MILESTONES}
# Difficulty the goal expects (level.dat Difficulty: 0 peaceful, 1 easy, 2 normal, 3 hard).
EXPECTED_DIFFICULTY = {"overnight": 2, "nether": 0}


def count_poi(world: Path, dim_dir: str, kind: str) -> int:
    """Records of a point-of-interest type in a dimension's poi region files ('' = overworld)."""
    d = (world / dim_dir / "poi") if dim_dir else (world / "poi")
    n = 0
    for f in sorted(d.glob("r.*.mca")) if d.is_dir() else []:
        try:
            for ch in nbt.region_chunks(f):
                for sec in (ch.get("Sections") or {}).values():
                    n += sum(1 for r in (sec.get("Records") or []) if r.get("type") == kind)
        except (OSError, ValueError):
            continue
    return n


def read_world(world: Path) -> dict | None:
    """{"stats", "adv", "level", "player"} from the world folder, or None if it isn't there yet."""
    if not (world / "level.dat").exists():
        return None

    def one(sub: str, suffix: str):
        files = sorted((world / sub).glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime) if (world / sub).is_dir() else []
        return files[-1] if files else None

    def j(p):
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p else {}
        except (OSError, json.JSONDecodeError):
            return {}   # being written; the next poll gets it
    stats = j(one("stats", ".json")).get("stats", {})
    advs = j(one("advancements", ".json"))
    try:
        level = nbt.load(world / "level.dat").get("Data", {})
        pf = one("playerdata", ".dat")
        player = nbt.load(pf) if pf else {}
    except (OSError, ValueError, EOFError):
        return None
    return {"stats": stats, "adv": {k: v for k, v in advs.items() if isinstance(v, dict)}, "level": level, "player": player,
            "portal_poi": count_poi(world, "", "minecraft:nether_portal")}


def signals(s: dict, expected_difficulty: int = 2) -> dict[str, tuple[bool, str]]:
    lv, pl = s["level"], s["player"]
    gm = pl.get("playerGameType", lv.get("GameType", 0))
    return {
        "commands_enabled": (bool(lv.get("allowCommands", 0)), "the world allows cheats/commands (allowCommands)"),
        "gamemode_changed": (gm != 0, f"the player's game mode is {gm}, not survival (0)"),
        "difficulty_changed": (lv.get("Difficulty", expected_difficulty) != expected_difficulty,
                               f"difficulty is {lv.get('Difficulty')}, not the expected {expected_difficulty}"),
    }


ACTION_STATS = {"mine": "minecraft:mined", "craft": "minecraft:crafted", "use": "minecraft:used",
                "kill": "minecraft:killed"}


def action_totals(stats: dict) -> dict[str, int]:
    """Cumulative player actions from the game's own statistics (it counts what the player did):
    blocks mined, items crafted, items/blocks used (placing a block is a use), mobs killed. Walking and
    pick-ups are left out (pick-ups follow from mining), so this undercounts."""
    out = {}
    for kind, key in ACTION_STATS.items():
        n = sum(v for v in (stats.get(key) or {}).values() if isinstance(v, (int, float)))
        if n:
            out[kind] = int(n)
    return out


class Grader:
    def __init__(self, run_dir: Path, world: Path, log_file: Path | None = None, milestones: str = "overnight",
                 expected_difficulty: int | None = None) -> None:
        self.set_name, self.milestones = milestones, MILESTONE_SETS[milestones]
        self.expected_difficulty = EXPECTED_DIFFICULTY[milestones] if expected_difficulty is None else expected_difficulty
        self.nether_deaths: int | None = None      # deaths counted when the player was last seen in the Nether
        self.run_dir, self.world = run_dir, world
        self.log_file = log_file or world.parents[1] / "logs" / "latest.log"
        st = run_dir / "runner-state.json"
        run_id = (json.loads(st.read_text()).get("run_id") if st.exists() else None) or run_dir.name
        self.path = run_dir / "grader.jsonl"
        self.reached: set[str] = set()
        self.active: dict[str, bool] = {}
        self.seen_cmds: set[tuple] = set()
        self.seen_deaths: set[str] = set()
        self.action_totals: dict[str, int] = {}
        self.cmd_id = 0
        self.up: bool | None = None
        self.defined = None
        self._resume()
        self.log = EventLog(self.path, run_id, writer="grader")
        defined = [{"id": i, "name": n} for i, n, _ in self.milestones]
        if self.defined != defined:
            self.log.write("milestones_defined", milestones=defined, milestone_set=self.set_name,
                           expected_difficulty=self.expected_difficulty)

    def _resume(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            t = r.get("type")
            if t == "milestones_defined":
                self.defined = r.get("milestones")
            elif t == "milestone":
                self.reached.add(r["id"])
            elif t == "integrity":
                self.active[r["signal"]] = r["active"]
            elif t == "program_actions":
                self.action_totals = {k: int(v) for k, v in (r.get("totals") or {}).items()}
            elif t == "marker" and r.get("kind") == "agent_died":
                self.seen_deaths.add(r.get("key", ""))
            elif t == "marker" and r.get("kind") == "nether_seen":
                self.nether_deaths = r.get("deaths")
            elif t == "host_command":
                self.cmd_id = max(self.cmd_id, r.get("command_id", 0))
                self.seen_cmds.add((r.get("tick"), r.get("command"), r.get("parameters")))

    def poll(self) -> dict | None:
        s = read_world(self.world)
        if s is None:
            self._status(False, f"no readable world at {self.world}")
            return None
        self._status(True, "world files readable")
        sig = signals(s, self.expected_difficulty)
        integrity = {k: v for k, (v, _) in sig.items()}
        for name, (on, detail) in sig.items():
            self._signal(name, on, detail, {"level_dat_difficulty": s["level"].get("Difficulty")})
        self._commands()
        self._deaths()
        self._actions(s)
        self._dimension(s)
        for mid, _, judge in self.milestones:
            if mid in self.reached:
                continue
            ev = judge(s)
            if ev:
                ev.update(integrity=integrity, game_time_ticks=s["level"].get("Time"))
                self.log.write("milestone", id=mid, evidence=ev)
                self.reached.add(mid)
        return s

    def _dimension(self, s: dict) -> None:
        """Remember when the saved player was in the Nether, so a later save in the Overworld with no death
        in between counts as a return trip (a respawn after dying in the Nether is not one). The player's
        file is written every few minutes, so a visit shorter than that can go unseen."""
        dim = (s.get("player") or {}).get("Dimension")
        if dim == "minecraft:the_nether":
            if self.nether_deaths != len(self.seen_deaths):
                self.nether_deaths = len(self.seen_deaths)
                self.log.write("marker", kind="nether_seen", label="player seen in the Nether", deaths=self.nether_deaths)
        s["returned_overworld"] = (dim == "minecraft:overworld" and self.nether_deaths is not None
                                   and self.nether_deaths == len(self.seen_deaths))

    def _commands(self) -> None:
        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for ln in lines:
            m = CMD_LINE.search(ln)
            if not m:
                continue
            cmd, _, params = m["cmd"].partition(" ")
            key = (m["t"], cmd, params)
            if key in self.seen_cmds:
                continue
            self.seen_cmds.add(key)
            self.cmd_id += 1
            self.log.write("host_command", command_id=self.cmd_id, command=cmd, parameters=params,
                           by=f"player {m['who']}", custom=False, tick=m["t"])

    def _actions(self, s: dict) -> None:
        """A program_actions record (shape as Factorio's grader writes it) with what is new since the last
        one. The game saves statistics every few minutes, so records arrive in lumps. A count that went
        down (an older save) restarts from the new value."""
        totals = action_totals(s.get("stats", {}))
        kinds = {}
        for k, n in totals.items():
            before = self.action_totals.get(k, 0)
            d = n - before if n >= before else n
            if d:
                kinds[k] = d
        if kinds:
            self.log.write("program_actions", count=sum(kinds.values()), kinds=kinds, totals=totals,
                           tick=(s.get("level") or {}).get("Time"), source="game statistics (mined, crafted, used, killed)")
        self.action_totals = totals

    def _deaths(self) -> None:
        """A marker (kind `agent_died`) for each death line the game logged: when, who, and the cause as the game words it."""
        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for ln in lines:
            m = DEATH_LINE.search(ln)
            if not m or m["t"] + m["msg"] in self.seen_deaths:
                continue
            self.seen_deaths.add(m["t"] + m["msg"])
            self.log.write("marker", kind="agent_died", label=f"died: {m['msg']}", cause=m["msg"], player=m["who"],
                           game_time=m["t"], key=m["t"] + m["msg"], count=len(self.seen_deaths))

    def _signal(self, name, on, detail, evidence) -> None:
        if self.active.get(name, False) != on:
            self.log.write("integrity", signal=name, active=on, detail=detail, evidence=evidence)
            self.active[name] = on

    def _status(self, ok: bool, detail: str) -> None:
        if self.up != ok:
            self.log.write("grader_status", ok=ok, detail=detail)
            self.up = ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--world", required=True, help="the world's folder name under saves/")
    ap.add_argument("--saves", type=Path, default=DEFAULT_SAVES)
    ap.add_argument("--interval", type=float, default=20)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--milestones", choices=sorted(MILESTONE_SETS), default="overnight",
                    help="which goal's milestone set to judge (nether = peaceful path to the Nether)")
    ap.add_argument("--expected-difficulty", type=int, choices=range(4), default=None,
                    help="level.dat Difficulty the goal expects; a different value is an integrity signal "
                         "(default: 2 for overnight, 0 peaceful for nether)")
    a = ap.parse_args()
    g = Grader(a.run_dir.resolve(), a.saves / a.world, milestones=a.milestones, expected_difficulty=a.expected_difficulty)
    if a.once:
        s = g.poll()
        print(json.dumps({"reached": sorted(g.reached), "signals": {k: v[0] for k, v in signals(s, g.expected_difficulty).items()} if s else None}, indent=1))
        return 0
    while True:
        g.poll()
        if (a.run_dir / "STOP").exists():
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
