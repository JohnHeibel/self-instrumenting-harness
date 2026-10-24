"""How the runner starts the agent: `codex app-server` inside the agent container.

Each run gets its own Codex home under the run dir (`codex-home/`), mounted over
/home/agent/.codex, so thread history survives container restarts and `thread/resume`
finds it. The agent's workspace (`workspace/`, mounted at /home/agent/work) holds its
notes and also survives. The host's auth.json is bind-mounted, not copied, so a token
refresh can't fork the login (ModdedBench lesson).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE = "harness-agent:spike"


def container_name(run_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in run_id)
    return f"harness-agent-{safe}"[:63]


def prepare_codex_home(run_dir: Path) -> Path:
    home = run_dir / "codex-home"
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    if not cfg.exists():
        shutil.copyfile(REPO / "agent" / "codex-config.toml", cfg)
    (run_dir / "workspace").mkdir(exist_ok=True)
    return home


def docker_cmd(run_dir: Path, run_id: str, auth_json: Path, port: int,
               image: str = DEFAULT_IMAGE, model: str | None = None,
               reasoning_summary: str = "detailed") -> list[str]:
    home = prepare_codex_home(run_dir)
    cmd = ["docker", "run", "-i", "--rm", "--name", container_name(run_id),
           "-v", f"{home}:/home/agent/.codex",
           "-v", f"{auth_json}:/home/agent/.codex/auth.json",
           "-v", f"{run_dir / 'workspace'}:/home/agent/work",
           image, "codex", "app-server",
           "-c", f'mcp_servers.harness.url="http://host.docker.internal:{port}/mcp"',
           "-c", f'model_reasoning_summary="{reasoning_summary}"']
    if model:
        cmd += ["-c", f'model="{model}"']
    return cmd


def remove_container(run_id: str) -> None:
    """Kill a leftover container from a crashed runner. Killing the docker CLI alone
    doesn't always stop the container on Windows."""
    subprocess.run(["docker", "rm", "-f", container_name(run_id)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
