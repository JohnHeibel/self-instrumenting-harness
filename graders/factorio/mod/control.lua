-- Harness grader mod. Read-only toward the game: it changes nothing but its own records in
-- `storage` (the command log and the fair-play checks below).
--
-- /harness-grader <json> answers over RCON with one JSON object. The request names what to count:
--   {"since": <last command id seen>, "items": [...], "fluids": [...], "recipes": [...]}
-- The reply: tick, production counts (all surfaces, player force), per recipe the products
-- finished by assembling machines set to it whose supply is automated (supply chain, below),
-- integrity fields, active mods, the console commands entered since `since` (by players or
-- over RCON), so the grader can tell which channel changed state, and `fairplay`: what the
-- three fair-play checks found (below). Registered mod commands like this one don't set
-- console_command_used.
--
-- Supply chain (recipe milestones). An assembler counts only if its supply traces upstream to
-- resource extraction with no hand-fed leaf. Upstream of a machine or chest: the inserters and
-- mining drills that drop into it. Of an inserter: its pickup target. Of a belt: its input
-- belts (splitters and loaders included), the other end of an underground, and the inserters
-- and drills that drop onto it. Mining drills (pumpjacks too) are extraction. A chest, furnace
-- or assembler that nothing feeds is hand-fed and breaks the chain, as does an inserter taking
-- from the ground or from something not traced (a train wagon, a car, a logistic chest: robots
-- aren't traced). A belt with no inputs is just the start of a belt. Fluids and fuel aren't
-- traced (a hand-fuelled furnace still counts; a fuel inserter from a hand-filled chest
-- doesn't). The search stops at TRACE_LIMIT entities per assembler and then excludes it.
-- products_finished stays the counter; it counts the machine's whole life, including crafts
-- made before it was connected. Verdicts are cached for TRACE_CACHE_TICKS.
--
-- Fair-play checks. Each keeps a sticky record in storage: once something is found it stays
-- reported, even if the evidence is later used up.
--
--   position_jump     every SAMPLE_TICKS each character's position is sampled (player characters
--                     and characters without a player, found by a scan every DISCOVER_TICKS).
--                     On foot, a character can't cover more than
--                       ticks * (running_speed * (1 + exoskeleton bonus) * fastest tile + fastest belt) + JUMP_SLACK
--                     between two samples. In the same vehicle in both samples, the limit is
--                     VEHICLE_SPEED tiles/tick. Getting in or out of a vehicle, a new character
--                     (respawn), a surface change or a gap of more than GAP_TICKS restarts the
--                     sampling instead of being judged. character_running_speed_modifier is
--                     deliberately not in the limit: nothing in the base game sets it, so a
--                     script raising it is the speed hack itself.
--
--   conservation      items can't come from nothing. Per item, everything visible now (character
--                     and player inventories, cursors, every entity inventory, inserter hands,
--                     belts, items on the ground) can't exceed what the force has produced
--                     (production statistics, all surfaces) + what existed when this mod was
--                     added (`baseline`) + known gifts (`allowance`: the freeplay scenario's
--                     created/respawn items, and what players or robots mine from entities that
--                     no item places, i.e. trees, rocks, ore, fish, the crash site, since the
--                     statistics may not count those) + CONSERVATION_TOLERANCE. Placing, moving,
--                     consuming or hiding an item (as an entity, a tile, armor equipment, in a
--                     crafting queue) only lowers what's visible, so normal play stays under the
--                     bound; only an item made from nothing goes over. Not checked: items no
--                     recipe makes that players get from shortcuts (blueprints, planners,
--                     remotes: "spawnable"/"only-in-cursor" and selection-tool types), burnt
--                     results and rocket launch products (the statistics may not count them).
--                     An exact balance (inputs - outputs = stock) isn't used: the statistics'
--                     consumption includes ingredients still inside machines and queues, which
--                     can't be seen. Cost: a full scan, so it runs at most every
--                     CONSERVATION_TICKS, when the grader asks. Gaps: a cheat made before this
--                     mod was added is in the baseline; an item made from nothing and used up
--                     before the next scan is missed; consumption leaves headroom, so for an item
--                     the force has already used up in quantity only the part of an insert above
--                     total production shows (a bound on production minus consumption would be
--                     tighter, but cancelled hand crafts, recipe changes mid-craft and machines
--                     mined mid-craft return ingredients the statistics already count as
--                     consumed, and recipe changes raise no event); mined natural resources are
--                     allowed twice if the statistics count them too.
--
--   instant_crafting  hand crafting can't finish faster than recipe energy / crafting speed,
--                     where speed = 1 + force.manual_crafting_speed_modifier (research).
--                     character_crafting_speed_modifier is not in the speed, for the same reason
--                     as the running modifier. Per player a "stretch" starts at a sample where
--                     the crafting queue is empty; every on_player_crafted_item adds that
--                     craft's time to the stretch, and the stretch can't be shorter than
--                     CRAFT_FRACTION of its total minus CRAFT_SLACK ticks. Sampling only makes
--                     a stretch start earlier than the crafting really did, which is lenient.
--
-- remote interface `harness_grader_selftest` (for tests on a headless server, which has no
-- connected player): crafted(key, recipe) and idle(key) drive the instant_crafting check for a
-- made-up crafter "selftest <key>" through the real code path. They only ever add records
-- under their own keys, so they can't hide anything a real player does.

