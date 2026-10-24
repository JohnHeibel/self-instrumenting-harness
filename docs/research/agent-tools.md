# How Codex and Claude Code expose our tools

Research done 2026-10-23 by subagents reading official docs and, for Codex, the source at `rust-v0.164.0` (github.com/openai/codex). Items marked **unverified** were read from code or docs but not run.

## Codex (first agent)

### Settings our harness server needs

```toml
[features]
mcp_tool_list_changed = true          # off by default, marked under development
suppress_unstable_features_warning = true

[mcp_servers.harness]
url = "http://host.docker.internal:<port>/mcp"   # streamable HTTP; agent is in Docker, server on host
startup_timeout_sec = 30
tool_timeout_sec = 300
default_tools_approval_mode = "approve"          # otherwise un-annotated tools count as destructive and need approval
omit_tools_from = ["deferred", "code_mode"]      # unverified: should make hooks real top-level tools
```

Run Codex inside the container with `--dangerously-bypass-approvals-and-sandbox` (or `sandbox_mode = "danger-full-access"` plus `approval_policy = "never"`). The container is the sandbox; Codex's own bubblewrap sandbox often fails inside Docker anyway.

### Findings

- **Hidden by default.** All bundled models set `supports_search_tool: true`, so MCP tools stay hidden until the model calls `tool_search`. Most current models (gpt-5.6-\*, gpt-6-\*) are also `code_mode_only`: MCP tools are called from inside a JS `exec` tool rather than as native function calls (`gpt-5.5` is the exception). The fix is `omit_tools_from` above, or `[features.code_mode] direct_only_tool_namespaces = ["mcp__harness"]` (unverified). The server may override the bundled model metadata, so check the live `tool_mode` at runtime.
- **New tools mid-session.** This works with `mcp_tool_list_changed = true`. A Codex integration test shows a newly added tool appearing on the next model request within the same turn and being called. Without the flag, the notification is only logged. Fallbacks: a stable `call_hook(name, args)` + `list_hooks()`, or the app-server's `config/mcpServer/reload` (unverified for an unchanged server).
- **App-server dynamic tools** (`dynamicTools` on `thread/start`, experimental) are fixed when the thread starts and can't be added later. MCP is the way to grow the toolset.
- **Names:** exposed under namespace `mcp__<server>`. Characters outside `[A-Za-z0-9_]` become `_`. Namespace plus name is capped at 128 chars (longer names are truncated with a hash suffix).
- **Limits:**
  - Up to 2,048 tools per server. Input schemas over 5,000 bytes are compacted.
  - Tool output is truncated to 10,000 tokens by default (`tool_output_token_limit`, or per-tool `output_token_limit`).
  - Default timeouts in the source are 30 s startup and 300 s per call; the docs say 10 s and 60 s. Set both explicitly.
- **Images:** an MCP `image` result reaches the model as an image (detail `high` by default; set it with `_meta["codex/imageDetail"]`). Screenshots work.
- **Driving one long session:** use `codex app-server` (JSONL over stdio) or the Python SDK (`openai-codex`) that wraps it.
  - One thread for the whole run. `turn/start` sends new input, and `turn/steer` injects into a running turn.
  - `thread/tokenUsage/updated` reports tokens, and per-turn usage is available.
  - Auto-compaction triggers at about 90% of a 272k window.
  - `codex exec --json` plus `codex exec resume <id>` is simpler, but it restarts MCP servers every turn.
- **Logging:**
  - Codex hooks are on by default. `PreToolUse`/`PostToolUse` with matcher `mcp__harness__.*` get `tool_input` and `tool_response`. Headless runs need `--dangerously-bypass-hook-trust`.
  - App-server item events also carry the full arguments and results.
  - `notify` fires only at turn end, so it can't log individual tool calls.

## Claude Code (later run)

- MCP tools reach the model as real tools named `mcp__<server>__<tool>`.
- `tools/list_changed` is honored mid-session, including in headless and SDK sessions.
- Tool search defers MCP tools by default. Set `"alwaysLoad": true` on the harness server, or `ENABLE_TOOL_SEARCH=false`.
- Image results reach the model.
- `MAX_MCP_OUTPUT_TOKENS` defaults to 25,000. Text results over 50,000 characters are saved to a file.
- To drive one long session, use the Agent SDK in streaming-input mode (`ClaudeSDKClient`). It handles resume, auto-compaction (`compact_boundary` events) and usage reporting.
- A `PreToolUse`/`PostToolUse` hook with matcher `*` logs every call.

## Design consequences

1. The tool server speaks MCP over streamable HTTP on one port, reachable from the agent container at `host.docker.internal`.
2. Each loaded hook is registered as a named MCP tool, and the server sends `tools/list_changed`. `call_hook` and `list_hooks` stay as a fallback.
3. Every hook tool is annotated (`readOnlyHint` where true) and the server is configured to auto-approve, so calls aren't denied.
4. The runner drives Codex through the app-server or Python SDK on one thread, and records per-turn token usage for the cost curve.
5. **First test on the PC:** confirm that on the chosen model, a hook tool added mid-session appears as a direct, top-level tool, not hidden behind search and not inside code mode.
