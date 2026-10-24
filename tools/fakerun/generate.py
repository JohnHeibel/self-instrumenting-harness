"""Generate a believable fake run in the event-log format (docs/interfaces/event-log.md).

For building and testing the UI without a real run. Deterministic per seed. The
scenario (milestones, hooks, narration, thumbnails) is data further down in this file;
the generator itself knows nothing about any particular host. Scenarios: factorio
(a game) and spreadsheet (a desktop spreadsheet app, not a game).

    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-factorio [--scenario factorio] [--seed 7] [--hours 14]
    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-spreadsheet --scenario spreadsheet
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Writer:
    def __init__(self, path: Path, run_id: str, writer: str) -> None:
        self.f = path.open("w", encoding="utf-8")
        self.run_id, self.writer, self.seq = run_id, writer, 0

    def __call__(self, t: datetime, type_: str, **fields) -> None:
        self.seq += 1
        rec = {"v": 1, "ts": t.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "run_id": self.run_id, "writer": self.writer, "seq": self.seq, "type": type_, **fields}
        self.f.write(json.dumps(rec) + "\n")


def generate(out: Path, seed: int, hours: float, scenario: dict) -> None:
    rng = random.Random(seed)
    if out.exists():
        shutil.rmtree(out)
    (out / "shots").mkdir(parents=True)
    run_id = out.name
    tools, runner, grader = (Writer(out / f, run_id, w) for f, w in
                             [("events.jsonl", "toolserver"), ("runner.jsonl", "runner"), ("grader.jsonl", "grader")])
    t0 = datetime(*scenario.get("start", (2026, 10, 24, 4, 0)), tzinfo=timezone.utc)
    end = t0 + timedelta(hours=hours)
    at = lambda frac: t0 + timedelta(seconds=frac * hours * 3600)

    runner(t0, "run_started", agent=scenario.get("agent", "codex"), model=scenario.get("model", "gpt-6.1-sol"), host=scenario["host"], goal=scenario["goal"])
    tools(t0, "server_started", host="127.0.0.1", port=8765)
    grader(t0, "milestones_defined", milestones=[{"id": m["id"], "name": m["name"]} for m in scenario["milestones"]])

    # Timed one-off events, merged with the steady stream of tool calls below.
    timeline: list[tuple[datetime, callable]] = []
    for m in scenario["milestones"]:
        timeline.append((at(m["at"]), lambda t, m=m: grader(t, "milestone", id=m["id"], evidence=m["evidence"])))
    for h in scenario["hooks"]:
        timeline.append((at(h["at"]), lambda t, h=h: _load_hook(tools, t, h, version=1)))
    brk = scenario["break"]
    timeline.append((at(brk["at"]), lambda t: runner(t, "marker", label=brk["label"], kind="planted_break")))
    timeline.append((at(brk["at"]) + timedelta(minutes=4), lambda t: tools(
        t, "hook_status", hook=brk["hook"], status="broken", detail=brk["detail"])))
    timeline.append((at(brk["at"]) + timedelta(minutes=19), lambda t: (
        _load_hook(tools, t, {**_hook(scenario, brk["hook"]), "reason": brk["repair_reason"],
                              "source": brk["repaired_source"]}, version=2),
        tools(t, "hook_status", hook=brk["hook"], status="repaired", detail=brk.get("repaired_detail", "health check passes on the new game version")))))
    for frac, text, serves in scenario["goals"]:
        timeline.append((at(frac), lambda t, x=text, s=serves: runner(t, "goal", text=x, serves=s)))
    for frac, title, text in scenario["reasoning"]:
        timeline.append((at(frac), lambda t, a=title, b=text: runner(t, "reasoning", title=a, text=b)))
    for frac, text in scenario["messages"]:
        timeline.append((at(frac), lambda t, x=text: runner(t, "agent_message", text=x)))
    for frac in scenario["compactions"]:
        timeline.append((at(frac), lambda t: runner(t, "agent_state", state="compacting", text="Compacting memory")))
        timeline.append((at(frac) + timedelta(minutes=6), lambda t: runner(t, "agent_state", state="thinking", text="")))
    timeline.sort(key=lambda x: x[0])

    loaded: list[dict] = []
    repaired_at = at(brk["at"]) + timedelta(minutes=19)
    turn, total_tokens, shot_n = 0, 0, 0
    next_usage = t0 + timedelta(minutes=5)
    t = t0 + timedelta(seconds=20)
    runner(t, "turn_started", turn=1)
    runner(t, "agent_state", state="thinking", text="")
    while t < end:
        while timeline and timeline[0][0] <= t:
            ts, fn = timeline.pop(0)
            fn(ts)
            loaded = [h for h in scenario["hooks"] if at(h["at"]) <= ts]
        # The more hooks exist, the more observations go through them instead of screenshots.
        hook_share = min(0.9, 0.1 * len(loaded) + rng.uniform(-0.05, 0.05))
        r = rng.random()
        if r < 0.38:  # observe
            if loaded and rng.random() < hook_share:
                h = rng.choice(loaded)
                tools(t, "tool_call", tool=h["name"], channel="hook", args=h.get("args", {}), ok=True, error=None,
                      duration_ms=rng.randint(8, 120), result_kind="json", result_bytes=rng.randint(300, 4000),
                      via="direct", hook={"name": h["name"], "version": 2 if h["name"] == brk["hook"] and t > repaired_at else 1},
                      summary=rng.choice(h["summaries"]))
            else:
                shot_n += 1
                artifact = None
                if shot_n % 6 == 1:
                    artifact = f"shots/{shot_n:06d}.jpg"
                    scenario["shot"](out / artifact, (t - t0).total_seconds() / (hours * 3600), rng)
                tools(t, "tool_call", tool="screenshot", channel="screenshot", args={}, ok=True, error=None,
                      duration_ms=rng.randint(40, 90), result_kind="image", result_bytes=rng.randint(180_000, 260_000),
                      source="window", captured_size=[2560, 1440], image_size=[1280, 720],
                      summary=scenario.get("shot_summary", "screenshot of the game window"), **({"artifact": artifact} if artifact else {}))
        elif r < 0.9:  # act
            act = rng.choice(scenario["actions"])
            ok = rng.random() > 0.04
            tools(t, "tool_call", tool=act[0], channel="input", args=act[1], ok=ok,
                  error=None if ok else scenario.get("action_error", "TimeoutError: game did not respond"), duration_ms=rng.randint(30, 400),
                  result_kind="text", result_bytes=40, summary=act[2])
        else:
            tools(t, "tool_call", tool="read_docs", channel="docs", args={"query": rng.choice(scenario["doc_queries"])},
                  ok=True, error=None, duration_ms=rng.randint(20, 200), result_kind="text",
                  result_bytes=rng.randint(2000, 9000), summary="read the API docs")
        if t >= next_usage:
            # Cost per step falls as the library grows: fewer screenshots, shorter reasoning.
            per = int(rng.uniform(0.8, 1.2) * 180_000 / (1 + 0.18 * len(loaded)))
            total_tokens += per
            runner(t, "usage", input_tokens=per, cached_input_tokens=int(per * 0.7),
                   output_tokens=per // 30, reasoning_output_tokens=per // 15, total_tokens=total_tokens)
            next_usage = t + timedelta(minutes=5)
            if rng.random() < 0.06:  # a turn ended; the runner sends "continue"
                runner(t, "turn_completed", turn=turn + 1)
                turn += 1
                runner(t + timedelta(seconds=2), "turn_started", turn=turn + 1)
        t += timedelta(seconds=rng.uniform(4, 22))
    runner(end, "turn_completed", turn=turn + 1)
    runner(end, "agent_state", state="ended", text="Goal reached")
    runner(end, "run_ended", reason="goal reached")


def _hook(scenario: dict, name: str) -> dict:
    return next(h for h in scenario["hooks"] if h["name"] == name)


def _load_hook(tools: Writer, t: datetime, h: dict, version: int) -> None:
    ext = {"lua": "lua", "python": "py", "javascript": "js"}[h["language"]]
    tools(t, "tool_call", tool="load_extension", channel="extension", args={"name": h["name"]}, ok=True, error=None,
          duration_ms=900 if h["kind"] == "in_host" else 120, result_kind="text", result_bytes=80,
          summary=f"loaded {h['name']} v{version}")
    tools(t, "hook_loaded", hook=h["name"], version=version, reason=h["reason"], language=h["language"],
          kind=h["kind"], depends_on=h.get("depends_on", []),
          files=[{"path": f"{h['name']}/{'control' if ext == 'lua' else h['name']}.{ext}", "content": h["source"]}])


def _shot(path: Path, progress: float, rng: random.Random) -> None:
    """A stand-in game frame that fills up with structures as the run progresses."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (58, 52, 40))
    d = ImageDraw.Draw(img)
    for x in range(0, 640, 32):
        d.line([(x, 0), (x, 360)], fill=(64, 58, 45))
    for y in range(0, 360, 32):
        d.line([(0, y), (640, y)], fill=(64, 58, 45))
    local = random.Random(int(progress * 50))  # structures stay put between frames
    for _ in range(int(6 + progress * 70)):
        x, y = local.randrange(0, 600), local.randrange(0, 330)
        kind = local.random()
        if kind < 0.45:
            d.rectangle([x, y, x + 28, y + 28], fill=(120, 110, 95), outline=(40, 36, 30))
        elif kind < 0.75:
            d.line([(x, y), (x + local.randrange(40, 160), y)], fill=(200, 170, 60), width=5)
        else:
            d.ellipse([x, y, x + 22, y + 22], fill=(150, 70, 50))
    px, py = 320 + rng.randint(-40, 40), 180 + rng.randint(-30, 30)
    d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(240, 240, 240))
    img.save(path, "JPEG", quality=70)


