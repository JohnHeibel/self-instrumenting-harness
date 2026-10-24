# Wrapping Claude Code, Codex and Droid; the primitive layer

The BRIEF says to wrap Claude Code and Codex without modifying either, through one shared primitive server. Decided: both are supported from the first slice.

## The common denominator: MCP

Claude Code, Codex and Factory Droid all act as **MCP clients**. A single stdio or HTTP MCP server exposing the four primitives is the cleanest "one shared primitive server". The repo's `tools/mailbox/server.py` already shows the pattern (FastMCP, run with `uv run --script`).

Suggested primitive tools:
- `screenshot`
- `key` / `type` / `click` / `move` / `scroll`
- `read_docs(query|path)`
- `write_extension(path, content)` + `load_extension()`, where "load" is host-specific: Factorio = reload save; Odoo = module upgrade; KSP = restart
- `call_hook(name, args)`: invoke a hook the agent built (RCON command, HTTP endpoint, ...)

Every call gets logged with `source: hook | screenshot | input` for the coverage metric.

## Claude Code

- Headless: `claude -p` with `--output-format stream-json`. Or the **Claude Agent SDK** (Python/TS): the same agent loop, with sessions, resume, and automatic compaction.
- Hooks useful to us:
  - `PostToolUse`: log every tool call.
  - `PreCompact`: write a handoff before compaction; `postCompact` exists in Claude Code (SDK support unconfirmed).
  - `Stop`: save state on exit.
  - `SessionStart`: inject the hook-library index and broken-hook report. The mailbox already uses this.
- The SDK emits a compaction boundary message type (`SDKCompactBoundaryMessage`, TS reference; verify). The dashboard can mark compactions on the timeline.
- Note: Claude Code's built-in computer use is macOS-only in the CLI (per the winuse-mcp author). On John's Windows PC, screenshot and input must come from our own primitive server, which the BRIEF wants anyway.

## Codex

- `codex exec`: one-shot, JSONL on stdout, exit codes. Good for batch work.
- `codex app-server`: JSON-RPC over stdio (JSONL), durable **threads** (create, resume, fork, archive), and approval requests that pause a turn. It's the right fit for a long-running session. The protocol is marked experimental. The official Python SDK (`openai-codex`) drives app-server; there's also a TS SDK in openai/codex.
- MCP: Codex consumes MCP servers (`codex mcp add ...`, as in the mailbox README). Whether Codex can itself *be* an MCP server wasn't confirmed, and we don't need it to.
- Logging: Codex *does* have `PreToolUse`/`PostToolUse` hooks; my search missed them. See John's `agent-tools.md`, which is authoritative for Codex and Claude Code tool exposure. Logging at the primitive server still gives identical accounting across backends.

**Design consequence:** log at the primitive server as well as in backend hooks. Then Claude Code, Codex and Droid get identical coverage accounting, which honors "no host-specific or backend-specific code".

## Factory Droid (sponsor track, $2k)

- `droid exec`: headless one-shot, with autonomy levels and output formats `text|json|stream-json|stream-jsonrpc`. It has flags to enable, disable or restrict tools, and to append to the system prompt. Supports MCP and custom subagents ("Custom Droids").
- Factory's tutorial streams `droid exec` output to an app over SSE, a good model for our dashboard feed.
- What the sponsor wants: Droid doing real work in the running product; custom triggers; controlled permissions; visible plans, steps and diffs. A Droid backend that writes and repairs hooks fits if the README explains where Droid runs.

## Screenshot and input on Windows / Docker

Options found (licenses and maintenance not checked):
- **ComputerMate**: Linux desktop in Docker with VNC; tools for screenshot, click, scroll, type, keys. Fits "agent sandbox in Docker" if the game client also runs in that Linux desktop (see the Factorio screenshot constraint in `factorio.md`).
- **Computer Use MCP**: X11 + Windows 10/11 (PowerShell + user32).
- **winuse-mcp**: Windows-only, 14 tools, aimed at Claude Code.
- **MCPComputerUse**: C# self-contained exe for Windows.
- **Codex Computer Run**: C#, Win32, needs a signed-in desktop session (not Docker).

Writing our own thin version is probably right. It's 5 tools, it has to log in our format, and it has to target one window. Use one of the above as a reference.

## Sources
- Codex SDK: https://developers.openai.com/codex/sdk · app-server: https://www.infoq.com/news/2026/02/opanai-codex-app-server · https://www.promptfoo.dev/docs/providers/openai-codex-app-server/
- Codex Python SDK notes: https://codex.danielvaughan.com/2026/05/12/openai-codex-python-sdk-embedding-programmable-agent-runtime/
- Agent SDK hooks: https://platform.claude.com/docs/en/agent-sdk/hooks
- Droid exec: https://docs.factory.ai/cli/droid-exec/overview.md · tutorial https://docs.factory.ai/guides/building/droid-exec-tutorial.md
- Computer-use MCPs: https://glama.ai/mcp/servers/one710/computermate · https://glama.ai/mcp/servers/k7ksz484is · https://socket.dev/npm/package/windows-computer-use-mcp
