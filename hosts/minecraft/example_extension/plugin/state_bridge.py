# In-game part of the example extension. Run in game chat:  \state_bridge
# Serves the live game state as JSON at http://0.0.0.0:8777/state until the script is killed (\killjob).
import json, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import minescript as m

PORT = 8777

def snapshot():
    items = m.container_get_items()
    return {
        "t": time.time(), "player": m.player_name(), "position": m.player_position(),
        "health": m.player_health(), "world": str(m.world_info().name), "screen": m.screen_name(),
        "inventory": [vars(i) for i in m.player_inventory()],
        "container": None if items is None else [vars(i) for i in items],
    }

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(snapshot(), default=str).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

srv = HTTPServer(("0.0.0.0", PORT), H)
srv.timeout = 1.0
m.echo(f"state_bridge listening on {PORT}")
while True:           # single thread: Minescript calls must come from the main thread
    srv.handle_request()
