# Hand-written Minescript script: run in game chat as  \state_dump  (writes state.json next to the script).
import json, os, time
import minescript as m

def snapshot():
    inv = [vars(i) for i in m.player_inventory()]
    return {
        "t": time.time(),
        "player": m.player_name(),
        "position": m.player_position(),
        "orientation": m.player_orientation(),
        "health": m.player_health(),
        "world": vars(m.world_info()) if hasattr(m.world_info(), "__dict__") else str(m.world_info()),
        "screen": m.screen_name(),
        "container": None if m.container_get_items() is None else [vars(i) for i in m.container_get_items()],
        "inventory": inv,
    }

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
with open(out, "w") as f:
    json.dump(snapshot(), f, indent=1, default=str)
m.echo(f"state written: {out}")
