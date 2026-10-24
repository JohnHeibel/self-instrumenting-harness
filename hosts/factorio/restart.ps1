# UNTESTED -- written from docs; verify on john-pc. Nothing here has been run against Factorio.
#
# Save, stop and relaunch the harness Factorio into the latest save, then reconnect the GUI client.
# Idempotent: with nothing running it just starts the server and the client; safe to run twice.
#
#   server : factorio.exe --start-server-load-latest ... (no window), RCON on for the grader and the agent's helpers
#   client : factorio.exe --mp-connect 127.0.0.1:<game port> (the window the agent screenshots)
#
# Flow: (1) if a server is running, RCON "/server-save" and wait for the newest save file to be written;
#       if that cannot be confirmed, stop with exit 4 and kill nothing. (2) stop the server and the client.
#       (3) start the server from the latest save. (4) wait until RCON answers. (5) start the client.
# Switches: -NoClient (server only), -NoSave (skip the save step; unsaved progress is lost).
# Exit codes: see lib.ps1. Settings come from environment variables, see hosts/factorio/README.md.
param([switch]$NoClient, [switch]$NoSave)

. (Join-Path $PSScriptRoot 'lib.ps1')
$S = Get-HostSettings
Assert-Settings $S $true

$savesDir = Join-Path $S.HarnessDir 'saves'
$serverCfg = Join-Path $S.HarnessDir 'config.ini'
$settingsJson = Join-Path $S.HarnessDir 'server-settings.json'
$logFile = Join-Path $S.HarnessDir 'factorio-server.log'
New-Item -ItemType Directory -Force -Path $S.HarnessDir, $savesDir | Out-Null

# The server gets its own user-data directory (write-data) so it does not fight the GUI client over
# %APPDATA%\Factorio's lock file. Mods are shared through --mod-directory. Generated once, never overwritten.
if (-not (Test-Path -LiteralPath $serverCfg)) {
    $root = Split-Path (Split-Path (Split-Path $S.Exe))          # ...\Factorio (exe is bin\x64\factorio.exe)
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

# --- 1. save
$procs = @(Get-FactorioProcesses)
$server = $procs | Where-Object { $_.IsServer } | Select-Object -First 1
if ($server -and -not $NoSave) {
    Write-Step "server running (pid $($server.ProcessId)); saving over RCON"
    $started = Get-Date
    try {
        $reply = Invoke-Rcon '127.0.0.1' $S.RconPort $S.Password '/server-save'
        Write-Step "RCON reply: $reply"
    } catch {
        Stop-WithError 4 "a server is running but RCON failed ($($_.Exception.Message)); not stopping it, nothing saved."
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
    if (-not $saved) { Stop-WithError 4 "no new save appeared in $savesDir within 90 s of /server-save; not stopping the server." }
    Write-Step "saved: $($latest.Name)"
}

# --- 2. stop
foreach ($p in $procs) {
    if (-not $p.IsServer -and $NoClient) { continue }
    Write-Step "stopping $(if ($p.IsServer) { 'server' } else { 'client' }) pid $($p.ProcessId)"
    if (-not (Stop-FactorioProcess $p.ProcessId)) { Stop-WithError 5 "process $($p.ProcessId) did not exit" }
}

# --- 3. start the server from the latest save
$latest = Get-LatestSave $savesDir
if (-not $latest) {
    Stop-WithError 3 ("no save in $savesDir. Create one once: & '$($S.Exe)' --config '$serverCfg' --create '$(Join-Path $savesDir 'harness.zip')' " +
                      "(see hosts/factorio/README.md)")
}
$serverArgs = @('--start-server-load-latest', '--config', (ConvertTo-Arg $serverCfg),
    '--server-settings', (ConvertTo-Arg $settingsJson), '--mod-directory', (ConvertTo-Arg $S.ModDir),
    '--port', $S.GamePort, '--rcon-bind', "$($S.RconBind):$($S.RconPort)", '--rcon-password', (ConvertTo-Arg $S.Password),
    '--console-log', (ConvertTo-Arg $logFile))
if ($S.LuaUdpPort) { $serverArgs += @('--enable-lua-udp', $S.LuaUdpPort) }
Write-Step "starting server from $($latest.Name)"
$srv = Start-Process -FilePath $S.Exe -ArgumentList $serverArgs -WindowStyle Hidden -PassThru

# --- 4. wait for RCON
$deadline = (Get-Date).AddSeconds(120)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if ($srv.HasExited) { break }
    try { $null = Invoke-Rcon '127.0.0.1' $S.RconPort $S.Password '/time' 3000; $ready = $true; break } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) {
    $tail = if (Test-Path -LiteralPath $logFile) { (Get-Content -LiteralPath $logFile -Tail 15) -join "`n" } else { '(no log)' }
    Stop-WithError 5 "server did not answer RCON within 120 s (exited: $($srv.HasExited)). Log tail:`n$tail"
}
Write-Step "server up, RCON answering on port $($S.RconPort)"

# --- 5. client
if (-not $NoClient) {
    $clientArgs = @('--mp-connect', "$($S.ConnectHost):$($S.GamePort)")
    try { $null = Start-Process -FilePath $S.Exe -ArgumentList $clientArgs -PassThru } catch { Stop-WithError 6 "could not start the client: $($_.Exception.Message)" }
    Write-Step "client launched with --mp-connect $($S.ConnectHost):$($S.GamePort) (connection not verified here)"
}
Write-Step "done"
exit 0
