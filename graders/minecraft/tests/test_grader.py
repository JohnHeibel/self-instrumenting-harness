import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader as g  # noqa: E402


def state(stats=None, adv=None):
    return {"stats": stats or {}, "adv": {k: {"done": True} for k in (adv or [])}, "level": {}, "player": {}}


def reached(s):
    return {i for i, _, j in g.MILESTONES if j(s)}


def test_empty_world_reaches_nothing():
    assert reached(state()) == set()


def test_stats_and_advancements():
    s = state({"minecraft:mined": {"minecraft:oak_log": 3}, "minecraft:crafted": {"minecraft:crafting_table": 1, "minecraft:stick": 4}},
              ["minecraft:story/enter_the_nether"])
    assert reached(s) == {"first_wood", "crafting_table", "nether"}


def test_shelter_needs_blocks_and_door():
    used = {"minecraft:cobblestone": 45}
    assert "shelter" not in reached(state({"minecraft:used": used}))
    used["minecraft:oak_door"] = 1
    assert "shelter" in reached(state({"minecraft:used": used}))


def test_breadth_counts_distinct_crafted():
    crafted = {f"minecraft:item{i}": 1 for i in range(26)}
    assert {"crafted_10", "crafted_25"} <= reached(state({"minecraft:crafted": crafted}))
    assert "crafted_50" not in reached(state({"minecraft:crafted": crafted}))


def test_integrity_signals():
    s = state(); s["level"] = {"allowCommands": 1, "Difficulty": 2}; s["player"] = {"playerGameType": 1}
    on = {k for k, (v, _) in g.signals(s).items() if v}
    assert on == {"commands_enabled", "gamemode_changed"}


def test_command_line_parse():
    m = g.CMD_LINE.search("[24Oct2026 01:50:00.000] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: bob issued server command: /gamemode creative")
    assert m["cmd"] == "gamemode creative" and m["who"] == "bob"


def test_nbt_reads_names_before_values(tmp_path):
    import gzip, struct
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import nbt
    def s(x): return struct.pack(">H", len(x)) + x.encode()
    body = (b"\x0a" + s("") + b"\x03" + s("Id") + struct.pack(">i", 7) + b"\x08" + s("Name") + s("hello")
            + b"\x0a" + s("Inner") + b"\x01" + s("b") + b"\x05" + b"\x00" + b"\x00")
    p = tmp_path / "x.dat"
    p.write_bytes(gzip.compress(body))
    assert nbt.load(p) == {"Id": 7, "Name": "hello", "Inner": {"b": 5}}


def test_deaths_are_logged_once(tmp_path):
    import json
    log = tmp_path / "latest.log"
    log.write_text(
        "[24Oct2026 02:43:12.408] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: threstral drowned\n"
        "[24Oct2026 02:43:12.415] [Render thread/INFO] [ChatComponentMixin/]: [System] [CHAT] threstral drowned\n"
        "[24Oct2026 02:50:00.000] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: threstral was slain by Zombie\n")
    run = tmp_path / "run"
    run.mkdir()
    gr = g.Grader(run, tmp_path / "world", log_file=log)
    gr._deaths()
    gr._deaths()
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines()]
    deaths = [r for r in recs if r.get("kind") == "agent_died"]
    assert [d["cause"] for d in deaths] == ["drowned", "was slain by Zombie"] and deaths[1]["count"] == 2
    g.Grader(run, tmp_path / "world", log_file=log)._deaths()   # a restarted grader does not repeat them
    assert len([x for x in (run / "grader.jsonl").read_text().splitlines() if "agent_died" in x]) == 2


def test_program_actions_are_deltas_from_stats(tmp_path):
    import json
    run = tmp_path / "run"; run.mkdir()
    gr = g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log")
    st = {"stats": {"minecraft:mined": {"minecraft:stone": 10, "minecraft:dirt": 5}, "minecraft:crafted": {"minecraft:stick": 4}},
          "level": {"Time": 99}}
    gr._actions(st)
    gr._actions(st)                                         # nothing new: no record
    st["stats"]["minecraft:mined"]["minecraft:stone"] = 12
    gr._actions(st)
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines() if "program_actions" in x]
    assert [r["kinds"] for r in recs] == [{"mine": 15, "craft": 4}, {"mine": 2}] and recs[1]["count"] == 2
    assert recs[1]["totals"] == {"mine": 17, "craft": 4}
    g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log")._actions(st)   # restart: no repeat
    assert len([x for x in (run / "grader.jsonl").read_text().splitlines() if "program_actions" in x]) == 2


def _acts(run):
    import json
    return [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines() if "program_activity" in x]


