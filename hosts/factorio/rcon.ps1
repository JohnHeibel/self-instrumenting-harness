# UNTESTED -- written from docs; verify on john-pc. Nothing here has been run against Factorio.
#
# Send one console command to the harness Factorio server over RCON and print the reply.
#   powershell -NoProfile -ExecutionPolicy Bypass -File hosts/factorio/rcon.ps1 -Command "/time"
# Used by John to smoke-test RCON, and (commented out in hosts/factorio.toml) as a lighter reload.
# Reads FACTORIO_RCON_PASSWORD / FACTORIO_RCON_PORT from the environment. Exit: 0 ok, 2 setup, 7 RCON failed.
param([Parameter(Mandatory = $true)][string]$Command, [string]$Server = '127.0.0.1')

. (Join-Path $PSScriptRoot 'lib.ps1')
$S = Get-HostSettings
Assert-Settings $S $false
try {
    $reply = Invoke-Rcon $Server $S.RconPort $S.Password $Command
} catch {
    Stop-WithError 7 "RCON to ${Server}:$($S.RconPort) failed: $($_.Exception.Message)"
}
[Console]::Out.WriteLine($reply)
exit 0
