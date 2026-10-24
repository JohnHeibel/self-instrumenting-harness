from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from runner.run import Config, Runner

FAKE = Path(__file__).parent / "fake_app_server.py"


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.run_dir = tmp_path / "run"
        self.fake_state = tmp_path / "fake"

    def runner(self, script: dict, **overrides) -> Runner:
        script_path = self.tmp / "script.json"
        script_path.write_text(json.dumps(script))
        cfg = dict(run_dir=self.run_dir, goal="Reach blue science.", toolserver=False,
                   agent_cmd=[sys.executable, str(FAKE), str(script_path), str(self.fake_state)],
                   host_check=lambda: (True, "up"), poll=0.02, host_poll=0.05,
                   backoff_base=0.01, backoff_cap=0.05, interrupt_grace=5, request_timeout=10)
        cfg.update(overrides)
        return Runner(Config(**cfg))

    def records(self, type_: str | None = None) -> list[dict]:
        path = self.run_dir / "runner.jsonl"
        recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        return [r for r in recs if type_ is None or r["type"] == type_]

    def requests(self, method: str | None = None) -> list[dict]:
        path = self.fake_state / "requests.jsonl"
        if not path.exists():
            return []
        reqs = [json.loads(l) for l in path.read_text().splitlines()]
        return [r for r in reqs if method is None or r.get("method") == method]

    def state(self) -> dict:
        return json.loads((self.run_dir / "runner-state.json").read_text())


@pytest.fixture
def h(tmp_path) -> Harness:
    return Harness(tmp_path)