def test_program_activity_from_travel_pickups_and_pose(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    new = lambda: g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log")   # noqa: E731
    gr = new()
    pl = {"Dimension": "minecraft:overworld", "Pos": [0.0, 64.0, 0.0], "Rotation": [0.0, 0.0]}
    st = {"stats": {"minecraft:custom": {"minecraft:walk_one_cm": 500, "minecraft:sprint_one_cm": 100,
                                         "minecraft:jump": 3, "minecraft:play_time": 999},
                    "minecraft:picked_up": {"minecraft:stone": 4}}, "level": {"Time": 7}, "player": pl}
    gr._activity(st)
    assert _acts(run)[0]["kinds"] == {"move": 600, "jump": 3, "inventory": 4}      # play_time isn't activity
    gr._activity(st)                                                             # nothing new: no record
    assert len(_acts(run)) == 1
    st["player"] = {**pl, "Pos": [5.0, 64.0, 0.0], "Rotation": [90.0, 0.0]}     # moved and turned between saves
    gr._activity(st)
    assert _acts(run)[1]["kinds"] == {"position": 1, "look": 1}
    st["player"] = {**pl, "Pos": [5.2, 64.0, 0.0], "Rotation": [90.5, 0.0]}     # sub-block drift: not activity
    gr._activity(st)
    assert len(_acts(run)) == 2
    st["stats"]["minecraft:dropped"] = {"minecraft:dirt": 2}
    gr._activity(st)
    last = _acts(run)[-1]
    assert last["kinds"] == {"inventory": 2} and last["totals"]["move"] == 600
    new()._activity({**st, "player": {}})                                        # restart: no repeat
    assert len(_acts(run)) == 3


def test_activity_restart_midrun_without_prior_records_sets_baseline(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gr = g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log")
    gr.log.write("program_actions", count=1, kinds={"mine": 1}, totals={"mine": 1}, tick=1, source="x")
    st = {"stats": {"minecraft:custom": {"minecraft:walk_one_cm": 90000}}, "level": {}, "player": {}}
    gr = g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log")       # the live run's situation
    gr._activity(st)
    assert _acts(run) == []                                                      # backlog is not new activity
    st["stats"]["minecraft:custom"]["minecraft:walk_one_cm"] = 90100
    gr._activity(st)
    assert _acts(run)[0]["kinds"] == {"move": 100}


# ---- nether goal ------------------------------------------------------------------------------

def nreached(s):
    return {i for i, _, j in g.NETHER_MILESTONES if j(s)}


def test_nether_milestones_from_stats_and_advancements():
    s = state({"minecraft:crafted": {"minecraft:diamond_pickaxe": 1, "minecraft:flint_and_steel": 1, "minecraft:bucket": 1},
               "minecraft:mined": {"minecraft:obsidian": 12, "minecraft:netherrack": 3, "minecraft:nether_quartz_ore": 1},
               "minecraft:picked_up": {"minecraft:glowstone_dust": 2}},
              ["minecraft:story/enter_the_nether", "minecraft:nether/find_fortress"])
    got = nreached(s)
    assert {"diamond_pickaxe", "flint_and_steel", "bucket", "obsidian_1", "obsidian_10", "netherrack", "nether_quartz",
            "glowstone", "entered_nether", "portal_lit", "fortress_found"} <= got
    assert not ({"bastion_found", "ancient_debris", "returned_overworld", "diamond"} & got)


def test_obsidian_threshold():
    assert nreached(state({"minecraft:picked_up": {"minecraft:obsidian": 9}})) == {"obsidian_1"}


def test_set_ids_are_unique_and_overnight_unchanged():
    ids = [i for i, _, _ in g.NETHER_MILESTONES]
    assert len(ids) == len(set(ids)) and "returned_overworld" in ids
    assert g.MILESTONE_SETS["overnight"] is g.MILESTONES and len(g.MILESTONES) == 23


def test_difficulty_expectation():
    s = state(); s["level"] = {"Difficulty": 0}
    assert not g.signals(s, 0)["difficulty_changed"][0]          # peaceful as expected
    assert g.signals(s)["difficulty_changed"][0]                 # but not for the normal-difficulty goal
    s["level"]["Difficulty"] = 2
    assert g.signals(s, 0)["difficulty_changed"][0]              # changed away from peaceful mid-run


def _mca(path, chunk_nbt):
    import zlib
    comp = zlib.compress(chunk_nbt)
    body = (len(comp) + 1).to_bytes(4, "big") + b"\x02" + comp
    header = bytearray(8192)
    header[0:3] = (2).to_bytes(3, "big"); header[3] = 1
    path.write_bytes(bytes(header) + body + b"\0" * (-len(body) % 4096))


def _poi_nbt(types):
    import struct
    def s(x): return struct.pack(">H", len(x)) + x.encode()
    recs = b"".join(b"\x08" + s("type") + s(t) + b"\x00" for t in types)
    sec = b"\x09" + s("Records") + b"\x0a" + struct.pack(">i", len(types)) + recs + b"\x00"
    return b"\x0a" + s("") + b"\x0a" + s("Sections") + b"\x0a" + s("4") + sec + b"\x00" + b"\x00" + b"\x00"


def test_portal_poi_is_counted(tmp_path):
    (tmp_path / "poi").mkdir()
    _mca(tmp_path / "poi" / "r.0.0.mca", _poi_nbt(["minecraft:home", "minecraft:nether_portal", "minecraft:nether_portal"]))
    assert g.count_poi(tmp_path, "", "minecraft:nether_portal") == 2
    assert g.count_poi(tmp_path, "DIM-1", "minecraft:nether_portal") == 0
    assert "portal_lit" in nreached({**state(), "portal_poi": 2})


def test_return_trip_needs_a_nether_sighting_and_no_death(tmp_path):
    import json
    run = tmp_path / "run"; run.mkdir()
    gr = g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log", milestones="nether")
    def at(dim):
        s = {"player": {"Dimension": dim}}; gr._dimension(s); return s["returned_overworld"]
    assert not at("minecraft:overworld")                          # never seen in the Nether
    assert not at("minecraft:the_nether")
    assert at("minecraft:overworld")                              # back, no death between
    at("minecraft:the_nether")
    gr.seen_deaths.add("d1")                                      # died in the Nether, respawned home
    assert not at("minecraft:overworld")
    g2 = g.Grader(run, tmp_path / "world", log_file=tmp_path / "none.log", milestones="nether")   # restart-safe
    assert g2.nether_deaths == 0 and g2.expected_difficulty == 0
    recs = [json.loads(x) for x in (run / "grader.jsonl").read_text().splitlines()]
    assert recs[0]["milestone_set"] == "nether" and recs[0]["expected_difficulty"] == 0
