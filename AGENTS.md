# Agent instructions

Two people work on this repo, each with their own coding agents (Claude Code, Codex). Agents coordinate through the **mailbox** MCP server (`tools/mailbox/`), which stores messages and a shared state board on the `mailbox` branch of this repo. The other agent may not be running, so the mailbox is asynchronous: write what they need to know, and read what they left for you.

**Who does what:** `docs/TASKS.md` is the task board, maintained by john-mac. Work only on tasks assigned to you there, one at a time, and name the task ID in your status.

## Mailbox protocol

- **At session start:** call `read_messages` and `get_state("status/*")` before doing anything else. Act on any `request`, `question` or `blocker` addressed to you, or say why you can't.
- **Keep your status current:** `set_state("status/<you>", ...)` with what you're working on, what you just finished, and what you need. Your name is `you_are` in any mailbox response.
- **Messages are for events; the board is for current truth.**
  - Send a message when something happened that another agent depends on: a changed interface, a decision, a finished piece, a blocker, a question. Include `refs` (files, commit SHAs).
  - Put things both sides must agree on in state keys: `decisions`, `interfaces/<name>` (e.g. `interfaces/event-log-schema`), `blockers`, `status/<agent>`.
- **Before changing a shared interface** (anything under `interfaces/`), message the other agent with `kind: "request"` and wait for agreement unless it's backward compatible.
- `set_state` refuses to overwrite a key you haven't read or that changed since you read it. Re-read, merge, and set again; never work around it.
- Don't put secrets in the mailbox.

## Generality rule (applies to every change)

The harness is the product; Factorio is only the first host. Before finishing any change to the harness (tool server, runner, agent image, event log, UI), ask: **would this work unchanged if the host were Kerbal Space Program, Excel, a web app, or a program nobody has seen before?**

- **No host names, file paths, window titles, key bindings or game concepts in harness code, defaults or tool descriptions.** All of it belongs in `hosts/<host>.toml` (data) or in what the agent writes itself.
- **Human-written host-specific code is allowed only in measuring instruments:** the grader, test fixtures, fake runs and goal files. It never goes on the agent's path to the host.
- **Tests exercise the generic path with a host that isn't a game.** If a feature only works because of something Factorio happens to do, the design is wrong.
- When a host needs something the harness can't express generically, add a generic capability with the host's need as data. Don't add a special case.

## Project context

See `docs/BRIEF.md` for goals and decisions.
