from toolserver.events import EventLog
import json


def test_seq_continues_after_restart(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "r1")
    log.write("server_started", host="127.0.0.1", port=1)
    log.write("server_started", host="127.0.0.1", port=1)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "type": "torn')  # crash mid-line
    EventLog(path, "r1").write("server_started", host="127.0.0.1", port=1)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["seq"] == 3
