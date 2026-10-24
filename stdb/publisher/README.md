# Event-log publisher

Tails a run directory and publishes its event-log records (and `shots/` thumbnails, and the `live/latest.jpg` spectator frame) to the SpacetimeDB module over HTTP. Node 22+, TypeScript via `tsx`, no SDK.

Everything about running it, including john-pc on Windows (binding to Tailscale, the owner identity, `grant_role`, `--forever`, `run-forever.cmd`, Task Scheduler), is in [`../README.md`](../README.md). Contract: [`../SCHEMA.md`](../SCHEMA.md).

    npm install
    npm run publisher -- --run-dir <run dir> [--once] [--from-start] [--live-only] [--forever]
    npm test            # 35+ tests, no server
    npm run typecheck
