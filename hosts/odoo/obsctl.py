"""Record the agent's browser window with OBS (raw Odoo runs). Host side only.

    uv run --project toolserver --with obsws-python python hosts/odoo/obsctl.py <command> ...

  install                       copy the profile + scene collection into ~/Library/Application Support/obs-studio
                                (idempotent) and turn obs-websocket on with a fresh random password (loopback use only,
                                kept in OBS's own config file). Does not start OBS.
  start --run-dir RUN           start OBS if it is not running (profile and collection "Sisyphus Raw"), point the
                                "Agent window" source at the agent browser's window (hosts/odoo/browser.py), record to
                                RUN/odoo-raw.mp4 (1920x1080, 30 fps, fragmented MP4), write RUN/video.json
                                {"file", "start_utc", "width", "height"}
  stop  --run-dir RUN [--quit]  stop recording, update video.json with the final file and duration, optionally quit OBS
  check                         exit 0 if OBS answers on the websocket

Scene "Raw" (hosts/odoo/obs/scene-collection.json): the browser window fitted into 1920x1020, and a 60 px bar with a
UTC clock (obs/clock.html, millisecond resolution) under it, so the video can be lined up with the run's logs by eye.

First time only: macOS asks to give OBS Screen Recording permission (System Settings > Privacy & Security > Screen &
System Audio Recording). Turn OBS on and restart OBS: until then the window capture is black.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBS_HOME = Path.home() / "Library" / "Application Support" / "obs-studio"
NAME = "Sisyphus Raw"
PORT = 4455
WS_CONFIG = OBS_HOME / "plugin_config" / "obs-websocket" / "config.json"


def utc(t: float | None = None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def install(run_dir: str | None = None) -> None:
    prof = OBS_HOME / "basic" / "profiles" / NAME
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "basic.ini").write_text((HERE / "obs" / "basic.ini").read_text().replace("@@RUN_DIR@@", run_dir or str(Path.home() / "sisyphus-runs")))
    scenes = OBS_HOME / "basic" / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    text = (HERE / "obs" / "scene-collection.json").read_text().replace("@@CLOCK_HTML@@", str(HERE / "obs" / "clock.html"))
    json.loads(text)
    (scenes / f"{NAME}.json").write_text(text)
    # No first-run wizard, no update prompts; start in our profile and collection.
    g = OBS_HOME / "global.ini"
    if not g.exists():
        g.write_text(f"[General]\nFirstRun=true\nConfirmOnExit=false\nEnableAutoUpdates=false\n\n"
                     f"[Basic]\nProfile={NAME}\nProfileDir={NAME}\nSceneCollection={NAME}\nSceneCollectionFile={NAME}\n")
    WS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if WS_CONFIG.exists() and json.loads(WS_CONFIG.read_text()).get("server_password"):
        cfg = json.loads(WS_CONFIG.read_text())   # OBS rewrites this file on quit: re-enable the server every time
        cfg.update(server_enabled=True, server_port=PORT, auth_required=True, alerts_enabled=False, first_load=False)
        WS_CONFIG.write_text(json.dumps(cfg, indent=2))
        os.chmod(WS_CONFIG, 0o600)
    else:
        WS_CONFIG.write_text(json.dumps({"alerts_enabled": False, "auth_required": True, "first_load": False,
                                         "server_enabled": True, "server_password": secrets.token_urlsafe(16),
                                         "server_port": PORT}, indent=2))
        os.chmod(WS_CONFIG, 0o600)


def obs_running() -> bool:
    return subprocess.run(["pgrep", "-x", "OBS"], capture_output=True).returncode == 0


def client(wait_s: float = 0):
    import obsws_python as obs
    pw = json.loads(WS_CONFIG.read_text())["server_password"]
    end = time.time() + wait_s
    while True:
        try:
            return obs.ReqClient(host="127.0.0.1", port=PORT, password=pw, timeout=8)
        except Exception as e:  # noqa: BLE001
            if time.time() >= end:
                raise SystemExit(f"OBS websocket not reachable: {type(e).__name__}")
            time.sleep(1.5)


def start(run_dir: Path) -> int:
    sys.path.insert(0, str(HERE))
    import browser
    install(str(run_dir))
    if not obs_running():
        subprocess.Popen(["open", "-g", "-a", "OBS", "--args", "--profile", NAME, "--collection", NAME, "--disable-updater",
                          "--disable-shutdown-check"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cl = client(wait_s=90)
    if cl.get_profile_list().current_profile_name != NAME:
        cl.set_current_profile(NAME)
    if cl.get_scene_collection_list().current_scene_collection_name != NAME:
        cl.set_current_scene_collection(NAME)
        time.sleep(2)
        cl = client(wait_s=30)
    w = browser.window()
    if not w:
        print("agent browser window not found: open it first (hosts/odoo/browser.py open)", file=sys.stderr)
        return 1
    cl.set_input_settings("Agent window", {"type": 1, "window": w["id"], "application": browser.BUNDLE_ID,
                                          "show_cursor": True, "hide_obs": False}, True)
    cl.set_input_settings("UTC clock", {"is_local_file": True, "local_file": str(HERE / "obs" / "clock.html"),
                                       "width": 1920, "height": 60}, True)
    run_dir.mkdir(parents=True, exist_ok=True)
    cl.set_record_directory(str(run_dir))
    cl.set_profile_parameter("Output", "FilenameFormatting", "odoo-raw")
    cl.set_profile_parameter("SimpleOutput", "RecFormat2", "fragmented_mp4")
    t0 = time.time()
    cl.start_record()
    for _ in range(40):
        if cl.get_record_status().output_active:
            break
        time.sleep(0.25)
    else:
        print("OBS did not start recording", file=sys.stderr)
        return 1
    t1 = time.time()
    (run_dir / "video.json").write_text(json.dumps({
        "file": "odoo-raw.mp4", "start_utc": utc((t0 + t1) / 2), "width": 1920, "height": 1080,
        "recorder": "obs", "note": "start_utc is within about a second; the clock bar in the video gives the exact time"}, indent=2))
    print(f"{utc()} recording to {run_dir}/odoo-raw.mp4 (window id {w['id']})", flush=True)
    return 0


def stop(run_dir: Path, quit_obs: bool) -> int:
    rc = 0
    try:
        cl = client(wait_s=5)
        if cl.get_record_status().output_active:
            out = cl.stop_record().output_path
            for _ in range(60):
                if not cl.get_record_status().output_active:
                    break
                time.sleep(0.5)
            vj = run_dir / "video.json"
            if vj.exists() and out:
                d = json.loads(vj.read_text())
                d["file"] = str(Path(out).relative_to(run_dir)) if Path(out).is_relative_to(run_dir) else out
                d["stop_utc"] = utc()
                vj.write_text(json.dumps(d, indent=2))
            print(f"{utc()} recording stopped: {out}", flush=True)
    except SystemExit as e:
        print(e, file=sys.stderr)
        rc = 1
    if quit_obs:
        subprocess.run(["osascript", "-e", 'tell application "OBS" to quit'], capture_output=True)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "start", "stop", "check"])
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--quit", action="store_true")
    a = ap.parse_args()
    if a.action == "install":
        install()
        print(f"installed OBS profile and scene collection {NAME!r} in {OBS_HOME}")
        return 0
    if a.action == "check":
        client()
        return 0
    if not a.run_dir:
        ap.error("--run-dir is required")
    return start(a.run_dir) if a.action == "start" else stop(a.run_dir, a.quit)


if __name__ == "__main__":
    sys.exit(main())
