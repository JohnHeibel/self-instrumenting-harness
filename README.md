# Self-Instrumenting Agent Harness

Cal Hacks 13.0 project (October 23–25, 2026).

A layer that wraps Claude Code or Codex and builds its own interface to any program it's pointed at, then keeps improving that interface. The agent gets only screenshots, keyboard and mouse input, the host's docs, and the host's own extension system. It writes hooks into the host through that extension system, builds tools on top of them, and repairs them when the host changes.

Target hosts: Odoo (business software), modded Minecraft, Kerbal Space Program 1.

See [docs/BRIEF.md](docs/BRIEF.md) for the full project brief: goals, evaluation plan, prior art, sponsor fit, and open questions.

## Status

Architecture not yet designed.