local MAX_LOG = 500
local MAX_REPLY_COMMANDS = 50
local SELF = "harness-grader"

local SAMPLE_TICKS = 10
local DISCOVER_TICKS = 300
local JUMP_SLACK = 1.5          -- tiles, per sample interval
local VEHICLE_SPEED = 2.0       -- tiles/tick; faster than any base vehicle (locomotive 1.2)
local GAP_TICKS = 600
local CONSERVATION_TICKS = 300
local CONSERVATION_TOLERANCE = 2
local CRAFT_FRACTION = 0.9
local CRAFT_SLACK = 2
local MAX_RECORDS = 20
local TRACE_LIMIT = 2000
local TRACE_CACHE_TICKS = 600
local MAX_EXCLUDED = 10

local SKIP_ITEM_TYPES = {
  ["blueprint"] = true, ["blueprint-book"] = true, ["deconstruction-item"] = true,
  ["upgrade-item"] = true, ["selection-tool"] = true, ["copy-paste-tool"] = true,
}
-- Entity types whose inventories hold items. linked-container and proxy-container are left out:
-- they show another entity's inventory, which would count it twice.
local INVENTORY_TYPES = {
  "container", "logistic-container", "infinity-container", "temporary-container",
  "car", "spider-vehicle", "cargo-wagon", "artillery-wagon", "locomotive",
  "assembling-machine", "furnace", "rocket-silo", "lab", "mining-drill", "boiler", "burner-generator",
  "reactor", "generator", "inserter", "roboport", "construction-robot", "logistic-robot",
  "ammo-turret", "artillery-turret", "beacon", "character", "character-corpse", "radar",
  "agricultural-tower", "asteroid-collector", "space-platform-hub", "cargo-landing-pad", "cargo-pod",
}
local BELT_TYPES = { "transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "lane-splitter" }
local BELT = {}
for _, t in pairs(BELT_TYPES) do BELT[t] = true end

local function existing_types(list)
  local have, out = {}, {}
  for _, p in pairs(prototypes.entity) do have[p.type] = true end
  for _, t in pairs(list) do if have[t] then out[#out + 1] = t end end
  return out
end

local function r2(x) return math.floor(x * 100 + 0.5) / 100 end

-- Item counting ------------------------------------------------------------------------

local function item_key(name, quality)
  if quality and quality ~= "normal" then return name .. "@" .. quality end
  return name
end

local function add(t, name, quality, n)
  local k = item_key(name, quality)
  t[k] = (t[k] or 0) + n
end

local function add_list(t, list)
  for _, it in pairs(list) do add(t, it.name, it.quality, it.count) end
end

local function add_stack(t, st)
  if st and st.valid_for_read then add(t, st.name, st.quality and st.quality.name, st.count) end
end

local function stock(inv_types, belt_types)
  local t = {}
  local skip_vehicle = defines.inventory.character_vehicle
  for _, s in pairs(game.surfaces) do
    for _, e in pairs(s.find_entities_filtered{type = inv_types}) do
      local is_char = e.type == "character"
      for i = 1, e.get_max_inventory_index() do
        if not (is_char and i == skip_vehicle) then   -- the vehicle's own inventory, counted with it
          local ok, inv = pcall(e.get_inventory, i)
          if ok and inv then add_list(t, inv.get_contents()) end
        end
      end
      if e.type == "inserter" then add_stack(t, e.held_stack) end
    end
    for _, e in pairs(s.find_entities_filtered{type = belt_types}) do
      for i = 1, e.get_max_transport_line_index() do add_list(t, e.get_transport_line(i).get_contents()) end
    end
    for _, e in pairs(s.find_entities_filtered{type = "item-entity"}) do add_stack(t, e.stack) end
  end
  for _, p in pairs(game.players) do
    add_stack(t, p.cursor_stack)
    if not p.character then   -- god or editor controller: the player's own inventory
      local inv = p.get_main_inventory()
      if inv then add_list(t, inv.get_contents()) end
    end
  end
  return t
end

local function produced(key)
  local name, quality = key, "normal"
  local at = string.find(key, "@", 1, true)
  if at then name, quality = string.sub(key, 1, at - 1), string.sub(key, at + 1) end
  local n = 0
  local force = game.forces.player
  for _, s in pairs(game.surfaces) do
    local stats = force.get_item_production_statistics(s)
    local ok, c = pcall(stats.get_input_count, {name = name, quality = quality})
    if ok and c then n = n + c end
  end
  return n
end

local skipped_cache
local function skipped(key)
  if not skipped_cache then
    skipped_cache = {}
    for name, p in pairs(prototypes.item) do
      if SKIP_ITEM_TYPES[p.type] or (p.flags and (p.flags["spawnable"] or p.flags["only-in-cursor"])) then
        skipped_cache[name] = true
      end
      if p.burnt_result then skipped_cache[p.burnt_result.name] = true end
      for _, r in pairs(p.rocket_launch_products or {}) do
        if r.name then skipped_cache[r.name] = true end
      end
    end
  end
  return skipped_cache[(string.gsub(key, "@.*$", ""))]
end

-- Storage ------------------------------------------------------------------------------

local function limits()
  local tile, belt = 1, 0
  for _, t in pairs(prototypes.tile) do
    if (t.walking_speed_modifier or 0) > tile then tile = t.walking_speed_modifier end
  end
  for _, p in pairs(prototypes.entity) do
    if p.type == "transport-belt" and (p.belt_speed or 0) > belt then belt = p.belt_speed end
  end
  return {tile = tile, belt = belt}
end

local function init()
  storage.commands = storage.commands or {}
  storage.next_id = storage.next_id or 1
  local fp = storage.fairplay or {}
  storage.fairplay = fp
  fp.samples = fp.samples or {}       -- character unit_number -> last sample
  fp.chars = fp.chars or {}           -- character unit_number -> entity
  fp.jumps = fp.jumps or {count = 0, recent = {}}
  fp.crafts = fp.crafts or {count = 0, recent = {}}
  fp.stretch = fp.stretch or {}       -- crafter key -> {start, required, crafts, flagged}
  fp.cons = fp.cons or {allowance = {}, flagged = {}, count = 0}
  fp.traces = fp.traces or {}         -- assembler unit_number -> {tick, ok, reason}
  if not fp.limits then
    fp.limits = limits()
    fp.inventory_types = existing_types(INVENTORY_TYPES)
    fp.belt_types = existing_types(BELT_TYPES)
  end
  if not fp.cons.baseline then
    -- What exists before this mod first runs (starting items, the crash site) counts as given.
    fp.cons.baseline = {}
    for key, n in pairs(stock(fp.inventory_types, fp.belt_types)) do
      local extra = n - produced(key)
      if extra > 0 then fp.cons.baseline[key] = extra end
    end
    fp.cons.baseline_tick = game.tick
  end
end

local function on_config_changed()
  if storage.fairplay then
    storage.fairplay.limits = nil   -- prototypes may have changed
    skipped_cache = nil
  end
  init()
end

local function push(list, rec)
  list.count = list.count + 1
  list.recent[#list.recent + 1] = rec
  if #list.recent > MAX_RECORDS then table.remove(list.recent, 1) end
end

-- Commands log -------------------------------------------------------------------------

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

-- position_jump ------------------------------------------------------------------------

local function who(c)
  local p = c.player
  if p then return "player " .. p.name end
  return "character " .. c.unit_number
end

local function sample_character(fp, c, tick)
  local v = c.vehicle
  local grid = c.grid
  local cur = {
    tick = tick, x = c.position.x, y = c.position.y, surface = c.surface.index,
    vehicle = v and v.valid and (v.unit_number or -1) or nil,
    exo = grid and grid.movement_bonus or 0,
  }
  local prev = fp.samples[c.unit_number]
  fp.samples[c.unit_number] = cur
  if not prev or prev.surface ~= cur.surface or tick - prev.tick > GAP_TICKS then return end
  local dt = tick - prev.tick
  if dt <= 0 then return end
  local limit, mode
  if prev.vehicle or cur.vehicle then
    if prev.vehicle ~= cur.vehicle then return end   -- got in or out: the game moves the character
    limit, mode = dt * VEHICLE_SPEED + JUMP_SLACK, "vehicle"
  else
    local speed = c.prototype.running_speed * (1 + math.max(prev.exo, cur.exo)) * fp.limits.tile + fp.limits.belt
    limit, mode = dt * speed + JUMP_SLACK, "on foot"
  end
  local dx, dy = cur.x - prev.x, cur.y - prev.y
  local d = math.sqrt(dx * dx + dy * dy)
  if d > limit then
    push(fp.jumps, {who = who(c), mode = mode, surface = cur.surface,
                    from = {x = r2(prev.x), y = r2(prev.y)}, to = {x = r2(cur.x), y = r2(cur.y)},
                    from_tick = prev.tick, to_tick = tick, ticks = dt, distance = r2(d), limit = r2(limit),
                    running_speed_modifier = c.character_running_speed_modifier})
  end
end

-- instant_crafting ---------------------------------------------------------------------

local function craft_speed(force)
  return 1 + (force and force.manual_crafting_speed_modifier or 0)
end

local function craft_ticks(recipe, force)
  local items = 0
  for _, p in pairs(recipe.products or {}) do
    if p.type == "item" then items = items + 1 end
  end
  -- The event fires once per item product, so a multi-product craft is split between them.
  return recipe.energy * 60 / craft_speed(force) / math.max(1, items)
end

local function crafting_idle(fp, key, tick)
  fp.stretch[key] = {start = tick, required = 0, crafts = 0}
end

local function crafted(fp, key, recipe, force, tick, modifier)
  local s = fp.stretch[key]
  if not s then
    -- First craft seen for this crafter (e.g. the mod was just added mid-queue): the craft
    -- in progress may have started long ago, so it's free.
    s = {start = tick, required = -craft_ticks(recipe, force), crafts = 0}
    fp.stretch[key] = s
  end
  s.required = s.required + craft_ticks(recipe, force)
  s.crafts = s.crafts + 1
  local elapsed = tick - s.start
  if elapsed < s.required * CRAFT_FRACTION - CRAFT_SLACK then
    if s.flagged then
      s.flagged.crafts, s.flagged.elapsed, s.flagged.required = s.crafts, elapsed, r2(s.required)
    else
      s.flagged = {who = key, recipe = recipe.name, tick = tick, stretch_start = s.start, crafts = s.crafts,
                   elapsed = elapsed, required = r2(s.required), speed = craft_speed(force),
                   character_crafting_speed_modifier = modifier}
      push(fp.crafts, s.flagged)
    end
  end
end

script.on_event(defines.events.on_player_crafted_item, function(e)
  init()
  local p = game.get_player(e.player_index)
  if not (p and e.recipe and e.recipe.valid) then return end
  local mod = p.character and p.character.character_crafting_speed_modifier or nil
  crafted(storage.fairplay, "player " .. p.name, e.recipe, p.force, e.tick, mod)
end)

-- Sampling -----------------------------------------------------------------------------

script.on_nth_tick(SAMPLE_TICKS, function(e)
  local fp = storage.fairplay
  if not fp then return end
  local tick = e.tick
  if tick % DISCOVER_TICKS == 0 then
    for _, s in pairs(game.surfaces) do
      for _, c in pairs(s.find_entities_filtered{type = "character"}) do fp.chars[c.unit_number] = c end
    end
  end
  for _, p in pairs(game.players) do
    local c = p.character
    if c and c.valid then
      fp.chars[c.unit_number] = c
      if p.crafting_queue_size == 0 then crafting_idle(fp, "player " .. p.name, tick) end
    end
  end
  for un, c in pairs(fp.chars) do
    if c.valid then
      sample_character(fp, c, tick)
    else
      fp.chars[un] = nil
      fp.samples[un] = nil
    end
  end
end)

-- conservation -------------------------------------------------------------------------

local function conservation_check()
  local fp = storage.fairplay
  local c = fp.cons
  if c.checked_tick and game.tick - c.checked_tick < CONSERVATION_TICKS then return end
  for key, n in pairs(stock(fp.inventory_types, fp.belt_types)) do
    if not skipped(key) then
      local made = produced(key)
      local given = (c.baseline[key] or 0) + (c.allowance[key] or 0)
      local excess = n - made - given
      if excess > CONSERVATION_TOLERANCE then
        local f = c.flagged[key]
        if not f then
          c.flagged[key] = {item = key, first_tick = game.tick, tick = game.tick, stock = n, produced = made,
                            given = given, excess = excess, max_excess = excess}
          c.count = c.count + 1
        else
          f.stock, f.produced, f.given, f.excess, f.tick = n, made, given, excess, game.tick
          if excess > f.max_excess then f.max_excess = excess end
        end
      end
    end
  end
  c.checked_tick = game.tick
end

local function allow(items)
  local a = storage.fairplay.cons.allowance
  for name, n in pairs(items or {}) do a[name] = (a[name] or 0) + n end
end

local function mined_allowance(e)
  local proto = e.entity and e.entity.valid and e.entity.prototype
  if not proto or (proto.items_to_place_this and #proto.items_to_place_this > 0) then return end
  init()
  local a = storage.fairplay.cons.allowance
  for _, it in pairs(e.buffer.get_contents()) do
    local k = item_key(it.name, it.quality)
    a[k] = (a[k] or 0) + it.count
  end
end
script.on_event(defines.events.on_player_mined_entity, mined_allowance)
script.on_event(defines.events.on_robot_mined_entity, mined_allowance)

local function scenario_items(fn)
  if remote.interfaces.freeplay and remote.interfaces.freeplay[fn] then
    local ok, items = pcall(remote.call, "freeplay", fn)
    if ok and type(items) == "table" then return items end
  end
  return nil
end

script.on_event(defines.events.on_player_created, function() init(); allow(scenario_items("get_created_items")) end)
script.on_event(defines.events.on_player_respawned, function() init(); allow(scenario_items("get_respawn_items")) end)

-- Supply chain -------------------------------------------------------------------------

local function describe(e)
  return string.format("%s at (%.1f, %.1f)", e.name, e.position.x, e.position.y)
end

local function droppers(e)
  -- Inserters and mining drills whose output goes into (or onto) e.
  local bb = e.bounding_box
  local out = {}
  local area = {{bb.left_top.x - 3, bb.left_top.y - 3}, {bb.right_bottom.x + 3, bb.right_bottom.y + 3}}
  for _, f in pairs(e.surface.find_entities_filtered{type = {"inserter", "mining-drill"}, area = area}) do
    if f.drop_target == e then out[#out + 1] = f end
  end
  return out
end

local function needs_items(e)
  local ok, recipe = pcall(e.get_recipe)
  if not ok or not recipe then
    if e.type == "furnace" then return true end
    return false
  end
  for _, i in pairs(recipe.ingredients) do
    if i.type == "item" then return true end
  end
  return false
end

local function trace(root)
  -- Breadth-first upstream from the assembler. Returns ok, reason, entities visited.
  local seen, queue, head, extraction = {}, {root}, 1, false
  local function key(e) return e.unit_number or (e.name .. e.position.x .. "," .. e.position.y) end
  seen[key(root)] = true
  local function visit(e)
    if e and e.valid and not seen[key(e)] then
      seen[key(e)] = true
      queue[#queue + 1] = e
    end
  end
  while queue[head] do
    if head > TRACE_LIMIT then return false, "search stopped at " .. TRACE_LIMIT .. " entities", head - 1 end
    local e = queue[head]
    head = head + 1
    local t = e.type
    if t == "mining-drill" then
      extraction = true
    elseif t == "inserter" then
      local src = e.pickup_target
      if src then
        visit(src)
      elseif e.surface.count_entities_filtered{type = "item-entity", position = e.pickup_position, radius = 0.5} > 0 then
        return false, describe(e) .. " picks up items from the ground", head - 1
      end   -- else it picks up from nothing and supplies nothing
    elseif BELT[t] then
      for _, b in pairs(e.belt_neighbours.inputs) do visit(b) end
      if t == "underground-belt" and e.belt_to_ground_type == "output" then visit(e.underground_belt_neighbour) end
      for _, f in pairs(droppers(e)) do visit(f) end
    elseif t == "assembling-machine" or t == "furnace" or t == "container" then
      local fs = droppers(e)
      if #fs == 0 and (t == "container" or needs_items(e)) then
        return false, describe(e) .. " has no inserter or drill feeding it (hand-fed)", head - 1
      end
      for _, f in pairs(fs) do visit(f) end
    else
      return false, describe(e) .. " is a source the trace doesn't follow (" .. t .. ")", head - 1
    end
  end
  if not extraction then return false, "no mining drill upstream", head - 1 end
  return true, nil, head - 1
end

local function supply_ok(fp, e)
  local cached = fp.traces[e.unit_number]
  if cached and game.tick - cached.tick < TRACE_CACHE_TICKS then return cached end
  local ok, reason, n = trace(e)
  cached = {tick = game.tick, ok = ok, reason = reason, visited = n}
  fp.traces[e.unit_number] = cached
  return cached
end

-- Reply --------------------------------------------------------------------------------

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
  -- machines/finished: assemblers with an automated supply; all_*: every assembler on the recipe.
  local out = {}
  if not names or #names == 0 then return out end
  local fp = storage.fairplay
  local wanted = {}
  for _, name in pairs(names) do
    wanted[name] = true
    out[name] = {machines = 0, finished = 0, all_machines = 0, all_finished = 0, excluded = {}}
  end
  local alive = {}
  for _, surface in pairs(game.surfaces) do
    for _, e in pairs(surface.find_entities_filtered{type = "assembling-machine", force = "player"}) do
      local recipe = e.get_recipe()
      if recipe and wanted[recipe.name] then
        local r = out[recipe.name]
        r.all_machines = r.all_machines + 1
        r.all_finished = r.all_finished + e.products_finished
        alive[e.unit_number] = true
        local v = supply_ok(fp, e)
        if v.ok then
          r.machines = r.machines + 1
          r.finished = r.finished + e.products_finished
        elseif #r.excluded < MAX_EXCLUDED then
          r.excluded[#r.excluded + 1] = {position = {x = e.position.x, y = e.position.y},
                                         finished = e.products_finished, reason = v.reason}
        end
      end
    end
  end
  for un in pairs(fp.traces) do
    if not alive[un] then fp.traces[un] = nil end
  end
  return out
end

local function fairplay()
  local fp = storage.fairplay
  conservation_check()
  local items = {}
  for _, f in pairs(fp.cons.flagged) do items[#items + 1] = f end
  table.sort(items, function(a, b) return a.first_tick < b.first_tick end)
  return {
    position_jump = {count = fp.jumps.count, recent = fp.jumps.recent, sample_ticks = SAMPLE_TICKS,
                     tracked = table_size(fp.chars)},
    conservation = {count = fp.cons.count, items = items, checked_tick = fp.cons.checked_tick,
                    baseline_tick = fp.cons.baseline_tick, tolerance = CONSERVATION_TOLERANCE},
    instant_crafting = {count = fp.crafts.count, recent = fp.crafts.recent},
  }
end

-- Init ---------------------------------------------------------------------------------

script.on_init(init)
script.on_configuration_changed(on_config_changed)

remote.add_interface("harness_grader_selftest", {
  crafted = function(key, recipe)
    init()
    local r = game.forces.player.recipes[recipe]
    if r then crafted(storage.fairplay, "selftest " .. tostring(key), r, game.forces.player, game.tick, nil) end
  end,
  idle = function(key)
    init()
    crafting_idle(storage.fairplay, "selftest " .. tostring(key), game.tick)
  end,
})

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
    rockets_launched = game.forces.player and game.forces.player.rockets_launched or 0,
    console_command_used = game.console_command_used,
    players = players,
    mods = script.active_mods,
    commands = recent,
    last_command_id = storage.next_id - 1,
    fairplay = fairplay(),
  }
  local out = helpers.table_to_json(reply)
  if cmd.player_index then
    game.get_player(cmd.player_index).print(out)
  else
    rcon.print(out)
  end
end)
