"""Start, stop and check the Minecraft client (Prism Launcher instance) on macOS. Data in, exit code out."""
import argparse, subprocess, sys, time

INSTANCE = "sisyphus-neoforge"
GAME = "org.prismlauncher.EntryPoint"          # the game's JVM main class, on its command line


def pids() -> list[str]:
    out = subprocess.run(["pgrep", "-f", GAME], capture_output=True, text=True).stdout.split()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "start", "stop", "focus"])
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    if a.action == "check":
        return 0 if pids() else 1
    if a.action == "focus":
        # Background apps can't take focus on recent macOS, but a real click on the title bar can.
        import Quartz as Q
        for w in Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly, Q.kCGNullWindowID):
            if str(w.get("kCGWindowOwnerPID")) in pids() and "Minecraft" in (w.get("kCGWindowName") or ""):
                b = w["kCGWindowBounds"]
                pt = (b["X"] + b["Width"] - 120, b["Y"] + 10)
                for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
                    Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, kind, pt, Q.kCGMouseButtonLeft))
                    time.sleep(0.05)
                return 0
        return 1
    if a.action == "start":
        if not pids():
            subprocess.run(["open", "-a", "Prism Launcher", "--args", "--launch", INSTANCE], check=True)
        end = time.time() + a.timeout
        while time.time() < end:
            if pids():
                return 0
            time.sleep(2)
        print("the game did not start (Prism's first-run wizard open? screen locked?)", file=sys.stderr)
        return 1
    for p in pids():                              # SIGTERM runs Minecraft's shutdown hook: world is saved
        subprocess.run(["kill", "-TERM", p])
    for _ in range(30):
        if not pids():
            return 0
        time.sleep(1)
    return 1


sys.exit(main())
