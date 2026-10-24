"""An extension cannot declare a tool timeout above the host's cap."""
import json

from conftest import SRC  # noqa: F401  (puts src on the path)
from toolserver.extensions import HostConfig, parse_manifest


def _manifest(ws, timeout):
    d = ws / "ext"
    d.mkdir()
    (d / "extension.json").write_text(json.dumps({
        "name": "ext_test", "version": 1, "reason": "test", "language": "python", "kind": "helper",
        "tools": [{"name": "slow", "description": "d", "run": ["python3", "x.py"], "timeout_s": timeout}]}))
    return "ext/extension.json"


def test_declared_timeout_is_clamped(tmp_path):
    host = HostConfig()
    assert host.max_tool_timeout_s == 90
    m = parse_manifest(tmp_path, _manifest(tmp_path, 1200), host, set())
    assert m.tools[0].timeout_s == 90


def test_lower_timeout_kept_and_cap_configurable(tmp_path):
    host = HostConfig(max_tool_timeout_s=10)
    m = parse_manifest(tmp_path, _manifest(tmp_path, 5), host, set())
    assert m.tools[0].timeout_s == 5
    (tmp_path / "ext" / "extension.json").unlink()
    (tmp_path / "ext").rmdir()
    m = parse_manifest(tmp_path, _manifest(tmp_path, 1200), host, set())
    assert m.tools[0].timeout_s == 10
