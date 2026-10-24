# Mailbox MCP server

Async messages and a shared state board between coding agents that may not be running at the same time. Storage is the `mailbox` branch of this repo, written through the GitHub API, so there is nothing to host. Each message is its own file, so concurrent writers never conflict.

## Tools

| Tool | Purpose |
|---|---|
| `send_message(to, kind, subject, body?, reply_to?, refs?)` | `to` is an agent name or `"all"`; `kind` is `info`, `question`, `request`, `decision` or `blocker`. |
| `read_messages(unread_only=true, from_agent?, kind?, limit=20)` | Messages to you or `"all"`, oldest first; unread ones returned are marked read. |
| `get_state(key?)` | Exact key, a prefix like `"status/*"` (with values), or omit to list all keys. |
| `set_state(key, value)` | Overwrite a key; refuses if you haven't read it or it changed since you did. |

## Identity

Your agent name defaults to your GitHub login, lowercased (e.g. `johnheibel`, `nazeemkamel1`). Set `MAILBOX_AGENT` to override, e.g. `johnheibel-codex` when running Codex alongside Claude Code.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (installs the `mcp` dependency automatically on first run)
- GitHub auth: `gh auth login`, or `GITHUB_TOKEN` with repo access

## Setup

**Claude Code:** nothing to do. `.mcp.json` registers the server for this project, and `.claude/settings.json` adds a session-start hook that reports unread messages. Approve the `mailbox` server if prompted.

**Codex:** register it once, with the absolute path to your checkout:

```bash
codex mcp add mailbox --env MAILBOX_AGENT=<github-login>-codex -- uv run --quiet --script /ABSOLUTE/PATH/TO/repo/tools/mailbox/server.py
```

**Check it from a shell:**

```bash
uv run --quiet --script tools/mailbox/server.py status
```

## Config

| Env var | Default |
|---|---|
| `MAILBOX_REPO` | `JohnHeibel/self-instrumenting-harness` |
| `MAILBOX_BRANCH` | `mailbox` |
| `MAILBOX_AGENT` | GitHub login |
