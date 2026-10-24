# Factorio host: launch setup and `restart` reload action

**UNTESTED. Written from documentation only, on a Mac with no Factorio. John's Windows PC (`john-pc`) must verify everything below.**
Nothing here has been run, not even the PowerShell (no PowerShell on the machine that wrote it).

## Short answers

1. **Does RCON work with the normal GUI client hosting a game?** Not by default, and not from the command line.
   RCON is a server feature: `--start-server <save> --rcon-port <p> --rcon-password <pw>` (or `--rcon-bind <addr>:<port>`).
   The wiki's Multiplayer page says `--start-server` starts a "Headless (Dedicated) server, with no GUI" ([Command line parameters](https://wiki.factorio.com/Command_line_parameters)).
   **One correction to the working assumption:** there is a hidden way to get RCON on a GUI-hosted game. `config.ini` has `local-rcon-socket` and
   `local-rcon-password` (under `[other]`, e.g. `local-rcon-socket=127.0.0.1:27015`) whose comments say they apply "when launching MP server from the menu".
   Found only through search summaries of forum threads and a third-party project, not an official page: [forum t=31962](https://forums.factorio.com/viewtopic.php?t=31962),
   [t=102576](https://forums.factorio.com/viewtopic.php?t=102576), [p=687213](https://forums.factorio.com/viewtopic.php?p=687213) (asks for a command-line way to host a
   GUI game, so none existed), [factorioMCP PR #1](https://github.com/menbou404/factorioMCP/pull/1) (reports it working on 2.0.77 for a GUI-hosted game).
   It still needs someone to click Multiplayer, Host Saved Game, Load in the menu, so it cannot be scripted. That is why this design uses a dedicated server.
2. **Recommended pattern: dedicated server plus GUI client.** The same `factorio.exe` runs the server hidden (`--start-server-load-latest`) and the client joins with
   `--mp-connect 127.0.0.1:34197`. A restart is then: save over RCON, stop the server, start it again, relaunch the client. No menu clicking.
3. **Lighter than a restart:** maybe, unverified. See "Lighter reloads".

## Launch commands (what `restart.ps1` runs)

Server (no window; own user-data dir so it does not collide with the client):

```
factorio.exe --start-server-load-latest --config "%LOCALAPPDATA%\factorio-harness\config.ini"
  --server-settings "%LOCALAPPDATA%\factorio-harness\server-settings.json"
  --mod-directory "%APPDATA%\Factorio\mods" --port 34197
  --rcon-bind 0.0.0.0:27015 --rcon-password <FACTORIO_RCON_PASSWORD>
  --console-log "%LOCALAPPDATA%\factorio-harness\factorio-server.log"  [--enable-lua-udp <port>]
```

Client (the window the agent screenshots): `factorio.exe --mp-connect 127.0.0.1:34197`

One-time save creation: `factorio.exe --config "%LOCALAPPDATA%\factorio-harness\config.ini" --create "%LOCALAPPDATA%\factorio-harness\saves\harness.zip"`
(run `restart.ps1` once first so `config.ini` exists; it will stop with exit 3 and print this).

### Paths on Windows (Steam)
- Install: `C:\Program Files (x86)\Steam\steamapps\common\Factorio` (exe in `bin\x64\factorio.exe`); override with `FACTORIO_EXE`.
- User data: `%APPDATA%\Factorio` holds `mods`, `saves`, `script-output`, `config`, logs ([Application directory](https://wiki.factorio.com/Application_directory)).
- The harness `install_dir` (`%APPDATA%/Factorio/mods`) is that mods folder. The server reads it via `--mod-directory` (the wiki says only "Mod directory to use"), the client reads it by default, so both see the agent's mod.
- **`script-output` for the server is `%LOCALAPPDATA%\factorio-harness\script-output`**, not `%APPDATA%\Factorio\script-output`, because the server has its own write-data. Any helper that reads `helpers.write_file` output must be pointed at the right folder. This is a real consequence of the two-instance design; confirm it.

### Why a separate write-data for the server
Two Factorio instances sharing one user-data folder conflict over the lock file and the `temp\currently-playing` folder. Community guidance is a second `config.ini` with a different
`write-data`, passed with `--config` ([forum t=101948](https://forums.factorio.com/viewtopic.php?t=101948), [t=45211](https://forums.factorio.com/viewtopic.php?t=45211),
[Steam guide 2843949020](https://steamcommunity.com/sharedfiles/filedetails/?id=2843949020)); one post doubts a second copy from the same Steam install will run. Unverified on the current version.
`restart.ps1` generates a minimal `config.ini` (`[path]` with `read-data` and `write-data`); check that it is enough.

### server-settings.json
Template in this folder (copied once to the harness dir). Autosave is set in the file (`autosave_interval` minutes, `autosave_slots`); the server also "saves on exit" when stopped gracefully
([Multiplayer](https://wiki.factorio.com/Multiplayer)), but we kill the process after an explicit save instead. Field names are from memory and unchecked against 2.0. Decision to make: `auto_pause: true` (default) pauses the game while no client is connected, so ticks stop during the restart and while the agent has no client.

## Restart flow (`restart.ps1`)
1. If a server is running: RCON `/server-save` (admin-only console command, [Console](https://wiki.factorio.com/Console)), wait until the newest `.zip` in the saves folder is new and stops growing (90 s). If RCON fails or no save appears: exit 4, nothing killed. Alternative from Lua: `game.server_save()` (multiplayer only, per our notes in `docs/research/factorio.md`).
2. Stop the server and any other `factorio.exe` (the client), by process kill after the save.
3. Start the server from the latest save with the flags above.
4. Poll RCON (`/time`) up to 120 s. Exit 5 with the log tail if it never answers.
5. Relaunch the client with `--mp-connect`. Not waited on; connection is not verified.

Idempotent: with nothing running it only starts things. Exit codes: 0 ok, 2 setup (env var, exe), 3 no save, 4 save failed or RCON unreachable (nothing killed), 5 server failed, 6 client failed, 7 RCON command failed (`rcon.ps1`).
Switches: `-NoClient`, `-NoSave`.

**Does the client need relaunching when the server restarts?** Assumed yes (the connection dies with the server, and the client lands on the main menu). The script relaunches it, so this is safe either way, but it is a heavy step for a screenshot window. Verify whether "reconnect" from the menu would do, or whether the old client can stay.

## RCON: port, password, grader
- One RCON listener per server, so the agent's helpers and the grader share the same port (default TCP 27015) and password. Factorio RCON can run arbitrary Lua (`/c`), which is the reward-hacking channel noted in `docs/research/factorio.md` (Prime Agent). The agent is told about it only as policy; the grader should log nothing it does not need. If this matters, consider giving the agent a read-only wrapper later (not designed here).
- Password: environment variable `FACTORIO_RCON_PASSWORD`, never in the repo. Set it as a user environment variable on john-pc, start the tool server from a shell that has it (reload commands inherit its environment) and give the grader the same variable. Port: `FACTORIO_RCON_PORT`.
- The password is passed on the command line (`--rcon-password`), so it is visible to other local processes. Acceptable on a single-user PC; there is no documented password-file option for the server.
- Binding `0.0.0.0` (`FACTORIO_RCON_BIND`) lets Docker's `host.docker.internal` reach it. Add a Windows Firewall rule limiting 27015 to the Docker/loopback range, since RCON is an admin shell. Whether Docker Desktop traffic arrives as loopback or another interface on John's setup is unknown.
- `--enable-lua-udp`: off unless `FACTORIO_LUA_UDP_PORT` is set. `helpers.send_udp` goes to localhost only, so a container cannot receive it without extra forwarding; prefer RCON or files.
- Crash report for RCON commands sent during tick 0 on 2.0.49 ([forum 128760](https://forums.factorio.com/128760), via search); the script waits for `/time` to answer but not for tick > 0.

## Lighter reloads (all unverified)
- `game.reload_script()`: "Forces a reload of the scenario script from the original scenario location". Our notes record no multiplayer restriction. Whether it re-runs a mod's `control.lua` is unknown. A commented `reload_script` action in `hosts/factorio.toml` runs it over RCON (`rcon.ps1`). It stays off because it exits 0 even if nothing reloaded.
- `game.reload_mods()`: does nothing in multiplayer.
- Save reload without quitting the process: none known for a server. Data-stage changes need the full restart regardless.
- If `reload_script` does not re-run mod `control.lua`, the restart action is the only reliable way, and the agent can also just edit and restart less often.

## Verify on john-pc (checklist)
1. `FACTORIO_RCON_PASSWORD` set; `restart.ps1` creates `config.ini` and `server-settings.json`; the `--create` command makes a save (exit 3 message is accurate).
2. Server starts hidden with `--config` pointing at the generated file (no "lock" error while the Steam client is open); mod folder is picked up; `--rcon-bind` accepted.
3. `rcon.ps1 -Command "/time"` returns text; my packet handling works (empty reply behaviour, auth response order).
4. From the Docker container, `host.docker.internal:27015` reaches RCON (firewall rule too).
5. `/server-save` via RCON writes a new zip into the harness `saves` folder; the "stops growing" wait is right.
6. Killing the process after the save loses nothing; the latest save loads (not an older autosave).
7. Client with `--mp-connect` joins; it works launched directly from `factorio.exe` while Steam runs; `allow_commands: admins-only` behaves as expected for the client player.
8. Full cycle through the toolserver: `reload: "restart"` in an `extension.json`, within `reload_timeout_s` (300), relative script path resolved (tool server cwd = repo root).
9. Whether the client must reconnect; whether `script-output` lands where helpers expect.
10. Test `reload_script` (plumbing test b) and fill in whether mod `control.lua` re-runs.
11. Whether the server-side `/c` and RCON disable achievements (irrelevant for scoring, but record it).

## Sources
Official wiki: [Command line parameters](https://wiki.factorio.com/Command_line_parameters), [Multiplayer](https://wiki.factorio.com/Multiplayer), [Console](https://wiki.factorio.com/Console),
[Application directory](https://wiki.factorio.com/Application_directory). The wiki RCON page (`wiki.factorio.com/RCON`) timed out and `forums.factorio.com` could not be fetched directly,
so forum claims above come from search-result summaries, not from reading the threads.
