"""What a program wrote just before it went down, found without knowing the program.

When the host goes down mid-run, the cause is usually written somewhere: a log, a crash report, an
error file. Rather than configure each program's log, look at every text file it changed in the last
few minutes under the folders the harness gave it (the run's host folder, plus any the host config
adds), and keep the lines that look like errors, with context, plus each file's last lines.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

RECENT_S = 600            # files changed within this long before the outage
MAX_FILES = 3             # most recently changed first
MAX_WALK = 5000           # files looked at, at most
MAX_READ = 256 * 1024     # bytes read from the end of each file
PER_FILE = 1500           # characters kept per file
TOTAL = 4000
GAP, BURST = 10, 60     # error lines this close belong to one burst; a burst spans at most BURST lines
ERROR = re.compile(r"error|exception|traceback|fatal|failed|panic|crash|abort", re.I)


def _text_files(dirs: list[Path], since: float) -> list[tuple[float, Path, Path]]:
    found, seen = [], 0
    for root in dirs:
        if not root.is_dir():
            continue
        for dirpath, _, names in os.walk(root):
            for n in names:
                seen += 1
                if seen > MAX_WALK:
                    return found
                p = Path(dirpath) / n
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime >= since and 0 < st.st_size:
                    found.append((st.st_mtime, p, root))
    return found


def _read_tail(p: Path) -> str | None:
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - MAX_READ))
            data = f.read()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None                       # not text
    return data.decode("utf-8", errors="replace")


def excerpt(text: str) -> str:
    """The last burst of error-looking lines, from where it starts (a burst: hits no more than GAP lines
    apart, so a stack trace between them stays in), with context, plus the file's last lines."""
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if ERROR.search(l)]
    keep: list[int] = []
    if hits:
        first = last = hits[-1]
        for h in reversed(hits[:-1]):
            if first - h > GAP or last - h > BURST:
                break
            first = h
        keep = list(range(max(0, first - 3), min(len(lines), last + 6)))
    tail = list(range(max(0, len(lines) - 8), len(lines)))
    out, prev = [], None
    for i in sorted(set(keep + tail)):
        if prev is not None and i != prev + 1:
            out.append("…")
        out.append(lines[i])
        prev = i
    text = "\n".join(out)
    if len(text) <= PER_FILE:
        return text
    head = "\n".join(lines[i] for i in sorted(set(keep)))     # too long: keep the start of the burst
    return head[:PER_FILE - 1] + "…"


def evidence(dirs: list[Path], since: float) -> tuple[str, list[str]]:
    """Excerpts of the most recently changed text files under `dirs`, as one block, and their names.
    Files with error-looking lines come first; a file whose excerpt repeats another's is left out."""
    found = []
    for mtime, p, root in _text_files(dirs, since):
        text = _read_tail(p)
        if text and text.strip():
            found.append((bool(ERROR.search(text[-20000:])), mtime, p, root, text))
    found.sort(key=lambda f: (not f[0], -f[1]))
    parts, names, seen, total = [], [], set(), 0
    for _, _, p, root, text in found:
        if len(names) >= MAX_FILES or total >= TOTAL:
            break
        ex = excerpt(text)
        sig = ex[-400:]
        if sig in seen:
            continue
        seen.add(sig)
        name = p.relative_to(root).as_posix()
        parts.append(f"--- {name}\n{ex}"[: TOTAL - total])
        names.append(name)
        total += len(parts[-1])
    return "\n".join(parts), names
