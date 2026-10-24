"""Append-only JSONL event log. Schema: docs/interfaces/event-log.md."""

from __future__ import annotations

import contextvars
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = 1


class _Result:
    """Filled in by a tool while it runs; written when the call ends."""

    def __init__(self) -> None:
        self.kind = "none"
        self.nbytes = 0
        self.extra: dict[str, Any] = {}
        self.artifact: Callable[[int], str] | None = None

    def set(self, kind: str, nbytes: int, **extra: Any) -> None:
        self.kind, self.nbytes, self.extra = kind, nbytes, extra

    def save_artifact(self, save: Callable[[int], str]) -> None:
        """`save(seq)` writes a file named after the record's seq and returns its path
        relative to the run dir; it runs when the record is written."""
        self.artifact = save


# Set while a run_script script calls a built-in tool, so its record says via="script".
VIA: contextvars.ContextVar[str | None] = contextvars.ContextVar("via", default=None)


class EventLog:
    def __init__(self, path: Path, run_id: str, writer: str = "toolserver") -> None:
        self.path = path
        self.run_id = run_id
        self.writer = writer
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A restarted writer appends to its own file, so continue its numbering.
        self._seq = _resume(path)

    def write(self, type_: str, _artifact: Callable[[int], str] | None = None, **fields: Any) -> None:
        with self._lock:
            self._seq += 1
            if _artifact:
                try:
                    fields["artifact"] = _artifact(self._seq)
                except Exception as e:  # a lost thumbnail must not lose the record
                    fields["artifact"], fields["artifact_error"] = None, f"{type(e).__name__}: {e}"
            record = {
                "v": SCHEMA_VERSION,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "run_id": self.run_id,
                "writer": self.writer,
                "seq": self._seq,
                "type": type_,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @contextmanager
    def call(self, tool: str, channel: str, args: dict[str, Any], **fields: Any) -> Iterator[_Result]:
        """Log one tool call: ok or error, duration, and what it returned."""
        result = _Result()
        if VIA.get() and "via" not in fields:
            fields = {**fields, "via": VIA.get()}
        t0 = time.perf_counter()
        try:
            yield result
        except Exception as e:
            self.write("tool_call", tool=tool, channel=channel, args=args, ok=False,
                       error=f"{type(e).__name__}: {e}", duration_ms=_ms(t0), **result.extra, **fields)
            raise
        self.write("tool_call", tool=tool, channel=channel, args=args, ok=True, error=None,
                   duration_ms=_ms(t0), result_kind=result.kind, result_bytes=result.nbytes,
                   _artifact=result.artifact, **result.extra, **fields)


def _resume(path: Path) -> int:
    """Last seq in an existing file. Ends a torn last line (from a crash) so the next
    record starts on its own line."""
    if not path.exists() or path.stat().st_size == 0:
        return 0
    last = 0
    with path.open("rb") as f:
        data = f.read()
    for line in data.splitlines():
        try:
            last = max(last, int(json.loads(line).get("seq", 0)))
        except (ValueError, AttributeError):
            continue
    if not data.endswith(b"\n"):
        with path.open("ab") as f:
            f.write(b"\n")
    return last


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)
