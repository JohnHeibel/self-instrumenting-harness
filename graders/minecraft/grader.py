"""Minecraft grader: judges milestones from the world's own files and writes grader.jsonl.

    python graders/minecraft/grader.py --run-dir runs/<run> --world sisyphus-mc1 [--interval 20] [--once]

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
    return {"stats": stats, "adv": {k: v for k, v in advs.items() if isinstance(v, dict)}, "level": level, "player": player}


def signals(s: dict) -> dict[str, tuple[bool, str]]:
    lv, pl = s["level"], s["player"]
    gm = pl.get("playerGameType", lv.get("GameType", 0))
    return {
        "commands_enabled": (bool(lv.get("allowCommands", 0)), "the world allows cheats/commands (allowCommands)"),
        "gamemode_changed": (gm != 0, f"the player's game mode is {gm}, not survival (0)"),
        "difficulty_changed": (lv.get("Difficulty", 2) != 2, f"difficulty is {lv.get('Difficulty')}, not normal (2)"),
    }


class Grader:
    def __init__(self, run_dir: Path, world: Path, log_file: Path | None = None) -> None:
        self.run_dir, self.world = run_dir, world
        self.log_file = log_file or world.parents[1] / "logs" / "latest.log"
        st = run_dir / "runner-state.json"
        run_id = (json.loads(st.read_text()).get("run_id") if st.exists() else None) or run_dir.name
        self.path = run_dir / "grader.jsonl"
        self.reached: set[str] = set()
        self.active: dict[str, bool] = {}
        self.seen_cmds: set[tuple] = set()
        self.cmd_id = 0
        self.up: bool | None = None
        self.defined = None
        self._resume()
        self.log = EventLog(self.path, run_id, writer="grader")
        defined = [{"id": i, "name": n} for i, n, _ in MILESTONES]
        if self.defined != defined:
            self.log.write("milestones_defined", milestones=defined)

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
            elif t == "host_command":
                self.cmd_id = max(self.cmd_id, r.get("command_id", 0))
                self.seen_cmds.add((r.get("tick"), r.get("command"), r.get("parameters")))

    def poll(self) -> dict | None:
        s = read_world(self.world)
        if s is None:
            self._status(False, f"no readable world at {self.world}")
            return None
        self._status(True, "world files readable")
        integrity = {k: v for k, (v, _) in signals(s).items()}
        for name, (on, detail) in signals(s).items():
            self._signal(name, on, detail, {"level_dat_difficulty": s["level"].get("Difficulty")})
        self._commands()
        for mid, _, judge in MILESTONES:
            if mid in self.reached:
                continue
            ev = judge(s)
            if ev:
                ev.update(integrity=integrity, game_time_ticks=s["level"].get("Time"))
                self.log.write("milestone", id=mid, evidence=ev)
                self.reached.add(mid)
        return s

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
    a = ap.parse_args()
    g = Grader(a.run_dir.resolve(), a.saves / a.world)
    if a.once:
        s = g.poll()
        print(json.dumps({"reached": sorted(g.reached), "signals": {k: v[0] for k, v in signals(s).items()} if s else None}, indent=1))
        return 0
    while True:
        g.poll()
        if (a.run_dir / "STOP").exists():
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
