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
    """The last error-looking stretch (a few lines before, more after) and the last lines."""
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if ERROR.search(l)]
    keep: list[int] = []
    if hits:
        last = hits[-1]
        first = last
        while first > 0 and first - 1 in hits and last - first < 20:   # the start of that stretch
            first -= 1
        keep = list(range(max(0, first - 5), min(len(lines), last + 15)))
    tail = list(range(max(0, len(lines) - 8), len(lines)))
    out, prev = [], None
    for i in sorted(set(keep + tail)):
        if prev is not None and i != prev + 1:
            out.append("…")
        out.append(lines[i])
        prev = i
    text = "\n".join(out)
    return text if len(text) <= PER_FILE else "…" + text[-PER_FILE:]


def evidence(dirs: list[Path], since: float) -> tuple[str, list[str]]:
    """Excerpts of the most recently changed text files under `dirs`, as one block, and their names."""
    files = sorted(_text_files(dirs, since), key=lambda t: -t[0])
    parts, names, total = [], [], 0
    for _, p, root in files:
        if len(names) >= MAX_FILES or total >= TOTAL:
            break
        text = _read_tail(p)
        if not text or not text.strip():
            continue
        name = p.relative_to(root).as_posix()
        block = f"--- {name}\n{excerpt(text)}"
        parts.append(block[: TOTAL - total])
        names.append(name)
        total += len(parts[-1])
    return "\n".join(parts), names
