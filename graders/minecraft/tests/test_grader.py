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
