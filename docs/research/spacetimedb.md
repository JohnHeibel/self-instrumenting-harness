# SpacetimeDB for the UI backend

Research done 2026-10-23 against SpacetimeDB v2.13.0 (released 2026-10-21): the official docs at spacetimedb.com/docs and the source at github.com/clockworklabs/SpacetimeDB.

## What we use
- **Module in TypeScript.** It's first-class now, so the module, the publisher and the UI share one language. A module declares `table(...)`s and `reducer(...)`s with `spacetimedb/server`.
- **Browser client:** the npm package `spacetimedb`, with React hooks (`SpacetimeDBProvider`, `useTable`, `useReducer`). Typed bindings come from `spacetime generate --lang typescript`. A subscription loads the matching rows, then streams inserts, updates and deletes.
- **Publisher:** Node 22 with the same TS SDK. There is no Python SDK; the HTTP fallback is `POST /v1/database/<db>/call/<reducer>` with a bearer token.
- **Hosting:**
  - **Development:** a local instance, `spacetime start`, listening on `0.0.0.0:3000`.
  - **Demo:** Maincloud. Sign-up is required (`spacetime login`), then `spacetime publish <db> --server maincloud`. The free plan allows about 3M calls a month, about 1 GB of storage and about 12.5 GB of egress. Databases pause after a week idle.

## Constraints that shape the design
- **Subscription SQL** supports `SELECT *` with WHERE comparisons (timestamp ranges work) and at most one join on indexed columns. It has **no ORDER BY, LIMIT or COUNT**, so sorting and aggregates happen in the client, or in views.
- **Budget:** at 20 calls a second, the free plan's calls run out in about 1.7 days. The publisher **batches** records, one reducer call per second carrying an array.
- **Binary data:** `t.array(t.u8())` columns are supported, and 20–60 KB thumbnails per row are fine. But storage and egress add up fast (1 per second at 40 KB is about 3.4 GB a day). So thumbnails go in **their own table, fetched lazily by id**, and the publisher mirrors only a sampled subset.
- **Auth:** reducers see `ctx.sender`. Keep a private role table seeded from the owner in `init`. Only the publisher identity may insert records; only an ops identity may send control actions. Browsers get an anonymous identity and token automatically.
- **Migrations:** republishing keeps data. You may add tables, indexes and reducers, and add columns only at the end with a default. Removing or changing columns fails; `--delete-data` resets the database, which is fine in development.
- **CORS** is permissive on `/v1`. Reconnect support (`withAutomaticReconnect()`) exists in the TS client since 2.12.

## Unverified
- Cal Hacks 13.0 Spacetime prize criteria. Devpost was unreachable; check at the booth or on Discord.
- The HTTP reducer route's body limit (likely 2 MB).
- Windows firewall rules for exposing port 3000 to the Mac.
