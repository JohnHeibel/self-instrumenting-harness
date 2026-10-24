# Agent instructions

Two people work on this repo, each with their own coding agents (Claude Code, Codex). Agents coordinate through the **mailbox** MCP server (`tools/mailbox/`), which stores messages and a shared state board on the `mailbox` branch of this repo. The other agent may not be running, so the mailbox is asynchronous: write what they need to know, and read what they left for you.

## Mailbox protocol

- **At session start:** call `read_messages` and `get_state("status/*")` before doing anything else. Act on any `request`, `question` or `blocker` addressed to you, or say why you can't.
- **Keep your status current:** `set_state("status/<you>", ...)` with what you're working on, what you just finished, and what you need. Your name is `you_are` in any mailbox response.
- **Messages are for events; the board is for current truth.**
  - Send a message when something happened that another agent depends on: a changed interface, a decision, a finished piece, a blocker, a question. Include `refs` (files, commit SHAs).
  - Put things both sides must agree on in state keys: `decisions`, `interfaces/<name>` (e.g. `interfaces/event-log-schema`), `blockers`, `status/<agent>`.
- **Before changing a shared interface** (anything under `interfaces/`), message the other agent with `kind: "request"` and wait for agreement unless it's backward compatible.
- `set_state` refuses to overwrite a key you haven't read or that changed since you read it. Re-read, merge, and set again; never work around it.
- Don't put secrets in the mailbox.

## Project context

See `docs/BRIEF.md` for goals and decisions.
