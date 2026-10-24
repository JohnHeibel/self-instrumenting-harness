# Requests from Nazeem for John (append-only)

Nazeem's branch (`nazeem/minecraft-host`) adds a Claude Code agent path next to the Codex one. Nothing in
`agent/` or `runner/src/` owned by john-pc was edited; everything new sits beside it. Items below are what
we'd like John to consider so the two paths converge. Append new entries at the bottom, dated.

## 2026-10-24

1. **New files outside our area (additive only).**
   - `agent/claude/` (Dockerfile, `claude.json`, `claude-settings.json`): Claude Code agent image,
     `harness-agent:claude-<version>`. Mirrors `agent/Dockerfile` and `agent/codex-config.toml`.
   - `runner/claude_runner.py`: a standalone runner for Claude Code. It imports `runner.agent`,
     `runner.hostcheck`, `runner.state` and `runner.toolserver_proc` unchanged and does not touch `runner.run`.
     Same run directory layout, same event log v1 records, STOP/PAUSE files and budget flags.
   Request: if you change the names or signatures of those four helper modules, tell us (or keep
   `agent.container_name`, `hostcheck.from_config`/`resolve`, `RunState`/`RunLock`, `ToolServer` stable).
2. **Runner generalization (optional).** `runner/src/runner/run.py` is tied to Codex's app-server.
   If you want one runner for both agents, the seam is a small "agent driver" interface: start session,
   send turn, stream events, interrupt, usage. `claude_runner.py` is a working second implementation of
   those pieces (one `claude -p [--resume <session>]` process per turn, usage from each assistant message).
3. **Event log `run_started.agent`** already lists `claude-code`; we write exactly that. We also write
   `usage.uncached_tokens` (as the Codex runner does) and `marker` kinds `compaction` and `mcp_problem`.
   Both are backward compatible; please add them to `docs/interfaces/event-log.md` if you agree.
4. **Credentials.** The Claude image takes `CLAUDE_CODE_OAUTH_TOKEN` through `docker run --env-file
   ~/.sisyphus/claude.env` (made with `claude setup-token`), the counterpart of bind-mounting
   `~/.codex/auth.json`. The runner only checks the file exists; it never reads it.
5. **Tool-server port.** The Codex image bakes port 8765 into `config.toml`. The Claude runner writes
   `<run>/mcp.json` with the real `--port` and mounts it, so the image's baked URL is only a default.
6. **P6 (container freedom)** is not done for the Claude image: it has passwordless sudo, but no egress
   proxy or resource caps. It should follow whatever P6 settles on for the Codex image.
