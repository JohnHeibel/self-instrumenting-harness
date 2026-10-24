import json, sys, urllib.request
args = json.load(sys.stdin) if not sys.stdin.isatty() else {}
last = None
for host in ("host.docker.internal", "127.0.0.1"):
    try:
        s = json.load(urllib.request.urlopen(f"http://{host}:8777/state", timeout=5))
        inv = sum(i.get("count", 0) for i in s["inventory"])
        s["summary"] = f'{s["player"]} at {[round(v,1) for v in s["position"]]}, {inv} items, screen={s["screen"]}'
        print(json.dumps(s)); sys.exit(0)
    except Exception as e:
        last = e
print(f"bridge not reachable ({last}); in game run \\state_bridge", file=sys.stderr); sys.exit(2)
