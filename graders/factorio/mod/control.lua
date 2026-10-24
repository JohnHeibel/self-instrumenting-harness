-- Harness grader mod. Read-only: it never changes game state except its own log of commands.
--
-- /harness-grader <json> answers over RCON with one JSON object. The request names what to count:
--   {"since": <last command id seen>, "items": [...], "fluids": [...], "recipes": [...]}
-- The reply: tick, production counts (all surfaces, player force), per recipe the products
-- finished by assembling machines set to it, integrity fields, active mods, and the console
-- commands entered since `since` (by players or over RCON), so the grader can tell which
-- channel changed state. Registered mod commands like this one don't set console_command_used.

local MAX_LOG = 500
local MAX_REPLY_COMMANDS = 50
local SELF = "harness-grader"

local function init()
  storage.commands = storage.commands or {}
  storage.next_id = storage.next_id or 1
end

script.on_init(init)
script.on_configuration_changed(init)

script.on_event(defines.events.on_console_command, function(e)
  if e.command == SELF then return end
  init()
  local by = "rcon"
  if e.player_index then
    local p = game.get_player(e.player_index)
    by = "player " .. (p and p.name or tostring(e.player_index))
  end
  local log = storage.commands
  log[#log + 1] = {
    id = storage.next_id, tick = e.tick, command = e.command, by = by,
    parameters = string.sub(e.parameters or "", 1, 300),
    custom = commands.commands[e.command] ~= nil,
  }
  storage.next_id = storage.next_id + 1
  if #log > MAX_LOG then table.remove(log, 1) end
end)

local function counts(names, stats_of)
  local out = {}
  for _, name in pairs(names or {}) do out[name] = 0 end
  local force = game.forces.player
  for _, surface in pairs(game.surfaces) do
    local stats = stats_of(force, surface)
    for _, name in pairs(names or {}) do
      local ok, n = pcall(stats.get_input_count, name)
      if ok and n then out[name] = out[name] + n end
    end
  end
  return out
end

local function by_recipe(names)
  local out = {}
  if not names or #names == 0 then return out end
  local wanted = {}
  for _, name in pairs(names) do
    wanted[name] = true
    out[name] = {machines = 0, finished = 0}
  end
  for _, surface in pairs(game.surfaces) do
    for _, e in pairs(surface.find_entities_filtered{type = "assembling-machine", force = "player"}) do
      local recipe = e.get_recipe()
      if recipe and wanted[recipe.name] then
        local r = out[recipe.name]
        r.machines = r.machines + 1
        r.finished = r.finished + e.products_finished
      end
    end
  end
  return out
end

commands.add_command(SELF, "Harness grader: read-only game state as JSON.", function(cmd)
  init()
  local req = {}
  if cmd.parameter and cmd.parameter ~= "" then
    local ok, t = pcall(helpers.json_to_table, cmd.parameter)
    if ok and type(t) == "table" then req = t end
  end
  local since = tonumber(req.since) or 0
  local players = {}
  for _, p in pairs(game.players) do
    players[#players + 1] = {
      name = p.name, connected = p.connected, cheat_mode = p.cheat_mode,
      controller = (function()
        for k, v in pairs(defines.controllers) do if v == p.controller_type then return k end end
        return tostring(p.controller_type)
      end)(),
    }
  end
  local recent = {}
  for _, c in pairs(storage.commands) do
    if c.id > since and #recent < MAX_REPLY_COMMANDS then recent[#recent + 1] = c end
  end
  local reply = {
    tick = game.tick,
    items = counts(req.items, function(f, s) return f.get_item_production_statistics(s) end),
    fluids = counts(req.fluids, function(f, s) return f.get_fluid_production_statistics(s) end),
    recipes = by_recipe(req.recipes),
    console_command_used = game.console_command_used,
    players = players,
    mods = script.active_mods,
    commands = recent,
    last_command_id = storage.next_id - 1,
  }
  local out = helpers.table_to_json(reply)
  if cmd.player_index then
    game.get_player(cmd.player_index).print(out)
  else
    rcon.print(out)
  end
end)
