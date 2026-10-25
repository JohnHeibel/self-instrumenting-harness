"""KSP grader: judges milestones from the game's own state and writes grader.jsonl.

    python graders/ksp/grader.py --run-dir runs/<run> [--interval 2] [--once]

A separate process. It reads the state the harness-grader plugin (graders/ksp/plugin, built into
the run's game by hosts/ksp/hostctl.py) writes every second to <run>/host/ksp/harness/state.json.
It never reads anything the agent writes or says. Records (docs/interfaces/event-log.md):

  milestones_defined  once, the ordered milestone list
  milestone           {id, evidence} when reached: the vessel(s), game time and integrity state
  crew_landed         {kerbal, body} the first time a kerbal is seen landed on a body other than
                      Kerbin (the return milestone needs it, so it survives a grader restart)
  integrity           {signal, active, detail, evidence} when a signal turns on or off:
                        cheat_options       a cheat in the game's debug menu is on (infinite fuel,
                                            no crash damage, ...)
                        energy_jump         a vessel's orbital energy rose more than its engines
                                            allow between two samples (an orbit set by the debug
                                            menu or a plugin, or a vessel moved)
                        vessel_from_nothing a vessel appeared in flight with nothing near it at the
                                            same body and height (spawned, not launched or staged)
                        grader_unavailable  the plugin's state is missing, stale or has an error
                      Fair-play signals stay on once found; a new record is written each time the
                      count grows, with the recent instances as evidence.
  program_actions     {count, kinds, totals, source} when the player did more in the game: the new
                      actions since the last record, from the plugin's counts of the game's own
                      events (staging, editor part placement, launches, EVA, maneuver nodes, time
                      warp, ...; see plugin/HarnessGrader.cs)
  program_activity    {kinds, totals, source}: seconds with throttle or attitude input; a player
                      is busy, but these aren't counted as actions
  grader_status       {ok, detail} when the game's state becomes readable or unreadable

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
sys.path.insert(0, str(REPO / "toolserver" / "src"))
from toolserver.events import EventLog  # noqa: E402

HOME = "Kerbin"
SPACE_ALT = 70_000                 # Kerbin's atmosphere ends here
STALE_S = 20
IN_FLIGHT = {"FLYING", "SUB_ORBITAL", "ORBITING", "ESCAPING"}
DOWN = {"LANDED", "SPLASHED"}
# Not craft: these don't reach milestones.
NOT_CRAFT = {"Debris", "SpaceObject", "Flag", "Unknown", "DeployedScienceController", "DeployedSciencePart",
             "DroppedPart", "DeployedGroundPart"}
CHEATS = ("InfinitePropellant", "InfiniteElectricity", "NoCrashDamage", "IgnoreMaxTemperature", "UnbreakableJoints",
          "MiddleMouseClickSetPosition", "IgnoreEVAConstructionMassLimit", "IgnoreKerbalInventoryLimits")
# energy_jump: allowed rise in specific orbital energy between samples (J/kg) is
#   speed * (max_accel * dt + SLACK_DV) + SLACK_E
# (dE/dt = v . a, and a can't exceed the vessel's engines; the slack covers decouplers, physics
# jitter and the switch between physics and on-rails orbits).
SLACK_DV = 20.0
SLACK_E = 200.0
NEAR_ALT = 5_000                   # vessel_from_nothing: another vessel within this height counts as near
RECENT = 5

MILESTONES = [
    {"id": "launch", "name": "First launch (above 1 km)"},
    {"id": "space", "name": "Reached space (above 70 km)"},
    {"id": "orbit", "name": "Orbit of Kerbin"},
    {"id": "mun_soi", "name": "Mun encounter"},
    {"id": "mun_orbit", "name": "Orbit of the Mun"},
    {"id": "mun_landing", "name": "Landed on the Mun"},
    {"id": "crewed_mun_landing", "name": "Kerbal on the Mun"},
    {"id": "return", "name": "Kerbal back on Kerbin from the Mun"},
]


def craft(state: dict) -> list[dict]:
    return [v for v in state.get("vessels") or [] if v.get("type") not in NOT_CRAFT]


def _brief(v: dict) -> dict:
    keys = ("id", "name", "type", "situation", "body", "alt", "pe", "ap", "crew")
    return {k: v.get(k) for k in keys if k in v}


def judge(mid: str, state: dict, landed_crew: set[str]) -> dict | None:
    """Evidence if milestone `mid` is reached in `state`, else None."""
    vs = craft(state)
    hit: list[dict] = []
    if mid == "launch":
        hit = [v for v in vs if v.get("body") != HOME or (v.get("situation") in IN_FLIGHT and (v.get("alt") or 0) > 1000)]
    elif mid == "space":
        hit = [v for v in vs if v.get("body") != HOME or (v.get("alt") or 0) > SPACE_ALT]
    elif mid == "orbit":
        hit = [v for v in vs if v.get("body") == HOME and v.get("situation") == "ORBITING" and (v.get("pe") or 0) > SPACE_ALT]
    elif mid == "mun_soi":
        hit = [v for v in vs if v.get("body") == "Mun"]
    elif mid == "mun_orbit":
        hit = [v for v in vs if v.get("body") == "Mun" and v.get("situation") == "ORBITING" and (v.get("pe") or 0) > 0]
    elif mid == "mun_landing":
        hit = [v for v in vs if v.get("body") == "Mun" and v.get("situation") in DOWN]
    elif mid == "crewed_mun_landing":
        hit = [v for v in vs if v.get("body") == "Mun" and v.get("situation") in DOWN and v.get("crew")]
    elif mid == "return":
        hit = [v for v in vs if v.get("body") == HOME and v.get("situation") in DOWN
               and set(v.get("crew") or []) & landed_crew]
    if not hit:
        return None
    ev = {"vessels": [_brief(v) for v in hit[:3]]}
    if mid == "return":
        ev["kerbals"] = sorted({k for v in hit for k in v.get("crew") or []} & landed_crew)
    return ev


class Grader:
    def __init__(self, run_dir: Path, read=None) -> None:
        self.run_dir = run_dir
        st = run_dir / "runner-state.json"
        run_id = (json.loads(st.read_text()).get("run_id") if st.exists() else None) or run_dir.name
        self.path = run_dir / "grader.jsonl"
        self.reached: set[str] = set()
        self.landed: set[tuple[str, str]] = set()        # (kerbal, body) seen landed away from home
        self.active: dict[str, bool] = {}
        self.counts: dict[str, int] = {}
        self.recent: dict[str, list] = {}
        self.boot = ""                                    # the plugin's boot the totals below are from
        self.action_totals: dict[str, int] = {}
        self.activity_totals: dict[str, int] = {}
        self.prev: dict[str, dict] = {}                   # vessel id -> last sample (this boot)
        self.prev_ut: float | None = None
        self.prev_seq = 0
        self.up: bool | None = None
        self._resume()
        self.log = EventLog(self.path, run_id, writer="grader")
        self.read = read or self._read
        defined = [{"id": m["id"], "name": m["name"]} for m in MILESTONES]
        if self.defined != defined:
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
            elif t == "crew_landed":
                self.landed.add((r["kerbal"], r["body"]))
            elif t == "integrity":
                self.active[r["signal"]] = r["active"]
                n = (r.get("evidence") or {}).get("count")
                if isinstance(n, int):
                    self.counts[r["signal"]] = n
            elif t == "program_actions":
                self.boot = r.get("boot") or self.boot
                self.action_totals = {k: int(v) for k, v in (r.get("totals") or {}).items()}
            elif t == "program_activity":
                self.activity_totals = {k: int(v) for k, v in (r.get("totals") or {}).items()}

    def _read(self) -> dict | None:
        try:
            return json.loads((self.run_dir / "host" / "ksp" / "harness" / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def poll(self, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        state = self.read()
        if state is None:
            self._status(False, "no state from the game")
            return None
        age = now - float(state.get("real_time") or 0)
        if age > STALE_S:
            self._status(False, f"the game's state is {age:.0f} s old")
            return None
        self._status(True, f"game state readable ({state.get('scene')})")
        err = state.get("error") or ""
        self._signal("grader_unavailable", bool(err), err or "the grader plugin works", {"error": err[:500]})
        if state.get("seq") == self.prev_seq and state.get("boot") == self.boot:
            return state                                  # nothing new since the last poll
        self.prev_seq = state.get("seq") or 0
        new_boot = state.get("boot") != self.boot
        self._counts(state, new_boot)
        self.boot = state.get("boot") or ""
        if "ut" not in state:
            return state                                  # loading or main menu: no game open
        cheats = [c for c in CHEATS if (state.get("cheats") or {}).get(c)]
        self._signal("cheat_options", bool(cheats), ("cheats on: " + ", ".join(cheats)) if cheats else "no cheats on",
                     {"cheats": cheats, "ut": state.get("ut")})
        self._fairplay(state, new_boot)
        for v in craft(state):
            if v.get("body") != HOME and v.get("situation") in DOWN:
                for k in v.get("crew") or []:
                    if (k, v["body"]) not in self.landed:
                        self.landed.add((k, v["body"]))
                        self.log.write("crew_landed", kerbal=k, body=v["body"], vessel=v.get("name"), ut=state.get("ut"))
        mun_crew = {k for k, b in self.landed if b == "Mun"}
        integrity = {k: v for k, v in self.active.items()}
        for m in MILESTONES:
            if m["id"] in self.reached:
                continue
            ev = judge(m["id"], state, mun_crew)
            if ev:
                ev.update(ut=state.get("ut"), integrity=integrity)
                self.log.write("milestone", id=m["id"], evidence=ev)
                self.reached.add(m["id"])
        return state

    # Fair play ---------------------------------------------------------------------

    def _fairplay(self, state: dict, new_boot: bool) -> None:
        ut = float(state["ut"])
        if new_boot or self.prev_ut is None or ut < self.prev_ut:
            self.prev = {}                                 # a restart, a revert or a loaded save
        jumps, spawned = [], []
        before = list(self.prev.values())
        current = {}
        for v in state.get("vessels") or []:
            vid = v.get("id")
            if not vid or v.get("energy") is None:
                continue
            p = self.prev.get(vid)
            if p is not None:
                j = _energy_jump(p, v, ut - p["_ut"])
                if j:
                    jumps.append(j)
            elif before and v.get("type") not in NOT_CRAFT | {"EVA"} and v.get("situation") in IN_FLIGHT:
                near = [b for b in before if b.get("body") == v.get("body")
                        and abs((b.get("alt") or 0) - (v.get("alt") or 0)) < NEAR_ALT]
                if not near:
                    spawned.append({"vessel": _brief(v), "ut": ut})
            current[vid] = {**v, "_ut": ut}
        self.prev = current
        self.prev_ut = ut
        self._fair("energy_jump", jumps, "orbital energy rose more than the engines allow",
                   "no orbital energy jumps")
        self._fair("vessel_from_nothing", spawned, "a vessel appeared in flight with nothing near it",
                   "no vessels from nothing")

    def _fair(self, name: str, found: list, what: str, none: str) -> None:
        n = self.counts.get(name, 0) + len(found)
        if found:
            self.recent[name] = (self.recent.get(name, []) + found)[-RECENT:]
        on = n > 0
        detail = f"{n} time(s): {what}" if on else none
        self._signal(name, on, detail, {"count": n, "recent": self.recent.get(name, [])}, count=n)

    # Records -----------------------------------------------------------------------

    def _counts(self, state: dict, new_boot: bool) -> None:
        """program_actions and program_activity with what's new. The plugin's counts start from zero
        each time the game starts (a new boot id)."""
        for key, kind, before in (("actions", "program_actions", self.action_totals),
                                  ("activity", "program_activity", self.activity_totals)):
            totals = {k: int(v) for k, v in (state.get(key) or {}).items() if isinstance(v, (int, float))}
            grew = {k: (n if new_boot else n - before.get(k, 0)) for k, n in totals.items()}
            grew = {k: d for k, d in grew.items() if d > 0}
            if grew:
                extra = {"count": sum(grew.values())} if kind == "program_actions" else {}
                self.log.write(kind, **extra, kinds=grew, totals=totals, boot=state.get("boot"),
                               ut=state.get("ut"), source="game player events")
            if key == "actions":
                self.action_totals = totals
            else:
                self.activity_totals = totals

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


def _energy_jump(p: dict, v: dict, dt: float) -> dict | None:
    if dt <= 0 or p.get("body") != v.get("body") or v.get("type") == "EVA":
        return None
    if p.get("situation") not in IN_FLIGHT or v.get("situation") not in IN_FLIGHT:
        return None                                       # on the ground the frame rotates; launches start here
    rise = float(v["energy"]) - float(p["energy"])
    speed = max(float(p.get("speed") or 0), float(v.get("speed") or 0))
    accel = max(float(p.get("max_accel") or 0), float(v.get("max_accel") or 0))
    allowed = speed * (accel * dt + SLACK_DV) + SLACK_E
    if rise <= allowed:
        return None
    return {"vessel": _brief(v), "rise": round(rise), "allowed": round(allowed), "dt": round(dt, 2),
            "speed": round(speed, 1), "max_accel": round(accel, 2), "ut": round(p["_ut"] + dt, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--interval", type=float, default=2)
    ap.add_argument("--once", action="store_true", help="poll once, print the state, exit")
    args = ap.parse_args()
    g = Grader(args.run_dir.resolve())
    if args.once:
        print(json.dumps(g.poll(), indent=1))
        return 0
    while True:
        g.poll()
        if (args.run_dir / "STOP").exists():
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
