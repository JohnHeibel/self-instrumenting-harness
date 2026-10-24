# UNTESTED -- written from docs; verify on john-pc. Nothing here has been run against Factorio.
#
# Save, stop and relaunch the harness Factorio into the latest save. Idempotent: with nothing running it
# just starts things. The game window is ALWAYS launched through Steam (steam://rungameid/427520), never by
# running the Steam copy of factorio.exe with arguments (that triggers Steam's custom-arguments prompt).
# Arguments for that Steam launch are the Launch Options John sets once (see README).
#
# FACTORIO_MODE=server (default): a headless server from a separate non-Steam install + the Steam client
#     (Launch Options: --mp-connect 127.0.0.1:34197). RCON is the server's.
# FACTORIO_MODE=host: the Steam game hosts the save itself (Launch Options: --host <save>); RCON comes from
#     local-rcon-socket / local-rcon-password in config.ini, which this script writes from the environment.
#
# Flow: save over RCON (exit 4 and kill nothing if that cannot be confirmed) -> stop -> start -> wait for RCON.
# Switches: -NoClient (server mode: do not relaunch the Steam client), -NoSave (skip saving; progress is lost).
# Exit codes: see lib.ps1. Settings come from environment variables, see hosts/factorio/README.md.
param([switch]$NoClient, [switch]$NoSave)

. (Join-Path $PSScriptRoot 'lib.ps1')
$S = Get-HostSettings
Assert-Settings $S $true
$hostMode = ($S.Mode -eq 'host')

function Wait-ForRcon($Proc, [int]$Seconds, [string]$LogFile) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($Proc -and $Proc.HasExited) { break }
        try { $null = Invoke-Rcon '127.0.0.1' $S.RconPort $S.Password '/time' 3000; return $true } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}

