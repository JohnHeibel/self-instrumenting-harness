# Factorio host: launch setup and `restart` reload action

**UNTESTED. Written from documentation only, on a Mac with no Factorio. John's Windows PC (`john-pc`) must verify everything below.**
Nothing here has been run, not even the PowerShell (no PowerShell on the machine that wrote it).

**Constraint (John):** the harness launches Factorio only through Steam, `steam://rungameid/427520`. Running the Steam copy of `factorio.exe` with arguments triggers Steam's "custom arguments" prompt, which blocks unattended restarts. Arguments are set once in Steam: Factorio, Properties, Launch Options.

## Can one Steam launch be both the visible game and an RCON endpoint?

RCON belongs to a server: `--start-server` / `--start-server-load-latest` with `--rcon-port`/`--rcon-bind` and `--rcon-password` ([Command line parameters](https://wiki.factorio.com/Command_line_parameters): `--start-server` is "a Headless (Dedicated) server, with no GUI" per the Multiplayer page). A `--start-server` Launch Option would make the Steam launch headless, so no window. Two ways around it, both leaving Steam as the only thing that starts the Steam copy:

| | A. Headless server (separate install) + Steam client | B. Steam game hosts the save itself |
|---|---|---|
| Steam Launch Options | `--mp-connect 127.0.0.1:34197` | `--host "C:\Users\<USER>\AppData\Roaming\Factorio\saves\harness.zip"` |
| RCON | server flags `--rcon-bind`, `--rcon-password` (documented) | `local-rcon-socket` / `local-rcon-password` in `config.ini` (forum-only, hidden) |
| Extra install | headless zip from factorio.com, unzipped to `C:\factorio-headless` (not Steam, so no Steam prompt; unverified) | none |
| Sources | wiki: `--start-server*`, `--rcon-*`, `--mp-connect` | wiki lists `--host FILE` "Start hosting a multiplayer game" and `--host-interactive FILE`; RCON keys from [forum t=31962](https://forums.factorio.com/viewtopic.php?t=31962), [t=102576](https://forums.factorio.com/viewtopic.php?t=102576), [p=687213](https://forums.factorio.com/viewtopic.php?p=687213), [factorioMCP PR #1](https://github.com/menbou404/factorioMCP/pull/1) (reports it working for a GUI-hosted game on 2.0.77) |
| Unknowns | Is the standalone headless build free of the Steam prompt? Launch Options can hold only one string, so the client is the only Steam launch. | Does `--host` open the normal window and honor `config.ini` RCON? Does `--host` take a path, a bare name? The wiki gives one line each. |

**Recommendation: A**, because every flag is on the official wiki and only the client is a Steam launch. **B is the fallback** (`FACTORIO_MODE=host`), with no second install, if john-pc finds A's server trips Steam or `--host` just works. Not chosen: running the Steam `factorio.exe` directly as the server (that is the prompt John hit); UDP or `script-output` as the grader channel (`helpers.send_udp` is localhost-only and needs `--enable-lua-udp`; script output is write-only from the game, and would still need the game to be running as a host; RCON is the only request/response channel).

### Exact Launch Options string for John
- **Mode A (recommended):** `--mp-connect 127.0.0.1:34197`
- **Mode B:** `--host "C:\Users\<USER>\AppData\Roaming\Factorio\saves\harness.zip"` (replace `<USER>`; Steam does not expand `%APPDATA%`). Try the bare form `--host harness.zip` only if the absolute one fails.

## Commands behind the scenes
Mode A server (started by `restart.ps1`, hidden, headless exe, own write-data so it does not clash with the client):
```
C:\factorio-headless\bin\x64\factorio.exe --start-server-load-latest --config "%LOCALAPPDATA%\factorio-harness\config.ini"
  --server-settings "%LOCALAPPDATA%\factorio-harness\server-settings.json" --mod-directory "%APPDATA%\Factorio\mods"
  --port 34197 --rcon-bind 0.0.0.0:27015 --rcon-password <FACTORIO_RCON_PASSWORD> --console-log ...\factorio-server.log [--enable-lua-udp <port>]
```
Client: `Start-Process steam://rungameid/427520` (Launch Options add `--mp-connect`).
Mode B: the script writes `local-rcon-socket=0.0.0.0:27015` and `local-rcon-password=<env value>` into `[other]` of `%APPDATA%\Factorio\config\config.ini` while the game is stopped (a running game may overwrite external edits), then opens the Steam URL.

One-time save for mode A: run `restart.ps1` once (creates the config; it exits 3 and prints the command), then `"C:\factorio-headless\bin\x64\factorio.exe" --config "%LOCALAPPDATA%\factorio-harness\config.ini" --create "%LOCALAPPDATA%\factorio-harness\saves\harness.zip"`. For mode B put the save at `%APPDATA%\Factorio\saves\harness.zip`.

### Paths (Windows, Steam)
User data `%APPDATA%\Factorio`: `mods`, `saves`, `script-output`, `config` ([Application directory](https://wiki.factorio.com/Application_directory)). The harness `install_dir` is that `mods` folder; the client reads it by default and the mode-A server through `--mod-directory`. **In mode A the server's `script-output` is `%LOCALAPPDATA%\factorio-harness\script-output`**, not `%APPDATA%\Factorio\script-output`: the server has its own write-data (avoids the lock/`currently-playing` clash of sharing one user-data folder: [t=101948](https://forums.factorio.com/viewtopic.php?t=101948), [t=45211](https://forums.factorio.com/viewtopic.php?t=45211), [Steam guide](https://steamcommunity.com/sharedfiles/filedetails/?id=2843949020)). In mode B everything is in `%APPDATA%\Factorio`.

### server-settings.json (mode A)
Template here, copied once. Autosave interval and slots are set in the file; the server also "saves on exit" ([Multiplayer](https://wiki.factorio.com/Multiplayer)) but we kill the process only after an explicit `/server-save`. Field names are from memory, unchecked against 2.0. Decide `auto_pause` (default true pauses the game while no client is connected). Whether the Launch Options `--mp-connect` can join a server whose `require_user_verification` is false without a login is untested.

## Restart flow (`restart.ps1`)
1. If the game is running: RCON `/server-save` (admin-only console command, [Console](https://wiki.factorio.com/Console)); wait up to 90 s until the newest save is new and stops growing. If RCON fails or no save appears: exit 4, nothing killed.
2. Stop the game (server, plus the Steam client in mode A) with a process kill after the save.
3. Mode A: start the server from the latest save; mode B: copy the newest save over `harness.zip` if a newer autosave exists, write the RCON keys, open the Steam URL.
4. Poll RCON `/time` (120 s mode A, 180 s mode B); exit 5 if it never answers.
5. Mode A: open the Steam URL for the client (not verified to connect).
Exit codes: 0 ok, 2 setup, 3 no save, 4 save failed or RCON unreachable (nothing killed), 5 game/server failed, 6 Steam launch failed, 7 `rcon.ps1` command failed. Switches `-NoClient`, `-NoSave`. Idempotent: run twice and the second just saves and restarts again. The client is assumed to need a relaunch after a server restart (verify whether reconnecting from the menu would do).

## RCON: port, password, grader
- One RCON listener, shared by the agent's helpers and our grader: TCP 27015 (`FACTORIO_RCON_PORT`), password from the environment variable `FACTORIO_RCON_PASSWORD`, never in the repo. Set it as a user environment variable on john-pc, start the tool server from a shell that has it (reload commands inherit its environment), give the grader the same variable.
- Mode A passes the password on the command line (visible to local processes); mode B writes it in plaintext into `config.ini` in the user profile. OK for a single-user PC, noted.
- Bind `0.0.0.0` (`FACTORIO_RCON_BIND`) so Docker's `host.docker.internal` can reach it; add a Windows Firewall rule limiting 27015 to Docker/loopback, since RCON can run any Lua (`/c`), the reward-hacking channel from `docs/research/factorio.md`.
- `--enable-lua-udp` is off unless `FACTORIO_LUA_UDP_PORT` is set; `helpers.send_udp` is localhost-only.
- RCON during tick 0 crashed 2.0.49 ([forum 128760](https://forums.factorio.com/128760), from search); the script waits for `/time` but not for tick > 0.

## Start save: avoiding the freeplay crash-site intro
john-pc found the crash-site intro blocks the inventory key until Tab is pressed. Do not start runs from a fresh map. Make the start save once, after the intro: join the game, press Tab, confirm the inventory key works, `/server-save` (mode A) or save (mode B) to `harness.zip`, and keep a copy as the task's starting state. Unverified alternative: skip the intro in a tiny scenario or mod via freeplay's remote interface (`remote.call("freeplay", "set_skip_intro", true)` and `set_disable_crashsite`, from memory of the Lua API docs), set before the first player spawns. Whatever is chosen, check the start save is not marked as already in the intro.

## Lighter reloads (all unverified)
- `game.reload_script()`: "Forces a reload of the scenario script from the original scenario location". Unknown whether it re-runs a mod's `control.lua`. A commented `reload_script` action in `hosts/factorio.toml` runs it over RCON (`rcon.ps1`); left off because it exits 0 even if nothing reloaded.
- `game.reload_mods()`: does nothing in multiplayer. No known save reload without quitting the process. Data-stage changes need the full restart regardless.

## Verify on john-pc
1. Which mode works: does the standalone headless exe run with the Steam client open and without the Steam prompt (A)? Does `--host` open the window, and does `config.ini` RCON apply (B)? What exact `--host` path form is accepted?
2. `FACTORIO_RCON_PASSWORD` set; `restart.ps1` makes its config files; `rcon.ps1 -Command "/time"` works (packet handling, empty replies); same from the Docker container at `host.docker.internal:27015` (firewall).
3. `/server-save` writes a new zip; kill after save loses nothing; the latest save is what loads.
4. The Steam client with the Launch Options joins; whether it needs relaunching after a restart.
5. Full cycle through `load_extension` with `"reload": "restart"` inside `reload_timeout_s` (300), relative script path resolved (tool server cwd = repo root).
6. Start save has no intro blocking; `script-output` lands where helpers read it.
7. Plumbing tests (a) state out and (b) reload; test `game.reload_script()` before enabling it.

## Sources
Official wiki: [Command line parameters](https://wiki.factorio.com/Command_line_parameters), [Multiplayer](https://wiki.factorio.com/Multiplayer), [Console](https://wiki.factorio.com/Console), [Application directory](https://wiki.factorio.com/Application_directory). The wiki RCON page timed out and `forums.factorio.com` could not be fetched, so forum claims come from search summaries.