FACTORIO = {
    "host": "factorio",
    "shot": _shot,
    "goal": "Reach blue science: produce the first chemical science pack",
    "milestones": [
        {"id": "first_assembler", "name": "First assembler", "at": 0.05, "evidence": "1 assembling-machine-1 placed"},
        {"id": "red_auto", "name": "Red science automated", "at": 0.16, "evidence": "automation-science-pack produced by assemblers: 40"},
        {"id": "green_auto", "name": "Green science automated", "at": 0.33, "evidence": "logistic-science-pack produced: 25"},
        {"id": "oil", "name": "Oil processing", "at": 0.52, "evidence": "petroleum-gas produced: 1,200"},
        {"id": "plastic", "name": "Plastic", "at": 0.66, "evidence": "plastic-bar produced: 60"},
        {"id": "adv_circuit", "name": "Advanced circuits", "at": 0.78, "evidence": "advanced-circuit produced: 30"},
        {"id": "engine", "name": "Engine units", "at": 0.86, "evidence": "engine-unit produced: 12"},
        {"id": "blue", "name": "Blue science", "at": 0.97, "evidence": "chemical-science-pack produced: 1"},
    ],
    "hooks": [
        {"name": "get_player_state", "at": 0.02, "language": "lua", "kind": "in_host",
         "reason": "Inventory and position aren't readable reliably from the screen; I need exact counts.",
         "summaries": ["player at (12,-40), 48 iron plates", "inventory: 112 coal, 30 stone"],
         "source": 'remote.add_interface("agent", {\n  player_state = function()\n    local p = game.players[1]\n    rcon.print(helpers.table_to_json({pos = p.position, inv = p.get_main_inventory().get_contents()}))\n  end\n})\n'},
        {"name": "get_entities_near", "at": 0.07, "language": "lua", "kind": "in_host",
         "reason": "Finding placed machines by eye is slow and error-prone. Query entities around a point.",
         "args": {"radius": 30}, "summaries": ["14 entities within 30 tiles", "3 furnaces, 2 drills nearby"],
         "source": 'function entities_near(x, y, r)\n  local s = game.surfaces[1]\n  local out = {}\n  for _, e in pairs(s.find_entities_filtered{position = {x, y}, radius = r}) do\n    out[#out + 1] = {name = e.name, pos = e.position, status = e.status}\n  end\n  rcon.print(helpers.table_to_json(out))\nend\n'},
        {"name": "get_assembler_status", "at": 0.12, "language": "lua", "kind": "in_host", "depends_on": ["get_entities_near"],
         "reason": "I can't tell from pixels whether an assembler is working or starved. The entity status field says.",
         "summaries": ["6 of 8 assemblers working, 2 missing input", "all red-science assemblers working"],
         "source": 'function assembler_status(area)\n  local out = {}\n  for _, e in pairs(game.surfaces[1].find_entities_filtered{type = "assembling-machine", area = area}) do\n    out[#out + 1] = {recipe = e.get_recipe() and e.get_recipe().name, status = e.status}\n  end\n  rcon.print(helpers.table_to_json(out))\nend\n'},
        {"name": "find_starved_lines", "at": 0.2, "language": "python", "kind": "helper", "depends_on": ["get_assembler_status"],
         "reason": "Groups starved assemblers by missing ingredient so I fix the cause, not each machine.",
         "summaries": ["starved: iron-gear-wheel (3 machines)", "no starved lines"],
         "source": 'def find_starved_lines(status):\n    starved = {}\n    for a in status:\n        if a["status"] == "item_ingredient_shortage":\n            starved.setdefault(a["recipe"], []).append(a)\n    return {k: len(v) for k, v in starved.items()}\n'},
        {"name": "get_production_stats", "at": 0.27, "language": "lua", "kind": "in_host",
         "reason": "Throughput per minute tells me where the bottleneck is; the screen only shows snapshots.",
         "summaries": ["iron plates 92/min, gears 31/min", "green science 4.1/min"],
         "source": 'function production(item)\n  local st = game.forces.player.item_production_statistics\n  rcon.print(helpers.table_to_json({item = item, per_min = st.get_flow_count{name = item, input = true, precision_index = defines.flow_precision_index.one_minute}}))\nend\n'},
        {"name": "read_fluid_box", "at": 0.48, "language": "lua", "kind": "in_host",
         "reason": "Pipes look identical whether full or empty. I need fluid amounts to debug the refinery.",
         "summaries": ["refinery input: crude 74/100", "pipe segment 3 empty"],
         "source": 'function fluid_box(unit)\n  local e = game.get_entity_by_unit_number(unit)\n  local out = {}\n  for i = 1, #e.fluidbox do out[i] = e.fluidbox[i] end\n  rcon.print(helpers.table_to_json(out))\nend\n'},
        {"name": "get_oil_flow", "at": 0.55, "language": "python", "kind": "helper", "depends_on": ["read_fluid_box", "get_production_stats"],
         "reason": "Combines fluid boxes and production stats into one view of the oil chain.",
         "summaries": ["petroleum 62/min, heavy oil backing up", "oil chain balanced"],
         "source": 'def get_oil_flow(boxes, stats):\n    return {"petroleum_per_min": stats["petroleum-gas"],\n            "backed_up": [b for b in boxes if b["amount"] > 0.95 * b["capacity"]]}\n'},
        {"name": "plan_belt_route", "at": 0.7, "language": "python", "kind": "helper", "depends_on": ["get_entities_near"],
         "reason": "Dragging belts by hand kept crossing other lines. Plan a route around existing entities first.",
         "summaries": ["route planned: 34 tiles, 2 undergrounds", "no clear route; suggests detour"],
         "source": 'def plan_belt_route(grid, start, goal):\n    from collections import deque\n    q, seen = deque([(start, [])]), {start}\n    while q:\n        p, path = q.popleft()\n        if p == goal:\n            return path\n        for d in ((1,0),(-1,0),(0,1),(0,-1)):\n            n = (p[0]+d[0], p[1]+d[1])\n            if n not in seen and not grid.blocked(n):\n                seen.add(n); q.append((n, path + [n]))\n'},
    ],
    "break": {"at": 0.6, "hook": "read_fluid_box", "label": "Game updated mid-run (planted change)",
              "detail": "error: LuaEntity has no member 'fluidbox[i]' with that signature after the update",
              "repair_reason": "The update changed how fluid box entries are read. Rewrote it with get_fluid_segment_contents.",
              "repaired_source": 'function fluid_box(unit)\n  local e = game.get_entity_by_unit_number(unit)\n  local out = {}\n  for i = 1, #e.fluidbox do\n    out[i] = {contents = e.fluidbox.get_fluid_segment_contents(i), capacity = e.fluidbox.get_capacity(i)}\n  end\n  rcon.print(helpers.table_to_json(out))\nend\n'},
    "goals": [
        (0.01, "Get basic iron and copper smelting running", "every later step needs plates"),
        (0.1, "Automate red science", "research needs a steady pack supply"),
        (0.25, "Automate green science", "unlocks oil processing research"),
        (0.45, "Build oil processing", "plastic and advanced circuits need petroleum"),
        (0.6, "Fix the refinery readout after the update", "can't debug fluids blind"),
        (0.7, "Make plastic and advanced circuits", "inputs to blue science"),
        (0.84, "Make engine units", "the last input to blue science"),
        (0.92, "Assemble the first chemical science pack", "the goal"),
    ],
    "reasoning": [
        (0.03, "Reading inventory exactly", "Counting items from the screenshot keeps going wrong at stack boundaries. A tiny mod that prints the inventory as JSON will be exact and cheaper than another screenshot."),
        (0.13, "Why are two assemblers idle?", "They look fine on screen. The status field will say whether they're starved or output-blocked, so I'll write a hook for it instead of guessing."),
        (0.34, "Throughput, not snapshots", "I keep checking the same belts. Production stats per minute show the bottleneck directly: gears at 31/min can't feed green science."),
        (0.61, "Refinery hook broke after the update", "The game updated and read_fluid_box now errors. The API for fluid boxes changed; I'll read the new docs and rewrite it rather than fall back to screenshots."),
        (0.79, "Fewer screenshots now", "Most of what I need comes from hooks. I only screenshot to place things now."),
    ],
    "messages": [
        (0.16, "Red science is automated: 8 assemblers fed from one iron line."),
        (0.62, "read_fluid_box broke after the game update. I've rewritten it against the new API, and the health check passes again."),
        (0.97, "First chemical science pack produced. The goal is reached."),
    ],
    "compactions": [0.3, 0.58, 0.83],
    "actions": [
        ("click", {"x": 412, "y": 230}, "clicked an assembler"), ("drag", {"from": [412, 230], "to": [520, 230]}, "dragged a belt"),
        ("key", {"key": "e"}, "opened the inventory"), ("click", {"x": 640, "y": 360}, "placed an inserter"),
        ("type", {"text": "gear"}, "searched the crafting menu"), ("drag", {"from": [200, 300], "to": [200, 120]}, "laid a pipe"),
    ],
    "doc_queries": ["LuaEntity.status", "fluidbox", "item_production_statistics", "find_entities_filtered"],
}