if ($hostMode) {
    $savesDir = Join-Path $S.UserData 'saves'
    $hostSave = Join-Path $savesDir 'harness.zip'            # the file --host in Launch Options points at
    $configIni = Get-EnvOr 'FACTORIO_CONFIG_INI' (Join-Path $S.UserData 'config\config.ini')
    $logFile = ''
} else {
    $savesDir = Join-Path $S.HarnessDir 'saves'
    $serverCfg = Join-Path $S.HarnessDir 'config.ini'
    $settingsJson = Join-Path $S.HarnessDir 'server-settings.json'
    $logFile = Join-Path $S.HarnessDir 'factorio-server.log'
    New-Item -ItemType Directory -Force -Path $S.HarnessDir, $savesDir | Out-Null
    # Own user-data dir for the headless server (write-data); mods shared through --mod-directory. Generated once.
    if (-not (Test-Path -LiteralPath $serverCfg)) {
        $root = Split-Path (Split-Path (Split-Path $S.Exe))      # exe is <root>\bin\x64\factorio.exe
        $fwd = { param($p) $p.Replace('\', '/') }
        @("[path]", "read-data=$(& $fwd (Join-Path $root 'data'))", "write-data=$(& $fwd $S.HarnessDir)") |
            Set-Content -LiteralPath $serverCfg -Encoding ASCII
        Write-Step "wrote $serverCfg"
    }
    if (-not (Test-Path -LiteralPath $settingsJson)) {
        $template = Join-Path $PSScriptRoot 'server-settings.json'
        if (-not (Test-Path -LiteralPath $template)) { Stop-WithError 2 "missing $template" }
        Copy-Item -LiteralPath $template -Destination $settingsJson
        Write-Step "copied server-settings.json to $settingsJson"
    }
}

# --- 1. save. "The game" = the server process (server mode) or the single Steam game process (host mode).
$procs = @(Get-FactorioProcesses)
$running = if ($hostMode) { $procs | Select-Object -First 1 } else { $procs | Where-Object { $_.IsServer } | Select-Object -First 1 }
if ($running -and -not $NoSave) {
    Write-Step "game running (pid $($running.ProcessId)); saving over RCON"
    $started = Get-Date
    try {
        $reply = Invoke-Rcon '127.0.0.1' $S.RconPort $S.Password '/server-save'
        Write-Step "RCON reply: $reply"
    } catch {
        Stop-WithError 4 "the game is running but RCON failed ($($_.Exception.Message)); not stopping it, nothing saved."
    }
    $deadline = (Get-Date).AddSeconds(90)
    $saved = $false
    $lastSize = -1
    while ((Get-Date) -lt $deadline) {
        $latest = Get-LatestSave $savesDir
        if ($latest -and $latest.LastWriteTime -ge $started.AddSeconds(-1)) {
            if ($latest.Length -eq $lastSize) { $saved = $true; break }   # written and stopped growing
            $lastSize = $latest.Length
        }
        Start-Sleep -Seconds 2
    }
    if (-not $saved) { Stop-WithError 4 "no new save appeared in $savesDir within 90 s of /server-save; not stopping the game." }
    Write-Step "saved: $($latest.Name)"
}

# --- 2. stop (the server, and the Steam client if it is a separate process)
foreach ($p in $procs) {
    if (-not $hostMode -and -not $p.IsServer -and $NoClient) { continue }
    Write-Step "stopping pid $($p.ProcessId) ($(if ($p.IsServer) { 'server' } elseif ($hostMode) { 'game' } else { 'client' }))"
    if (-not (Stop-FactorioProcess $p.ProcessId)) { Stop-WithError 5 "process $($p.ProcessId) did not exit" }
}

# --- 3/4. start and wait for RCON
$latest = Get-LatestSave $savesDir
if (-not $latest) {
    $hint = if ($hostMode) { "Put a save at $hostSave (see README)." }
            else { "Create one once: & '$($S.Exe)' --config '$serverCfg' --create '$(Join-Path $savesDir 'harness.zip')' (see README)." }
    Stop-WithError 3 "no save in $savesDir. $hint"
}
if ($hostMode) {
    # --host in Launch Options is a fixed file, so make that file the newest state (e.g. after a crash left a newer autosave).
    if ($latest.FullName -ne $hostSave) {
        Write-Step "copying $($latest.Name) over harness.zip"
        Copy-Item -LiteralPath $latest.FullName -Destination $hostSave -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $configIni) | Out-Null
    Set-IniValue $configIni 'other' 'local-rcon-socket' "$($S.RconBind):$($S.RconPort)"
    Set-IniValue $configIni 'other' 'local-rcon-password' $S.Password
    Write-Step "RCON written to $configIni; launching through Steam"
    try { Start-Process $S.SteamUrl } catch { Stop-WithError 6 "could not open $($S.SteamUrl): $($_.Exception.Message)" }
    if (-not (Wait-ForRcon $null 180 '')) { Stop-WithError 5 "no RCON answer within 180 s of the Steam launch. Check Launch Options (--host ...), the Steam 'custom arguments' prompt, and Factorio's log." }
    Write-Step "game hosting, RCON answering on port $($S.RconPort)"
} else {
    $serverArgs = @('--start-server-load-latest', '--config', (ConvertTo-Arg $serverCfg),
        '--server-settings', (ConvertTo-Arg $settingsJson), '--mod-directory', (ConvertTo-Arg $S.ModDir),
        '--port', $S.GamePort, '--rcon-bind', "$($S.RconBind):$($S.RconPort)", '--rcon-password', (ConvertTo-Arg $S.Password),
        '--console-log', (ConvertTo-Arg $logFile))
    if ($S.LuaUdpPort) { $serverArgs += @('--enable-lua-udp', $S.LuaUdpPort) }
    Write-Step "starting headless server from $($latest.Name)"
    $srv = Start-Process -FilePath $S.Exe -ArgumentList $serverArgs -WindowStyle Hidden -PassThru
    if (-not (Wait-ForRcon $srv 120 $logFile)) {
        $tail = if (Test-Path -LiteralPath $logFile) { (Get-Content -LiteralPath $logFile -Tail 15) -join "`n" } else { '(no log)' }
        Stop-WithError 5 "server did not answer RCON within 120 s (exited: $($srv.HasExited)). Log tail:`n$tail"
    }
    Write-Step "server up, RCON answering on port $($S.RconPort)"
    # --- 5. client, through Steam; its Launch Options carry --mp-connect
    if (-not $NoClient) {
        try { Start-Process $S.SteamUrl } catch { Stop-WithError 6 "could not open $($S.SteamUrl): $($_.Exception.Message)" }
        Write-Step "client launched through Steam (connection not verified here)"
    }
}
Write-Step "done"
exit 0
