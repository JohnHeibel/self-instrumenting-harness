"""Odoo host control (Docker: PostgreSQL + Odoo Community 19.0, pinned image). Stdlib only.

    python hosts/odoo/hostctl.py <command>

  start      bring the containers up, wait for Odoo's health check; if the database has not been
             bootstrapped yet (no keys file), run bootstrap.py once
  stop       stop the containers (data volumes stay)
  reset      stop and delete the containers and volumes: the next start builds a fresh database
             (and deletes the old keys file, which no longer matches anything)
  check      exit 0 when Odoo answers its health check and the keys file exists
  (start / bootstrap take --agent-web-password PW: the agent user also gets that web password, for raw runs)
  bootstrap  (re)run the setup-time bootstrap by hand
  agent-key  --out FILE: write {"url", "db", "login", "key"} for the agent user only (chmod 600), with the URL
             the agent's container uses; the runner puts this in the agent's workspace (host/odoo.json)

State outside the repo, in ~/.sisyphus/odoo/ (override with SISYPHUS_ODOO_HOME):
  odoo.conf        generated once: random master password, database list off, single database
  odoo-keys.json   chmod 600: the agent / auditor / intake users' API keys (see bootstrap.py)

Odoo listens on 127.0.0.1:8069 only; containers (the agent's sandbox) reach it as
host.docker.internal:8069.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("SISYPHUS_ODOO_HOME", Path.home() / ".sisyphus" / "odoo"))
CONF = HOME / "odoo.conf"
KEYS = HOME / "odoo-keys.json"
DB = "odoo"
URL = "http://127.0.0.1:8069"

CONF_TEXT = """[options]
admin_passwd = {master}
db_host = db
db_user = odoo
db_password = odoo
db_name = {db}
list_db = False
dbfilter = ^{db}$
proxy_mode = False
workers = 0
limit_memory_soft = 1073741824
limit_memory_hard = 1342177280
"""


def prepare() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)
    if not CONF.exists():
        CONF.write_text(CONF_TEXT.format(master=secrets.token_urlsafe(24), db=DB))
        os.chmod(CONF, 0o600)


def compose(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    prepare()
    env = {**os.environ, "ODOO_CONF": str(CONF)}
    return subprocess.run(["docker", "compose", "-f", str(HERE / "docker-compose.yml"), *args], env=env,
                          check=check, text=True, capture_output=capture)


def healthy() -> bool:
    try:
        with urllib.request.urlopen(URL + "/web/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def wait_healthy(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if healthy():
            return True
        time.sleep(2)
    return False


def start(timeout: float, agent_web_password: str = "") -> int:
    compose("up", "-d", "--wait", "db")
    if not KEYS.exists():
        # Odoo's own image would start serving an empty database; bootstrap needs it initialised first.
        print("no keys file: bootstrapping a fresh database", flush=True)
        extra = ["--agent-web-password", agent_web_password] if agent_web_password else []
        return subprocess.call([sys.executable, str(HERE / "bootstrap.py"), *extra])
    compose("up", "-d", "odoo")
    if not wait_healthy(timeout):
        print("Odoo did not answer /web/health in time", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "stop", "reset", "check", "bootstrap", "agent-key"])
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--out", help="agent-key: where to write the agent's connection file")
    ap.add_argument("--agent-web-password", default="", help="start/bootstrap: also give the agent user this web "
                    "password (raw, input-only runs; the hero run has none)")
    ap.add_argument("--run-dir", help="accepted for host-config symmetry; unused")
    a = ap.parse_args()
    if a.action == "check":
        return 0 if healthy() and KEYS.exists() else 1
    if a.action == "agent-key":
        keys = json.loads(KEYS.read_text())
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"url": "http://host.docker.internal:8069", "db": keys["db"],
                                   "login": keys["agent"]["login"], "key": keys["agent"]["key"]}, indent=2))
        os.chmod(out, 0o600)
        return 0
    if a.action == "start":
        return start(a.timeout, a.agent_web_password)
    if a.action == "stop":
        compose("stop")
        return 0
    if a.action == "bootstrap":
        extra = ["--agent-web-password", a.agent_web_password] if a.agent_web_password else []
        return subprocess.call([sys.executable, str(HERE / "bootstrap.py"), *extra])
    compose("down", "-v", "--remove-orphans")
    KEYS.unlink(missing_ok=True)
    print("volumes removed; the next start builds a fresh database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