# ---------------------------------------------------------------------------
# Scenario "spreadsheet": an agent in a desktop spreadsheet app, reconciling and
# cleaning a large workbook into a finished report. Not a game.
# ---------------------------------------------------------------------------

def _sheet_shot(path: Path, progress: float, rng: random.Random) -> None:
    """A stand-in spreadsheet frame: a grid whose rows get cleaned and filled as the session progresses."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 640, 22], fill=(33, 115, 70))
    d.text((8, 6), "Q3 Ledger Reconciliation.xlsx", fill=(255, 255, 255))
    d.rectangle([0, 22, 640, 42], fill=(240, 240, 240))
    d.text((8, 28), "fx", fill=(90, 90, 90))
    cols = [60, 180, 300, 400, 500, 640]
    for i, name in enumerate("ABCDE"):
        d.rectangle([cols[i], 44, cols[i + 1], 62], fill=(232, 232, 232), outline=(190, 190, 190))
        d.text((cols[i] + 50, 49), name, fill=(60, 60, 60))
    local = random.Random(int(progress * 40))
    for r in range(15):
        y = 62 + r * 20
        d.rectangle([0, y, 60, y + 20], fill=(232, 232, 232), outline=(190, 190, 190))
        d.text((20, y + 5), str(r + 1), fill=(60, 60, 60))
        clean = local.random() < 0.15 + progress * 0.85
        for c in range(5):
            fill = (255, 255, 255) if clean else (255, 226, 226) if local.random() < 0.4 else (255, 255, 255)
            d.rectangle([cols[c], y, cols[c + 1], y + 20], fill=fill, outline=(215, 215, 215))
            if c == 0:
                d.text((cols[c] + 6, y + 5), f"INV-{1000 + r * 7}", fill=(30, 30, 30))
            elif c == 4:
                d.text((cols[c] + 6, y + 5), "OK" if clean else "#N/A", fill=(20, 110, 50) if clean else (190, 40, 40))
            else:
                d.rectangle([cols[c] + 6, y + 7, cols[c] + 6 + local.randrange(30, 80), y + 13], fill=(150, 150, 150))
    d.rectangle([0, 346, 640, 360], fill=(240, 240, 240))
    d.text((8, 348), f"Ready    Unmatched: {max(0, int(1840 * (1 - progress) ** 2) + rng.randint(0, 3))}", fill=(60, 60, 60))
    img.save(path, "JPEG", quality=70)


_SHEET_LOOKUP_V1 = (
    "function match_rows(sheet, keyCol, otherSheet) {\n"
    "  const idx = new Map(otherSheet.getRange('A:A').getValues().map((r, i) => [r[0], i + 1]));\n"
    "  const out = [];\n"
    "  sheet.getRange(keyCol).getValues().forEach((r, i) => out.push({row: i + 1, match: idx.get(r[0]) || null}));\n"
    "  return JSON.stringify(out);\n"
    "}\n")
_SHEET_LOOKUP_V2 = (
    "// The app update replaced Range.getValues() with Range.values() and made it async.\n"
    "async function match_rows(sheet, keyCol, otherSheet) {\n"
    "  const idx = new Map((await otherSheet.range('A:A').values()).map((r, i) => [r[0], i + 1]));\n"
    "  const out = [];\n"
    "  (await sheet.range(keyCol).values()).forEach((r, i) => out.push({row: i + 1, match: idx.get(r[0]) || null}));\n"
    "  return JSON.stringify(out);\n"
    "}\n")

SPREADSHEET = {
    "host": "spreadsheet-app",
    "agent": "claude-code",
    "model": "claude-sonnet-5-5",
    "start": (2026, 10, 25, 6, 0),
    "shot": _sheet_shot,
    "shot_summary": "screenshot of the spreadsheet window",
    "action_error": "TimeoutError: the app did not respond",
    "goal": "Reconcile the bank export against the ledger and produce a clean, formatted summary report",
    "milestones": [
        {"id": "read_cells", "name": "Read cell ranges via hook", "at": 0.03, "evidence": "Sheet 'Ledger' A1:F2000 read through a hook: 1,987 data rows"},
        {"id": "dupes_removed", "name": "Duplicates removed", "at": 0.17, "evidence": "142 duplicate invoice rows removed; row count 1,845"},
        {"id": "types_fixed", "name": "Dates and amounts normalized", "at": 0.30, "evidence": "0 text-formatted dates and 0 text-formatted amounts remain in Ledger"},
        {"id": "matched_90", "name": "90% of rows reconciled", "at": 0.50, "evidence": "1,662 of 1,845 ledger rows matched to the bank export"},
        {"id": "pivot", "name": "Summary pivot built", "at": 0.74, "evidence": "Pivot 'By Vendor' totals equal Ledger total: 1,284,310.55"},
        {"id": "report_done", "name": "Report formatted and exported", "at": 0.98, "evidence": "Report.xlsx saved with 3 sheets; 0 formula errors"},
    ],
    "hooks": [
        {"name": "read_range", "at": 0.02, "language": "javascript", "kind": "in_host",
         "reason": "Reading numbers off screenshots of a grid is slow and I misread digits. The app has a macro console; read the cells directly.",
         "args": {"sheet": "Ledger", "range": "A1:F50"},
         "summaries": ["Ledger A1:F50: 49 rows, 6 columns", "Bank A1:D50: 49 rows, 4 columns", "selected range: 120 cells"],
         "source": "function read_range(sheet, range) {\n  return JSON.stringify(app.workbook.sheet(sheet).getRange(range).getValues());\n}\n"},
        {"name": "find_duplicates", "at": 0.10, "language": "python", "kind": "helper", "depends_on": ["read_range"],
         "reason": "Spotting duplicate invoices by eye misses near-duplicates. Group rows by normalized invoice number and amount.",
         "summaries": ["31 duplicate groups, 142 rows", "no duplicates in the new import"],
         "source": "def find_duplicates(rows):\n    seen, dupes = {}, []\n    for i, r in enumerate(rows):\n        key = (str(r[0]).strip().upper(), round(float(r[3]), 2))\n        if key in seen:\n            dupes.append(i)\n        seen[key] = i\n    return dupes\n"},
        {"name": "column_profile", "at": 0.22, "language": "javascript", "kind": "in_host", "depends_on": ["read_range"],
         "reason": "Mixed text and number cells hide in columns that look uniform. Count cell types per column.",
         "summaries": ["Date: 1,702 dates, 143 text", "Amount: 1,845 numbers", "Vendor: 1,845 text, 37 blank"],
         "source": "function column_profile(sheet, col) {\n  const t = {};\n  for (const [v] of app.workbook.sheet(sheet).getRange(col).getValues()) t[typeof v] = (t[typeof v] || 0) + 1;\n  return JSON.stringify(t);\n}\n"},
        {"name": "match_rows", "at": 0.38, "language": "javascript", "kind": "in_host", "depends_on": ["read_range"],
         "reason": "Matching ledger rows to the bank export one lookup at a time is far too slow. Build an index of the bank sheet and match in one pass.",
         "args": {"sheet": "Ledger", "keyCol": "A2:A2000", "otherSheet": "Bank"},
         "summaries": ["1,310 of 1,845 matched", "1,662 of 1,845 matched, 183 unmatched", "no new unmatched rows"],
         "source": _SHEET_LOOKUP_V1},
        {"name": "explain_unmatched", "at": 0.52, "language": "python", "kind": "helper", "depends_on": ["match_rows", "read_range"],
         "reason": "The leftover rows have causes, not noise. Group unmatched rows by likely reason: amount off by a fee, date shifted, or missing.",
         "summaries": ["183 unmatched: 96 fee differences, 51 date shifts, 36 missing", "36 unmatched, all missing from the bank export"],
         "source": "def explain_unmatched(unmatched, bank):\n    out = {'fee': [], 'date_shift': [], 'missing': []}\n    for r in unmatched:\n        near = [b for b in bank if abs(b['amount'] - r['amount']) <= 3.0]\n        out['fee' if near else 'missing'].append(r['row'])\n    return out\n"},
        {"name": "check_totals", "at": 0.68, "language": "javascript", "kind": "in_host", "depends_on": ["read_range"],
         "reason": "Before sending a report I want proof that the summary equals the source. Compare the pivot grand total with the ledger sum.",
         "summaries": ["pivot total equals ledger total", "difference 0.00, 0 formula errors", "difference 12.40 in row group 'Misc'"],
         "source": "function check_totals() {\n  const led = app.workbook.sheet('Ledger').getRange('D2:D2000').getValues().flat().reduce((a, b) => a + (+b || 0), 0);\n  const piv = app.workbook.sheet('By Vendor').getRange('B2:B200').getValues().flat().reduce((a, b) => a + (+b || 0), 0);\n  return JSON.stringify({ledger: led, pivot: piv, diff: +(led - piv).toFixed(2)});\n}\n"},
    ],
    "break": {"at": 0.55, "hook": "match_rows", "label": "Spreadsheet app updated mid-run (planted change: range API replaced)",
              "detail": "error: getRange(...).getValues is not a function; the macro API changed in app version 4.2",
              "repair_reason": "The app update replaced Range.getValues() with an async Range.values(). Rewrote the lookup against the new calls.",
              "repaired_source": _SHEET_LOOKUP_V2, "repaired_detail": "health check passes with the new range API"},
    "goals": [
        (0.01, "Get exact cell values out of the workbook", "every later step depends on correct numbers"),
        (0.10, "Remove duplicate invoices", "duplicates would double-count spend"),
        (0.24, "Normalize dates and amounts", "text-formatted cells break sorting and sums"),
        (0.36, "Reconcile the ledger against the bank export", "the main task"),
        (0.54, "Fix the lookup macro after the app update", "matching by hand would take hours"),
        (0.62, "Explain and resolve unmatched rows", "the report needs a reason for each"),
        (0.72, "Build the summary pivot", "readers want totals by vendor"),
        (0.90, "Format and export the report", "the deliverable"),
    ],
    "reasoning": [
        (0.04, "Exact cells, not screenshots", "I misread a 3 as an 8 in a screenshot of the grid. The app's macro console can return the cell values as JSON, which is exact and cheaper than another screenshot."),
        (0.12, "Duplicates that don't look identical", "Some invoice numbers differ only by trailing spaces or case. Grouping by a normalized key and the amount catches them without removing legitimate repeat purchases."),
        (0.31, "Why do the sums disagree?", "143 dates are stored as text, so the date filter silently skips them. Profiling the columns shows this directly; I'll convert them before matching."),
        (0.56, "The matching macro stopped working after the update", "The error says getValues is not a function. The update changed the range API. I'll read the new docs and port the macro rather than go back to matching by eye."),
        (0.76, "Check the totals before formatting", "A pivot can look right and still drop rows. I'll compare its grand total with the ledger sum, then format."),
        (0.88, "Fewer screenshots now", "Nearly everything I need comes back from macros and helpers. I only take screenshots to check formatting."),
    ],
    "messages": [
        (0.17, "Removed 142 duplicate invoice rows. 1,845 rows remain."),
        (0.31, "All dates and amounts are now real dates and numbers."),
        (0.57, "match_rows broke when the app update changed its range API. I ported it to the new calls, and the health check passes."),
        (0.98, "The report is exported: 3 sheets, totals match the ledger, no formula errors."),
    ],
    "compactions": [0.28, 0.58, 0.83],
    "actions": [
        ("click", {"x": 302, "y": 148}, "selected a cell"), ("type", {"text": "=SUMIF(A:A,F2,D:D)"}, "entered a formula"),
        ("key", {"key": "ctrl+shift+L"}, "toggled the filter"), ("click", {"x": 520, "y": 82}, "opened the sort menu"),
        ("drag", {"from": [180, 62], "to": [300, 62]}, "widened a column"), ("type", {"text": "2026-09-30"}, "typed a date"),
    ],
    "doc_queries": ["Range.getValues", "Range.values", "pivot table API", "conditional formatting"],
}

SCENARIOS = {"factorio": FACTORIO, "spreadsheet": SPREADSHEET}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="factorio")
    ap.add_argument("--out", type=Path, default=None, help="default: runs/fake-<scenario>")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hours", type=float, default=None, help="default: 14 for factorio, 24 for spreadsheet")
    a = ap.parse_args()
    out = a.out or Path(f"runs/fake-{a.scenario}")
    hours = a.hours if a.hours is not None else {"factorio": 14, "spreadsheet": 24}[a.scenario]
    generate(out, a.seed, hours, SCENARIOS[a.scenario])
    print(f"wrote {out}")
