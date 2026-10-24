"""Factorio grader: judges milestones from the game's own state and writes grader.jsonl.

    python graders/factorio/grader.py --run-dir runs/<run> [--interval 10] [--once]

A separate process. It asks the harness-grader mod (graders/factorio/mod, installed into the
run's mods folder by hosts/factorio/hostctl.py) for production counts over RCON every
--interval seconds. It never reads anything the agent writes or says. Records
(docs/interfaces/event-log.md):

  milestones_defined  once, the ordered milestone list
  milestone           {id, evidence} when reached: the counts, game tick and integrity state
  integrity           {signal, active, detail, evidence} when a signal turns on or off:
                      console_command_used (a Lua/console command was used in this save),
                      cheat_mode, editor_mode, grader_unavailable (the mod didn't answer), and
                      the mod's fair-play checks (FAIRPLAY; how each works is in mod/control.lua):
                        position_jump     the character moved farther between samples than it can
                        conservation      items exist that nothing produced (items from nothing)
                        instant_crafting  hand crafting finished faster than recipe times allow
                      These stay on once found, and a new record (still active) is written each
                      time the count of instances grows, with the recent instances as evidence.
  host_command        {command, parameters, by, custom, tick}: each console command the game
                      saw, entered by a player or sent over RCON; `custom` is a mod-registered
                      command (the agent's helpers), so the log shows which channel changed state
  grader_status       {ok, detail} when the game becomes reachable or unreachable

Restart-safe: it rebuilds what it has already written from grader.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "hosts" / "factorio"))
sys.path.insert(0, str(REPO / "toolserver" / "src"))
from rcon import Rcon, RconError  # noqa: E402
from toolserver.events import EventLog  # noqa: E402

COMMAND = "/harness-grader"
HEALTH_COMMANDS = {"players", "version"}   # read-only built-ins; not logged as host_command

# Ordered. Each is reached when one of its counts reaches `min`:
#   items/fluids: the force's production statistics (made by anyone: machines or hand crafting)
#   recipe: products finished by assembling machines set to that recipe whose supply traces back
#           to mining drills with no hand-fed chest or machine on the way (the mod's supply-chain
#           check; excluded machines and why are in the evidence)
MILESTONES = [
    {"id": "first_assembler", "name": "First assembling machine",
     "items": ["assembling-machine-1", "assembling-machine-2", "assembling-machine-3"], "min": 1},
    {"id": "red_auto", "name": "Red science automated", "recipe": "automation-science-pack", "min": 10},
    {"id": "green_auto", "name": "Green science automated", "recipe": "logistic-science-pack", "min": 10},
    {"id": "oil", "name": "Oil processing", "fluids": ["petroleum-gas"], "min": 1},
    {"id": "plastic", "name": "Plastic", "items": ["plastic-bar"], "min": 1},
    {"id": "adv_circuit", "name": "Advanced circuits", "items": ["advanced-circuit"], "min": 1},
    {"id": "engine", "name": "Engine units", "items": ["engine-unit"], "min": 1},
    {"id": "blue", "name": "Blue science", "items": ["chemical-science-pack"], "min": 1},
    {"id": "purple", "name": "Production science", "items": ["production-science-pack"], "min": 1},
    {"id": "yellow", "name": "Utility science", "items": ["utility-science-pack"], "min": 1},
    {"id": "silo", "name": "Rocket silo", "items": ["rocket-silo"], "min": 1},
    {"id": "rocket_part", "name": "First rocket part", "items": ["rocket-part"], "min": 1},
    {"id": "launch", "name": "Rocket launched", "stat": "rockets_launched", "min": 1},
]


def request() -> dict:
    return {"items": sorted({i for m in MILESTONES for i in m.get("items", [])}),
            "fluids": sorted({f for m in MILESTONES for f in m.get("fluids", [])}),
            "recipes": sorted({m["recipe"] for m in MILESTONES if "recipe" in m})}


def judge(m: dict, state: dict) -> dict | None:
    """Evidence if milestone `m` is reached in `state` (the mod's reply), else None."""
    if "stat" in m:   # a force counter the mod reports, e.g. rockets_launched
        n = state.get(m["stat"]) or 0
        return {"source": "force", "stat": m["stat"], "value": n, "min": m["min"]} if n >= m["min"] else None
    if "recipe" in m:
        r = _dict(state.get("recipes")).get(m["recipe"]) or {}
        if r.get("finished", 0) >= m["min"]:
            ev = {"source": "assembling machines", "recipe": m["recipe"], "finished": r["finished"],
                  "machines": r.get("machines", 0), "min": m["min"]}
            if "all_machines" in r:   # mod 0.2+: only machines with an automated supply count
                ev.update(supply_chain=True, all_machines=r["all_machines"], all_finished=r.get("all_finished", 0),
                          excluded=r.get("excluded") or [])
            return ev
        return None
    kind = "items" if "items" in m else "fluids"
    have = _dict(state.get(kind))
    counts = {name: have.get(name, 0) for name in m[kind]}
    if sum(counts.values()) >= m["min"]:
        return {"source": f"production statistics ({kind})", "counts": counts, "min": m["min"]}
    return None


def signals(state: dict) -> dict[str, tuple[bool, str]]:
    players = state.get("players") or []
    cheats = [p["name"] for p in players if p.get("cheat_mode")]
    editors = [p["name"] for p in players if p.get("controller") == "editor"]
    return {
        "console_command_used": (bool(state.get("console_command_used")),
                                 "the save records that a console (Lua) command was used"),
        "cheat_mode": (bool(cheats), "cheat mode on for " + ", ".join(cheats) if cheats else "cheat mode off"),
        "editor_mode": (bool(editors), "map editor open for " + ", ".join(editors) if editors else "no map editor"),
    }


FAIRPLAY = ("position_jump", "conservation", "instant_crafting")


def fairplay_signals(state: dict) -> dict[str, tuple[bool, str, dict, int]]:
    """(active, detail, evidence, count) per fair-play check the mod reported. Empty for a mod
    older than 0.2.0, which has no `fairplay` field."""
    fp = _dict(state.get("fairplay"))
    out = {}
    for name in FAIRPLAY:
        r = _dict(fp.get(name))
        if not r:
            continue
        n = int(r.get("count") or 0)
        recent = _list(r.get("items") if name == "conservation" else r.get("recent"))
        evidence = {"count": n, "tick": state.get("tick"),
                    ("items" if name == "conservation" else "recent"): recent}
        for k in ("checked_tick", "baseline_tick", "tolerance", "sample_ticks", "tracked"):
            if k in r:
                evidence[k] = r[k]
        out[name] = (n > 0, _fairplay_detail(name, n, recent), evidence, n)
    return out


def _fairplay_detail(name: str, n: int, recent: list) -> str:
    if n == 0:
        return {"position_jump": "no position jumps", "conservation": "no items from nothing",
                "instant_crafting": "no crafting faster than recipe times"}[name]
    last = recent[-1] if recent else {}
    if name == "position_jump":
        f, t = last.get("from") or {}, last.get("to") or {}
        return (f"{n} position jump(s); last: {last.get('who')} moved {last.get('distance')} tiles in "
                f"{last.get('ticks')} ticks ({last.get('mode')}, limit {last.get('limit')}) from "
                f"({f.get('x')}, {f.get('y')}) to ({t.get('x')}, {t.get('y')}) at tick {last.get('to_tick')}")
    if name == "conservation":
        items = ", ".join(f"{i.get('item')} +{i.get('max_excess')}" for i in recent[:8])
        return f"{n} item(s) exceed what was produced: {items}"
    return (f"{n} crafting stretch(es) too fast; last: {last.get('who')} made {last.get('crafts')} craft(s) "
            f"ending in {last.get('recipe')} in {last.get('elapsed')} ticks, needing {last.get('required')}")


class Grader:
    def __init__(self, run_dir: Path, connect=None) -> None:
        self.run_dir = run_dir
        state = run_dir / "runner-state.json"
        run_id = (json.loads(state.read_text()).get("run_id") if state.exists() else None) or run_dir.name
        self.path = run_dir / "grader.jsonl"
        self.reached: set[str] = set()
        self.active: dict[str, bool] = {}
        self.counts: dict[str, int] = {}
        self.last_command = 0
        self.up: bool | None = None
        self._resume()
        self.log = EventLog(self.path, run_id, writer="grader")
        self.connect = connect or self._connect
        defined = [{"id": m["id"], "name": m["name"]} for m in MILESTONES]
        if self.defined != defined:   # new run, or the list grew since this run started
            self.log.write("milestones_defined", milestones=defined)

    def _resume(self) -> None:
        self.defined: list | None = None
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
                n = _dict(r.get("evidence")).get("count")
                if isinstance(n, int):
                    self.counts[r["signal"]] = n
            elif t == "host_command":
                self.last_command = max(self.last_command, r.get("command_id", 0))

    def _connect(self) -> Rcon:
        info = json.loads((self.run_dir / "host" / "rcon.json").read_text())
        return Rcon("127.0.0.1", info["port"], info["password"], timeout=10)

    def poll(self) -> dict | None:
        """One query; writes whatever is new. Returns the mod's reply, or None if unavailable."""
        try:
            with self.connect() as r:
                raw = r.command(COMMAND + " " + json.dumps({"since": self.last_command, **request()}))
        except (OSError, RconError) as e:
            self._status(False, f"no RCON: {e}")
            return None
        self._status(True, "game answers RCON")
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            self._signal("grader_unavailable", True, "the harness-grader mod didn't answer",
                         {"reply": raw.strip()[:300]})
            return None
        self._signal("grader_unavailable", False, "the harness-grader mod answers", {})
        fair = fairplay_signals(state)
        integrity = {k: v for k, (v, _) in signals(state).items()}
        integrity.update({k: v[0] for k, v in fair.items()})
        for c in state.get("commands") or []:
            self.last_command = max(self.last_command, c["id"])
            if c.get("by") == "rcon" and c["command"] in HEALTH_COMMANDS:
                continue   # hostctl's up_check polls these every few seconds; they change nothing
            self.log.write("host_command", command_id=c["id"], command=c["command"], parameters=c.get("parameters", ""),
                           by=c.get("by"), custom=bool(c.get("custom")), tick=c.get("tick"))
        for name, (on, detail) in signals(state).items():
            self._signal(name, on, detail, {"tick": state.get("tick"), "players": state.get("players")})
        for name, (on, detail, evidence, n) in fair.items():
            self._signal(name, on, detail, evidence, count=n)
        for m in MILESTONES:
            if m["id"] in self.reached:
                continue
            evidence = judge(m, state)
            if evidence:
                evidence.update(tick=state.get("tick"), integrity=integrity, mods=_dict(state.get("mods")))
                self.log.write("milestone", id=m["id"], evidence=evidence)
                self.reached.add(m["id"])
        return state

    def _signal(self, name: str, on: bool, detail: str, evidence: dict, count: int | None = None) -> None:
        grew = count is not None and on and count > self.counts.get(name, 0)
        if self.active.get(name, False) != on or grew:
            self.log.write("integrity", signal=name, active=on, detail=detail, evidence=evidence)
            self.active[name] = on
        if count is not None:
            self.counts[name] = count

    def _status(self, ok: bool, detail: str) -> None:
        if self.up != ok:
            self.log.write("grader_status", ok=ok, detail=detail)
            self.up = ok


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}   # Factorio writes an empty table as []


def _list(v) -> list:
    return v if isinstance(v, list) else []   # an empty Lua table can also come as {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--once", action="store_true", help="poll once, print the reply, exit")
    args = ap.parse_args()
    g = Grader(args.run_dir.resolve())
    if args.once:
        print(json.dumps(g.poll(), indent=1))
        return 0
    # Runs until killed (hostctl stop --final) or the run's STOP file appears (after one last poll).
    while True:
        g.poll()
        if (args.run_dir / "STOP").exists():
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
