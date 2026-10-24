"""Map `codex app-server` notifications to runner.jsonl records (docs/interfaces/event-log.md).

What the app-server gives us (Codex 0.164.0, checked against a recorded session):
- turn/started, turn/completed (turn.status: completed | interrupted | failed, turn.durationMs, turn.error)
- item/started, item/completed with item.type: userMessage, agentMessage (phase: commentary |
  final_answer), reasoning (summary: ["**Title**\\n\\ntext", ...] when model_reasoning_summary is
  set; content: raw reasoning, normally empty), mcpToolCall, commandExecution, fileChange,
  webSearch, imageView, sleep, contextCompaction, and others
- item/agentMessage/delta, item/reasoning/summaryTextDelta: streaming text (not logged; the
  completed item carries the full text)
- thread/tokenUsage/updated: tokenUsage.total (thread-wide, persists across resume) and
  tokenUsage.last (the latest model request), plus modelContextWindow
- error (with willRetry), warning, thread/status/changed, thread/compacted (deprecated)
"""

from __future__ import annotations

import re
from typing import Any

from toolserver.events import EventLog

USAGE_FIELDS = {"inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens",
                "outputTokens": "output_tokens", "reasoningOutputTokens": "reasoning_output_tokens"}
WAIT_TOOLS = {"wait", "sleep", "wait_for"}
ACTING_ITEMS = {"mcpToolCall", "commandExecution", "fileChange", "webSearch", "imageView",
                "dynamicToolCall", "collabAgentToolCall", "imageGeneration", "functionCallOutput"}
_TITLE = re.compile(r"^\s*\*\*(.+?)\*\*\s*\n*", re.S)


class Mapper:
    """Stateful: tracks token totals and the current agent_state so records aren't repeated."""

    def __init__(self, log: EventLog) -> None:
        self.log = log
        self.total: dict[str, int] = {}       # latest thread-wide usage from Codex
        self.context_window: int | None = None
        self.turn: int | None = None
        self.turn_id: str | None = None
        self._turn_base: dict[str, int] = {}
        self._state: tuple[str, str] | None = None
        self.last_error: str | None = None
        self.on_usage = lambda total: None  # the runner persists totals so a crash loses nothing

    # Called by the runner ------------------------------------------------------

    def state(self, state: str, text: str = "", **extra: Any) -> None:
        if (state, text) == self._state and not extra:
            return
        self._state = (state, text)
        self.log.write("agent_state", state=state, text=text, **extra)

    def begin_turn(self, turn: int, turn_id: str) -> None:
        self.turn, self.turn_id = turn, turn_id
        self._turn_base = dict(self.total)
        self.turn_base = self._turn_base
        self.last_error = None
        self.log.write("turn_started", turn=turn, turn_id=turn_id)
        self.state("thinking")

    def end_turn(self, status: str, duration_ms: int | None, error: str | None) -> dict:
        usage = self.turn_usage()
        self.log.write("usage", turn=self.turn, **usage, total_tokens=self.total.get("totalTokens", 0),
                       turn_total_tokens=self._delta("totalTokens"),
                       context_window=self.context_window)
        self.log.write("turn_completed", turn=self.turn, turn_id=self.turn_id, status=status,
                       duration_ms=duration_ms, error=error)
        self.turn, self.turn_id = None, None
        return usage

    def restore_turn(self, turn: int, turn_id: str, base: dict) -> None:
        """Reopen a turn a crashed runner left open, so end_turn can close it."""
        self.turn, self.turn_id, self._turn_base = turn, turn_id, dict(base)

    def turn_usage(self) -> dict[str, int]:
        return {ours: self._delta(theirs) for theirs, ours in USAGE_FIELDS.items()}

    def total_tokens(self) -> int:
        return self.total.get("totalTokens", 0)

    def _delta(self, key: str) -> int:
        return self.total.get(key, 0) - self._turn_base.get(key, 0)

    # Notifications ----------------------------------------------------------------

    def handle(self, msg: dict) -> None:
        method = msg.get("method", "")
        p = msg.get("params") or {}
        if method == "thread/tokenUsage/updated":
            tu = p.get("tokenUsage") or {}
            self.total = {k: int(v) for k, v in (tu.get("total") or {}).items() if isinstance(v, int)}
            self.context_window = tu.get("modelContextWindow") or self.context_window
            self.on_usage(self.total)
        elif method == "item/started":
            self._item_started(p.get("item") or {})
        elif method == "item/completed":
            self._item_completed(p.get("item") or {})
        elif method == "thread/compacted":
            self.state("thinking")
        elif method == "error":
            err = p.get("error") or {}
            self.last_error = err.get("message")

    def _item_started(self, item: dict) -> None:
        kind = item.get("type")
        if kind == "contextCompaction":
            self.state("compacting")
        elif kind == "sleep" or (kind == "mcpToolCall" and item.get("tool") in WAIT_TOOLS):
            self.state("waiting", _describe(item))
        elif kind in ACTING_ITEMS:
            self.state("acting", _describe(item))
        elif kind in ("reasoning", "agentMessage"):
            self.state("thinking")

    def _item_completed(self, item: dict) -> None:
        kind = item.get("type")
        if kind == "agentMessage":
            text = item.get("text") or ""
            if text:
                self.log.write("agent_message", text=text, phase=item.get("phase"))
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                title, text = split_summary(part)
                self.log.write("reasoning", title=title, text=text)
        if kind in ACTING_ITEMS or kind in ("sleep", "contextCompaction"):
            self.state("thinking")


def split_summary(part: str) -> tuple[str, str]:
    """Codex summaries look like '**Calculating products**\\n\\nI need to ...'."""
    m = _TITLE.match(part)
    if not m:
        return "", part.strip()
    return m.group(1).strip(), part[m.end():].strip()


def _describe(item: dict) -> str:
    kind = item.get("type")
    if kind == "mcpToolCall":
        return f"{item.get('server')}.{item.get('tool')}"
    if kind == "commandExecution":
        cmd = item.get("command") or ""
        return f"shell: {cmd[:120]}"
    if kind == "fileChange":
        return "editing files"
    if kind == "webSearch":
        return f"web search: {item.get('query') or ''}"[:120]
    if kind == "sleep":
        return f"sleep {item.get('durationMs')} ms"
    return kind or ""
