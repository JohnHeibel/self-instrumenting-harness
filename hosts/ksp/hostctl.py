"""Kerbal Space Program host control for one run (Windows, Steam copy of KSP 1.12). Stdlib only.

    python hosts/ksp/hostctl.py <command> --run-dir runs/<run>

  prepare   make <run>/host/ksp/: this run's own copy of the game (the large read-only folders
            hard-linked from the Steam install, everything the game writes copied or new), the
            harness-grader plugin, settings (windowed, keeps running in the background), and the
            agent's view under <run>/workspace/host/
  build     compile every plugin in the copy's GameData from its .cs files (see Plugins below);
            exits 1 with the compiler's errors if one fails
  start     build, then launch the game and wait until the run's save is open (the grader plugin
            opens it at the main menu; a new run gets a new sandbox save); starts the grader
  stop      ask the game to save, wait for it, then close the game (--final: the grader too,
            after a last poll)
  restart   build first (a plugin that doesn't compile leaves the game running as it was), then
            stop and start: KSP loads plugins only at startup
  check     exit 0 when the game is running, its grader plugin wrote state in the last 20 s, and
            a game is open (not the loading screen or main menu); a scene change in a game that
            was up within SCENE_LOAD_GRACE still counts as up
  save      ask the game to save now

Plugins. KSP loads compiled .dll files from GameData. Each folder in GameData that has .cs files
(anywhere under it, except the game's own Squad folder) is compiled with the host's C# compiler
(Roslyn csc from Visual Studio, found with vswhere; --csc overrides) against all of the game's
own assemblies, into <run>/host/build/<folder>.dll; start copies those into
GameData/harness-built/Plugins/ while the game is stopped (it locks the plugins it loaded, and
the agent's own folders must stay replaceable). The agent's extensions are installed into
GameData by the tool server, and the reload action "restart" builds and restarts the game.

Layout:
  <run>/host/ksp/          the run's game; saves/harness/ is its save, harness/ the grader
                           plugin's files (state.json, save requests), KSP.log its log
  <run>/host/ksp/agent-io  a junction to <run>/workspace/host/io: files plugins write there
                           reach the agent
  <run>/workspace/host/    api.txt (the game's public API, one member per line), io/, and
                           KSP-last.log (the game's log, copied at each stop)
  <run>/host/pids.json     the game's and the grader's process ids

The game is launched directly (KSP_x64.exe in the copy), not through Steam, so the user's own
install, settings and saves are never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_INSTALL = r"C:\Program Files (x86)\Steam\steamapps\common\Kerbal Space Program"
VSWHERE = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GRADER = REPO / "graders" / "ksp"
GRADER_PLUGIN = "harness-grader"
BUILT = "harness-built"                     # GameData folder for the compiled plugins
CACHE = HERE / "cache"                       # gitignored: the API listing, built once per install
CREATE_NO_WINDOW = 0x08000000
EXE = "KSP_x64.exe"
# Read-only game data, hard-linked into each run (same volume) instead of copied: ~4.6 GB.
LINKED = ["KSP_x64_Data", "MonoBleedingEdge", "GameData/Squad"]
# Not copied: the user's saves and the stock example craft (the run builds its own craft),
# the launcher, and the game's write-back files (a run starts from the game's defaults).
SKIPPED = {"saves", "Ships", "Launcher.exe", "Launcher_Data", "settings.cfg", "KSP.log", "Logs",
           "PartDatabase.cfg", "thumbs", "Missions", "screenshots", "harness", "agent-io"}
GAME_SCENES = {"SPACECENTER", "EDITOR", "FLIGHT", "TRACKSTATION"}
SCENE_LOAD_GRACE = 120   # s a scene change may stall the game's state before check calls it down
# The game's settings for a run: windowed, keeps simulating when another window has focus (the
# agent's inputs and screenshots don't need focus), no splash screen or start-up dialogs. Keys the file doesn't name
# keep the game's defaults; the game writes the whole file back on exit.
SETTINGS = {
    "FULLSCREEN": "False",
    "SCREEN_RESOLUTION_WIDTH": "1280",
    "SCREEN_RESOLUTION_HEIGHT": "720",
    "SHOW_WHATSNEW_DIALOG": "False",                # the release notes and two expansion adverts
    "SHOW_WHATSNEW_DIALOG_VersionsShown": "1.12.5",
    "SERENITY_SHOW_EXPANSION_INFO": "False",
    "MISSION_SHOW_EXPANSION_INFO": "False",
    "SHOW_ANALYTICS_DIALOG": "False",
    "SIMULATE_IN_BACKGROUND": "True",
    "SHOW_SPLASH_SCREEN": "False",
    "AUTOSAVE_INTERVAL": "300",
}
API_ASSEMBLIES = ["Assembly-CSharp", "Assembly-CSharp-firstpass", "UnityEngine.CoreModule",
                  "UnityEngine.PhysicsModule", "UnityEngine.IMGUIModule", "UnityEngine.InputLegacyModule"]


class HostError(Exception):
    pass


class Host:
    def __init__(self, args) -> None:
        self.run_dir = Path(args.run_dir).resolve()
        self.dir = self.run_dir / "host"
        self.ksp = self.dir / "ksp"
        self.install = Path(args.install)
        self.csc_arg = args.csc
        self.final = getattr(args, "final", False)
        self.no_grader = getattr(args, "no_grader", False)
        self.workspace_host = self.run_dir / "workspace" / "host"

    # Commands ---------------------------------------------------------------------

    def prepare(self) -> str:
        if not (self.install / EXE).exists():
            raise HostError(f"no {EXE} in {self.install} (--install)")
        if (self.ksp / EXE).exists():
            return f"{self.ksp} already prepared"
        self.ksp.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        for rel in LINKED:
            _link_tree(self.install / rel, self.ksp / rel)
        for src in self.install.iterdir():
            if src.name in SKIPPED or src.name in LINKED or src.name == "GameData":
                continue
            dst = self.ksp / src.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        for name in os.listdir(self.install / "GameData"):          # the game's own loose files
            src = self.install / "GameData" / name
            if src.is_file():
                shutil.copy2(src, self.ksp / "GameData" / name)
        for d in ("saves", "Ships/VAB", "Ships/SPH", "harness"):
            (self.ksp / d).mkdir(parents=True, exist_ok=True)
        (self.ksp / "settings.cfg").write_text("".join(f"{k} = {v}\n" for k, v in SETTINGS.items()), encoding="utf-8")
        io = self.workspace_host / "io"
        io.mkdir(parents=True, exist_ok=True)
        link = self.ksp / "agent-io"
        if not link.exists():
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(io)], check=True, stdout=subprocess.DEVNULL)
        shutil.copy2(self._api(), self.workspace_host / "api.txt")
        return f"prepared {self.ksp} in {time.monotonic() - t0:.0f} s"

    def build(self) -> str:
        if not (self.ksp / EXE).exists():
            self.prepare()
        self._install_grader()
        built, failed = [], []
        for folder in self._plugin_folders():
            sources = sorted(folder.rglob("*.cs"))
            out = self.staged / f"{folder.name}.dll"
            if out.exists() and out.stat().st_mtime >= max(s.stat().st_mtime for s in sources):
                continue                                             # up to date
            ok, text = self._compile(sources, out)
            (built if ok else failed).append((folder.name, text))
        if failed:
            raise HostError("plugin build failed:\n" + "\n".join(f"{n}:\n{t}" for n, t in failed))
        return "built: " + (", ".join(n for n, _ in built) or "nothing changed")

    def _plugin_folders(self) -> list[Path]:
        return sorted(p for p in (self.ksp / "GameData").iterdir()
                      if p.is_dir() and p.name not in ("Squad", BUILT) and any(p.rglob("*.cs")))

    @property
    def staged(self) -> Path:
        return self.dir / "build"

    def _install_built(self) -> None:
        """With the game stopped (it locks the plugins it loaded): put each folder's compiled plugin
        in GameData/harness-built/, and drop plugins whose source folder is gone. The agent's
        folders hold only sources, so the tool server can replace them while the game runs."""
        dst = self.ksp / "GameData" / BUILT / "Plugins"
        dst.mkdir(parents=True, exist_ok=True)
        want = {f"{f.name}.dll" for f in self._plugin_folders()}
        for old in dst.glob("*.dll"):
            if old.name not in want:
                old.unlink()
        for name in want:
            src, target = self.staged / name, dst / name
            if src.exists() and (not target.exists() or target.read_bytes() != src.read_bytes()):
                shutil.copy2(src, target)

    def start(self, timeout: float = 420) -> str:
        self.build()
        pids = self._pids()
        new_save = not (self.ksp / "saves" / "harness" / "persistent.sfs").exists()
        if not self._game_pid():
            self._install_built()
            started = time.time()
            subprocess.Popen([str(self.ksp / EXE), "-screen-fullscreen", "0",
                              "-screen-width", SETTINGS["SCREEN_RESOLUTION_WIDTH"],
                              "-screen-height", SETTINGS["SCREEN_RESOLUTION_HEIGHT"]],
                             cwd=self.ksp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._wait(lambda: self._game_pid() is not None, 60, "the game to start")
            pids["game"] = self._game_pid()
            self._save_pids(pids)
            self._wait(lambda: self._in_game(since=started), timeout, "the run's save to open", pids["game"])
            if new_save:
                # A new save shows the space center's first-visit tip on its first load only, even
                # though the plugin marks it seen; one restart gives the run a clean start.
                self.stop()
                return self.start(timeout)
        if not self.no_grader and not _alive(pids.get("grader")):
            proc = subprocess.Popen([sys.executable, str(GRADER / "grader.py"), "--run-dir", str(self.run_dir)],
                                    stdout=(self.dir / "grader.log").open("ab"), stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            pids["grader"] = proc.pid
            self._save_pids(pids)
        return "host up: game running with the run's save open" + ("" if self.no_grader else ", grader running")

    def stop(self) -> str:
        pids = self._pids()
        if self.final and _alive(pids.get("grader")):
            subprocess.run([sys.executable, str(GRADER / "grader.py"), "--run-dir", str(self.run_dir), "--once"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            subprocess.run(["taskkill", "/PID", str(pids["grader"]), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            pids.pop("grader")
        pid = self._game_pid()
        saved = ""
        if pid:
            try:
                saved = self.save()
            except HostError as e:
                saved = f"save before stop failed: {e}"
                print(saved, file=sys.stderr)
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self._wait(lambda: not _alive(pid), 30, "the game to exit")
        log = self.ksp / "KSP.log"
        if log.exists():
            self.workspace_host.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log, self.workspace_host / "KSP-last.log")
        self._save_pids({k: v for k, v in pids.items() if k == "grader"})
        return "host stopped" + (f" ({saved})" if saved else "") + (" (grader too)" if self.final else "")

    def restart(self) -> str:
        t0 = time.monotonic()
        self.build()
        self.stop()
        self.start()
        return f"host restarted in {time.monotonic() - t0:.0f} s"

    def check(self) -> tuple[bool, str]:
        pid = self._game_pid()
        if not pid:
            return False, "game not running"
        st = self._state()
        if st is None:
            return False, "the grader plugin hasn't written state"
        age = time.time() - float(st.get("real_time") or 0)
        scene = st.get("scene")
        if age <= 20 and scene in GAME_SCENES:
            self._mark_up(pid)
            return True, f"game up in {scene}"
        # Switching scenes (to a vessel, the editor, the Space Center) shows LOADING and can stall the
        # game's state for a while. The same game, in a game scene a moment ago, is still up.
        since = self._up_since(pid)
        if since is not None and time.time() - since < SCENE_LOAD_GRACE and scene not in ("MAINMENU", "SETTINGS"):
            return True, f"game loading a scene (state {age:.0f} s old, scene {scene})"
        if age > 20:
            return False, f"the game's state is {age:.0f} s old (frozen or still loading)"
        return False, f"no game open (scene {scene}){': ' + st['error'] if st.get('error') else ''}"

    def _mark_up(self, pid: int) -> None:
        try:
            (self.dir / "last-up.json").write_text(json.dumps({"pid": pid, "t": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def _up_since(self, pid: int) -> float | None:
        """When this same game process was last seen up in a game scene, if it was."""
        try:
            d = json.loads((self.dir / "last-up.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return float(d["t"]) if d.get("pid") == pid else None

    def save(self, timeout: float = 30) -> str:
        done = self.ksp / "harness" / "saved.txt"
        before = done.stat().st_mtime if done.exists() else 0
        (self.ksp / "harness" / "request-save").write_text("save", encoding="utf-8")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if done.exists() and done.stat().st_mtime > before:
                text = done.read_text(encoding="utf-8").split(" ", 2)[-1]
                if not text.startswith("saved"):
                    raise HostError(text)
                return text
            time.sleep(0.5)
        raise HostError(f"the game didn't save within {timeout:.0f} s")

    # Helpers ----------------------------------------------------------------------

    def _install_grader(self) -> None:
        dst = self.ksp / "GameData" / GRADER_PLUGIN
        dst.mkdir(parents=True, exist_ok=True)
        for src in (GRADER / "plugin").glob("*.cs"):
            target = dst / src.name
            if not target.exists() or target.read_bytes() != src.read_bytes():
                shutil.copy2(src, target)

    def _compile(self, sources: list[Path], out: Path) -> tuple[bool, str]:
        managed = self.ksp / "KSP_x64_Data" / "Managed"
        game_data = self.ksp / "GameData"
        refs = [f'-r:"{p}"' for p in sorted(managed.glob("*.dll"))]
        out.parent.mkdir(parents=True, exist_ok=True)
        rsp = out.with_suffix(".rsp")                       # too many references for one command line
        rsp.parent.mkdir(parents=True, exist_ok=True)
        rsp.write_text("\n".join(["-nologo", "-nostdlib", "-target:library", "-optimize",
                                  "-langversion:latest", "-nowarn:1701,1702", f'-out:"{out}"', *refs,
                                  *[f'"{s.relative_to(game_data)}"' for s in sources]]), encoding="utf-8")
        # Run from GameData, so errors name files as the agent installed them (folder\File.cs).
        p = subprocess.run([self._csc(), f"@{rsp}"], capture_output=True, text=True, cwd=game_data,
                           creationflags=CREATE_NO_WINDOW)
        text = (p.stdout + p.stderr).strip()
        if p.returncode != 0:
            errors = [l for l in text.splitlines() if ": error " in l] or text.splitlines()
            return False, "\n".join(errors[:30])
        return True, text

    def _csc(self) -> str:
        if self.csc_arg:
            return self.csc_arg
        if Path(VSWHERE).exists():
            p = subprocess.run([VSWHERE, "-latest", "-products", "*", "-requires", "Microsoft.Component.MSBuild",
                                "-find", r"MSBuild\**\Bin\Roslyn\csc.exe"], capture_output=True, text=True)
            found = [l for l in p.stdout.splitlines() if l.strip()]
            if found:
                return found[0]
        raise HostError("no C# compiler: install Visual Studio (or Build Tools) with MSBuild, or pass --csc")

    def _api(self) -> Path:
        """The game's public API as text, built once per install with tools/apidump.cs."""
        out = CACHE / "api.txt"
        if out.exists():
            return out
        CACHE.mkdir(exist_ok=True)
        exe = CACHE / "apidump.exe"
        fw = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
            r"Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
        p = subprocess.run([self._csc(), "-nologo", f"-out:{exe}", f"-r:{fw / 'System.Core.dll'}",
                            str(HERE / "tools" / "apidump.cs")], capture_output=True, text=True)
        if p.returncode != 0:
            raise HostError("apidump didn't compile: " + p.stdout[-800:])
        tmp = CACHE / "api.tmp"
        p = subprocess.run([str(exe), str(self.install / "KSP_x64_Data" / "Managed"), str(tmp), *API_ASSEMBLIES],
                           capture_output=True, text=True)
        if p.returncode != 0 or not tmp.exists():
            raise HostError("apidump failed: " + (p.stdout + p.stderr)[-800:])
        header = ("# The game's public API (KSP 1.12, from its own assemblies): one type per unindented line,\n"
                  "# then its members. Search it with grep, e.g. grep -n 'class Vessel ' api.txt\n")
        out.write_text(header + tmp.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.unlink()
        return out

    def _state(self) -> dict | None:
        try:
            return json.loads((self.ksp / "harness" / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _in_game(self, since: float) -> bool:
        st = self._state()
        if not st or float(st.get("real_time") or 0) < since:
            return False
        if st.get("error"):
            raise HostError(f"the grader plugin couldn't open the save: {st['error'][:500]}")
        return st.get("scene") in GAME_SCENES

    def _game_pid(self) -> int | None:
        """The KSP_x64.exe running from this run's copy. A killed game stays listed while anything
        still holds a handle to it, so a listed process that has exited doesn't count."""
        exe = str(self.ksp / EXE).lower()
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='KSP_x64.exe'\" | "
                              "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)\" }"],
                             capture_output=True, text=True, creationflags=CREATE_NO_WINDOW).stdout
        for line in out.splitlines():
            pid, _, path = line.partition("\t")
            if path.strip().lower() == exe and _alive(int(pid)):
                return int(pid)
        return None

    def _log_errors(self) -> str:
        try:
            lines = (self.ksp / "KSP.log").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        errs = [l for l in lines if "[ERR" in l or "[EXC" in l or "Exception" in l]
        return ("; KSP.log:\n" + "\n".join(errs[-12:])) if errs else ""

    def _pids(self) -> dict:
        p = self.dir / "pids.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_pids(self, pids: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "pids.json").write_text(json.dumps(pids))

    def _wait(self, cond, timeout: float, what: str, pid: int | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            if pid is not None and not _alive(pid):
                raise HostError(f"gave up waiting for {what}: the game exited{self._log_errors()}")
            time.sleep(1)
        raise HostError(f"timed out after {timeout:.0f} s waiting for {what}{self._log_errors()}")


def _link_tree(src: Path, dst: Path) -> None:
    for root, dirs, files in os.walk(src):
        r = Path(root)
        target = dst / r.relative_to(src)
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            t = target / f
            if not t.exists():
                os.link(r / f, t)


def _alive(pid: int | None) -> bool:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["prepare", "build", "start", "stop", "restart", "check", "save"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--install", default=os.environ.get("KSP_INSTALL", DEFAULT_INSTALL))
    ap.add_argument("--csc", default=os.environ.get("CSC_EXE"), help="the C# compiler (default: Roslyn from Visual Studio)")
    ap.add_argument("--final", action="store_true", help="stop: the run is over, so stop the grader too")
    ap.add_argument("--no-grader", action="store_true", help="without the grader process (the plugin still loads the save)")
    args = ap.parse_args()
    host = Host(args)
    try:
        if args.command == "check":
            up, detail = host.check()
            print(detail)
            return 0 if up else 1
        print(getattr(host, args.command)())
        return 0
    except (HostError, OSError, subprocess.SubprocessError) as e:
        print(f"{args.command} failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
