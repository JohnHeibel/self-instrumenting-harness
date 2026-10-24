# UNTESTED -- written from docs; verify on john-pc. Nothing here has been run against Factorio.
#
# Shared helpers for restart.ps1 and rcon.ps1 (dot-sourced). Exit codes used by the scripts:
#   0 ok | 2 bad setup (env var, exe, settings missing) | 3 no save to load
#   4 could not save (or RCON unreachable while a server is running; nothing was killed)
#   5 server did not come up | 6 client did not start | 7 RCON command failed
# Everything the user can change is an environment variable (see hosts/factorio/README.md).
# The RCON password is never written to the repo: it comes from FACTORIO_RCON_PASSWORD.

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) { [Console]::Out.WriteLine("[factorio-host] $Message") }

function Stop-WithError([int]$Code, [string]$Message) {
    [Console]::Error.WriteLine("[factorio-host] ERROR ($Code): $Message")
    exit $Code
}

function Get-EnvOr([string]$Name, [string]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($v)) { return $Default }
    return $v
}

function Get-HostSettings {
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    $appData = [Environment]::GetEnvironmentVariable('APPDATA')
    $local = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    $s = [ordered]@{
        Exe         = Get-EnvOr 'FACTORIO_EXE' "$pf86\Steam\steamapps\common\Factorio\bin\x64\factorio.exe"
        ModDir      = Get-EnvOr 'FACTORIO_MOD_DIR' "$appData\Factorio\mods"
        HarnessDir  = Get-EnvOr 'FACTORIO_HARNESS_DIR' "$local\factorio-harness"
        RconPort    = [int](Get-EnvOr 'FACTORIO_RCON_PORT' '27015')
        RconBind    = Get-EnvOr 'FACTORIO_RCON_BIND' '0.0.0.0'      # 0.0.0.0 so Docker's host.docker.internal can reach it
        GamePort    = [int](Get-EnvOr 'FACTORIO_GAME_PORT' '34197')
        ConnectHost = Get-EnvOr 'FACTORIO_CONNECT_HOST' '127.0.0.1'
        LuaUdpPort  = Get-EnvOr 'FACTORIO_LUA_UDP_PORT' ''           # empty = do not pass --enable-lua-udp
        Password    = [Environment]::GetEnvironmentVariable('FACTORIO_RCON_PASSWORD')
    }
    return [pscustomobject]$s
}

function Assert-Settings($S, [bool]$NeedExe) {
    if ([string]::IsNullOrEmpty($S.Password)) {
        Stop-WithError 2 'FACTORIO_RCON_PASSWORD is not set. Set it (user environment variable, same value for the grader) and restart the tool server.'
    }
    if ($NeedExe -and -not (Test-Path -LiteralPath $S.Exe)) {
        Stop-WithError 2 "Factorio executable not found at '$($S.Exe)'. Set FACTORIO_EXE to the full path of factorio.exe."
    }
}

function ConvertTo-Arg([string]$Value) { return '"' + $Value.Replace('"', '\"') + '"' }

# ---------------------------------------------------------------- processes

function Get-FactorioProcesses {
    # Returns objects with ProcessId, CommandLine, IsServer. The server is the one started with --start-server.
    $found = @(Get-CimInstance Win32_Process -Filter "Name = 'factorio.exe'" -ErrorAction SilentlyContinue)
    foreach ($p in $found) {
        $cl = [string]$p.CommandLine
        [pscustomobject]@{ ProcessId = [int]$p.ProcessId; CommandLine = $cl; IsServer = ($cl -match '--start-server') }
    }
}

function Stop-FactorioProcess([int]$ProcessId, [int]$TimeoutS = 30) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds($TimeoutS)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

# ---------------------------------------------------------------- RCON (Source RCON over TCP)
# Packet: int32 size, int32 id, int32 type, body (UTF-8), 0x00, 0x00; little endian.
# Types: 3 = auth, 2 = auth response (when received) / exec command (when sent), 0 = command response.
# Factorio's wiki page for RCON could not be fetched when this was written, so details such as
# whether an empty reply produces a packet are assumed; a read timeout after a command is treated as an empty reply.

function Read-Exact($Stream, [int]$Count) {
    $buf = New-Object byte[] $Count
    $off = 0
    while ($off -lt $Count) {
        $n = $Stream.Read($buf, $off, $Count - $off)
        if ($n -le 0) { throw 'connection closed by the server' }
        $off += $n
    }
    return , $buf
}

function Send-RconPacket($Stream, [int]$Id, [int]$Type, [string]$Body) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
    $ms = New-Object IO.MemoryStream
    $bw = New-Object IO.BinaryWriter($ms)
    $bw.Write([int32](10 + $bytes.Length)); $bw.Write([int32]$Id); $bw.Write([int32]$Type)
    $bw.Write($bytes); $bw.Write([byte]0); $bw.Write([byte]0); $bw.Flush()
    $out = $ms.ToArray()
    $Stream.Write($out, 0, $out.Length); $Stream.Flush()
}

function Read-RconPacket($Stream) {
    $size = [BitConverter]::ToInt32((Read-Exact $Stream 4), 0)
    if ($size -lt 10 -or $size -gt 1MB) { throw "bad RCON packet size $size" }
    $data = Read-Exact $Stream $size
    [pscustomobject]@{
        Id   = [BitConverter]::ToInt32($data, 0)
        Type = [BitConverter]::ToInt32($data, 4)
        Body = [Text.Encoding]::UTF8.GetString($data, 8, $size - 10)
    }
}

# Sends one command, returns its output text. Throws on connect or auth failure.
function Invoke-Rcon([string]$Server, [int]$Port, [string]$Password, [string]$Command, [int]$TimeoutMs = 8000) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($Server, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { throw "connect to ${Server}:${Port} timed out" }
        $client.EndConnect($iar)
        $stream = $client.GetStream()
        $stream.ReadTimeout = $TimeoutMs
        Send-RconPacket $stream 1 3 $Password
        $authed = $false
        for ($i = 0; $i -lt 4 -and -not $authed; $i++) {
            $p = Read-RconPacket $stream
            if ($p.Type -eq 2) {
                if ($p.Id -eq -1) { throw 'RCON password rejected' }
                $authed = $true
            }
        }
        if (-not $authed) { throw 'no RCON auth response' }
        Send-RconPacket $stream 2 2 $Command
        try {
            $r = Read-RconPacket $stream
            return $r.Body
        } catch [System.IO.IOException] {
            return ''     # read timed out: command produced no reply (assumed)
        }
    } finally {
        $client.Close()
    }
}

# ---------------------------------------------------------------- saves

function Get-LatestSave([string]$SavesDir) {
    if (-not (Test-Path -LiteralPath $SavesDir)) { return $null }
    return Get-ChildItem -LiteralPath $SavesDir -Filter *.zip -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
