"""Factorio host control for one run (Windows, Steam copy of Factorio). Stdlib only.

    python hosts/factorio/hostctl.py <command> --run-dir runs/<run>

  prepare   create <run>/host/: server and client configs (each its own write-data), the shared
            mods folder, server settings, a starting save, an RCON password
  start     start the headless server from the latest save (RCON on 127.0.0.1), wait for RCON,
            then launch the GUI client through Steam, joined to it; waits for its window
  stop      save over RCON, then stop the client and the server
  restart   stop, then start (picks up mod changes: control and data stage)
  check     exit 0 when the server answers RCON and the client's window is open
  save      /server-save over RCON
  make-start-save
            build hosts/factorio/saves/start.zip once per machine: a new map, joined once so the
            freeplay intro has played and been skipped (Tab, then Escape on the client window).
            prepare copies it into each new run. Needs the toolserver package (for the keys):
              uv run --project runner python hosts/factorio/hostctl.py make-start-save --run-dir runs/start-save

Layout under <run>/host/ (never mounted into the agent's container):
  server/   the server's write-data: config.ini, saves/, script-output/ (a junction into
            the workspace, so files mods write reach the agent), factorio-current.log
  client/   the client's write-data and config.ini
  mods/     the mod directory both use; extensions' in_host parts are installed here
  rcon.json {"host", "port", "password"}; also copied to <run>/workspace/host/rcon.json
  pids.json server and client process ids

The client is launched as `steam.exe -applaunch 427520 <args>`: launching factorio.exe with
arguments makes Steam ask the user to confirm them, and this way it doesn't (decided by John,
2026-10-24). The server is the same factorio.exe, run headless; Steam doesn't prompt for it.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rcon import Rcon, RconError  # noqa: E402

STEAM_APP = "427520"
DEFAULT_EXE = r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe"
DEFAULT_STEAM = r"C:\Program Files (x86)\Steam\steam.exe"
CLIENT_TITLE = "Factorio"
CREATE_NO_WINDOW = 0x08000000
SAVES = Path(__file__).parent / "saves"      # gitignored; made by make-start-save
GRADER = Path(__file__).resolve().parents[2] / "graders" / "factorio"
GRADER_MOD = "harness-grader"


class HostError(Exception):
    pass


class Host:
    def __init__(self, args) -> None:
        self.run_dir = Path(args.run_dir).resolve()
        self.dir = self.run_dir / "host"
        self.exe = Path(args.exe)
        self.steam = Path(args.steam)
        self.game_port = args.game_port
        self.rcon_port = args.rcon_port
        self.seed = args.seed
        self.final = getattr(args, "final", False)
        self.no_grader = getattr(args, "no_grader", False)

    # Paths ------------------------------------------------------------------------

    @property
    def server(self) -> Path:
        return self.dir / "server"

    @property
    def client(self) -> Path:
        return self.dir / "client"

    @property
    def mods(self) -> Path:
        return self.dir / "mods"

    def rcon_info(self) -> dict:
        return json.loads((self.dir / "rcon.json").read_text())

    def rcon(self) -> Rcon:
        info = self.rcon_info()
        return Rcon("127.0.0.1", info["port"], info["password"], timeout=10)

    # Commands ---------------------------------------------------------------------

    def prepare(self, create: bool = True) -> str:
        for d in (self.server / "saves", self.client, self.mods):
            d.mkdir(parents=True, exist_ok=True)
        for d in (self.server, self.client):
            # read-data is the game's own data; write-data keeps each instance's state apart
            # from the user's normal Factorio folder.
            (d / "config.ini").write_text(
                "[path]\nread-data=__PATH__executable__/../../data\n"
                f"write-data={d.as_posix()}\n", encoding="utf-8")
        out = self.run_dir / "workspace" / "host" / "script-output"
        out.mkdir(parents=True, exist_ok=True)
        link = self.server / "script-output"
        if not link.exists():
            # A junction, so the server's script output lands in the workspace (mounted in the
            # agent's container) without mounting anything else.
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(out)], check=True,
                           stdout=subprocess.DEVNULL)
        if not (self.dir / "rcon.json").exists():
            info = {"host": "host.docker.internal", "port": self.rcon_port, "password": secrets.token_urlsafe(18)}
            (self.dir / "rcon.json").write_text(json.dumps(info, indent=2))
        info = self.rcon_info()
        (self.run_dir / "workspace" / "host" / "rcon.json").write_text(json.dumps(info, indent=2))
        settings = {"name": f"harness {self.run_dir.name}", "description": "harness run",
                    "visibility": {"public": False, "lan": False}, "require_user_verification": False,
                    "max_players": 1, "auto_pause": False, "only_admins_can_pause_the_game": False,
                    "autosave_interval": 10, "autosave_slots": 6, "non_blocking_saving": False}
        (self.dir / "server-settings.json").write_text(json.dumps(settings, indent=2))
        if create and not list((self.server / "saves").glob("*.zip")):
            save = self.server / "saves" / "start.zip"
            if (SAVES / "start.zip").exists() and not self.seed:
                shutil.copy2(SAVES / "start.zip", save)
                return f"prepared {self.dir} from {SAVES / 'start.zip'}"
            args = ["--create", str(save)] + (["--map-gen-seed", str(self.seed)] if self.seed else [])
            self._run_exe(args, self.server, timeout=180)
            if not save.exists():
                raise HostError(f"--create did not write {save}; see {self.server / 'factorio-current.log'}")
        return f"prepared {self.dir}"

    def start(self, timeout: float = 240) -> str:
        if not (self.dir / "rcon.json").exists():
            self.prepare()
        pids = self._pids()
        if not _alive(pids.get("server")):
            info = self.rcon_info()
            # Windows can hold a killed server's ports for a few seconds (restart hits this).
            busy = lambda: _listening(self.rcon_port) or _listening_udp(self.game_port)
            deadline = time.monotonic() + 30
            while busy() and time.monotonic() < deadline:
                time.sleep(1)
            if busy():
                raise HostError(f"port {self.rcon_port} (RCON) or {self.game_port} (game) is already in use")
            if not self.no_grader:
                # The grader's read-only mod, refreshed on every start. The tool server refuses an
                # agent extension with this folder name, since the agent didn't install it.
                shutil.copytree(GRADER / "mod", self.mods / GRADER_MOD, dirs_exist_ok=True)
            proc = subprocess.Popen(
                [str(self.exe), "--config", str(self.server / "config.ini"),
                 "--start-server-load-latest", "--server-settings", str(self.dir / "server-settings.json"),
                 "--mod-directory", str(self.mods), "--port", str(self.game_port),
                 "--rcon-bind", f"127.0.0.1:{self.rcon_port}", "--rcon-password", info["password"]],
                stdout=(self.server / "stdout.log").open("ab"), stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
            pids["server"] = proc.pid
            self._save_pids(pids)
            self._wait(lambda: self._rcon_ok(), timeout, "the server to answer RCON", pids["server"])
        if not self._client_pid():
            subprocess.Popen([str(self.steam), "-applaunch", STEAM_APP,
                              "--config", str(self.client / "config.ini"),
                              "--mod-directory", str(self.mods),
                              "--mp-connect", f"127.0.0.1:{self.game_port}"])
            self._wait(lambda: self._client_pid() is not None, timeout, "the client to start")
            pids["client"] = self._client_pid()
            self._save_pids(pids)
            self._wait(self._client_joined, timeout, "the client to join the server")
        if not self.no_grader and not _alive(pids.get("grader")):
            proc = subprocess.Popen([sys.executable, str(GRADER / "grader.py"), "--run-dir", str(self.run_dir)],
                                    stdout=(self.dir / "grader.log").open("ab"), stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            pids["grader"] = proc.pid
            self._save_pids(pids)
        return "host up: server answers RCON, client joined, grader running"

    def stop(self) -> str:
        """Stops the game. With --final (the end of the run) the grader too, after a last poll."""
        pids = self._pids()
        if self.final and _alive(pids.get("grader")):
            subprocess.run([sys.executable, str(GRADER / "grader.py"), "--run-dir", str(self.run_dir), "--once"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            subprocess.run(["taskkill", "/PID", str(pids["grader"]), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            pids.pop("grader")
        if _alive(pids.get("server")):
            try:
                self.save()
            except (OSError, RconError) as e:
                print(f"save before stop failed: {e}", file=sys.stderr)
        for key in ("client", "server"):
            pid = pids.get(key) if key == "server" else (self._client_pid() or pids.get(key))
            if _alive(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            self._wait(lambda: not _alive(pid), 30, f"the {key} to exit")
        self._save_pids({k: v for k, v in pids.items() if k == "grader"})
        return "host stopped" + (" (grader too)" if self.final else "")

    def restart(self) -> str:
        t0 = time.monotonic()
        self.stop()
        self.start()
        return f"host restarted in {time.monotonic() - t0:.0f} s"

    def check(self, tries: int = 3) -> tuple[bool, str]:
        """Up only if a check passes; retries so one slow RCON reply (say, while the agent's
        tool runs a big query) doesn't count as the host being down."""
        for i in range(tries):
            up, detail = self._check_once()
            if up:
                return up, detail
            if i + 1 < tries:
                time.sleep(2)
        return up, detail

    def _check_once(self) -> tuple[bool, str]:
        if not _alive(self._pids().get("server")):
            return False, "server not running"
        if not self._rcon_ok():
            return False, "server not answering RCON"
        if not self._client_pid():
            return False, "client not running"
        return True, "server and client up"

    def save(self) -> str:
        with self.rcon() as r:
            out = r.command("/server-save")
        return out.strip() or "saved"

    def make_start_save(self) -> str:
        """In a fresh run dir: create a map, join it, skip the intro, save, stop, keep the save."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "toolserver" / "src"))
        from toolserver import capture
        from toolserver.inputs import Controller, Win32Backend
        if (self.dir / "rcon.json").exists():
            raise HostError(f"{self.run_dir} is in use; make-start-save needs a fresh --run-dir")
        self.prepare(create=False)
        save = self.server / "saves" / "start.zip"
        self._run_exe(["--create", str(save)] + (["--map-gen-seed", str(self.seed)] if self.seed else []),
                      self.server, timeout=180)
        self.start()
        try:
            time.sleep(5)                                   # the intro is playing
            target = capture.Target(CLIENT_TITLE, "factorio.exe", "window")
            ctl = Controller(Win32Backend(), target)
            ctl.capture = capture.screenshot(target, 1280).capture
            for key in ("tab", "escape"):                   # skip the cutscene, then leave its remote view
                print(ctl.key(key))
                time.sleep(3)
            (self.run_dir / "after-intro.png").write_bytes(capture.screenshot(target, 1280).png)
            self.save()
        finally:
            self.stop()
        SAVES.mkdir(exist_ok=True)
        shutil.copy2(save, SAVES / "start.zip")
        return f"wrote {SAVES / 'start.zip'}; check {self.run_dir / 'after-intro.png'} shows the normal game view"

    # Helpers ----------------------------------------------------------------------

    def _run_exe(self, args: list[str], write_dir: Path, timeout: float) -> None:
        subprocess.run([str(self.exe), "--config", str(write_dir / "config.ini"),
                        "--mod-directory", str(self.mods), *args],
                       stdout=(write_dir / "stdout.log").open("ab"), stderr=subprocess.STDOUT,
                       timeout=timeout, creationflags=CREATE_NO_WINDOW)

    def _rcon_ok(self) -> bool:
        try:
            with self.rcon() as r:
                r.command("/version")
            return True
        except (OSError, RconError):
            return False

    def _client_joined(self) -> bool:
        # Never a Lua command (/c, /sc) here: those mark the save as using commands, which
        # disables achievements and shows up in the grader's integrity signals.
        try:
            with self.rcon() as r:
                out = r.command("/players online")
        except (OSError, RconError):
            return False
        return "(0)" not in out and "online" in out.lower()

    def _client_pid(self) -> int | None:
        """The GUI Factorio whose command line names this run's client config."""
        cfg = str(self.client / "config.ini").lower()
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='factorio.exe'\" | "
                              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            pid, _, cmd = line.partition("\t")
            if cfg in cmd.lower().replace("/", "\\"):
                return int(pid)
        return None

    def _pids(self) -> dict:
        p = self.dir / "pids.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_pids(self, pids: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "pids.json").write_text(json.dumps(pids))

    def _server_errors(self) -> str:
        """The server log's errors, so a mod that fails to load says why (e.g. a wrong
        factorio_version) instead of only "the process exited"."""
        log = self.server / "factorio-current.log"
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        out, keep = [], 0
        for line in lines:
            if " Error " in line or "Failed to load mod" in line:
                out.append(line.strip())
                keep = 4   # the reasons follow on indented lines
            elif keep and line.startswith((" ", "\t", "•")) and "Info " not in line:
                out.append(line.strip())
                keep -= 1
            else:
                keep = 0
        return ("; server log:\n" + "\n".join(out[-12:])) if out else ""

    def _wait(self, cond, timeout: float, what: str, pid: int | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            if pid is not None and not _alive(pid):
                raise HostError(f"gave up waiting for {what}: the process exited{self._server_errors()}")
            time.sleep(1)
        raise HostError(f"timed out after {timeout:.0f} s waiting for {what}")


def _alive(pid: int | None) -> bool:
    """Asks Windows directly. (tasklist sometimes leaves out a live process under load, which
    made the host look down.)"""
    if not pid:
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5           # access denied: it exists
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def _listening_udp(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["prepare", "start", "stop", "restart", "check", "save", "make-start-save"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--exe", default=os.environ.get("FACTORIO_EXE", DEFAULT_EXE))
    ap.add_argument("--steam", default=os.environ.get("STEAM_EXE", DEFAULT_STEAM))
    ap.add_argument("--game-port", type=int, default=34297, help="UDP; not 34197, so the user's own games are unaffected")
    ap.add_argument("--rcon-port", type=int, default=27015)
    ap.add_argument("--seed", type=int, default=0, help="map seed for the starting save (0: random)")
    ap.add_argument("--final", action="store_true", help="stop: the run is over, so stop the grader too")
    ap.add_argument("--no-grader", action="store_true", help="start: without the grader mod and process")
    args = ap.parse_args()
    host = Host(args)
    try:
        if args.command == "check":
            up, detail = host.check()
            print(detail)
            return 0 if up else 1
        print(getattr(host, args.command.replace("-", "_"))())
        return 0
    except (HostError, OSError, RconError, subprocess.SubprocessError) as e:
        print(f"{args.command} failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
