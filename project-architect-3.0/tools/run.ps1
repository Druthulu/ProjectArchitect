# run.ps1 -- run.sh's contract for native PowerShell commands (dotnet, *.ps1).
#
#   powershell -File tools/run.ps1 <name> [--tail N | --grep PAT] [--timeout S] -- <cmd...>
#   powershell -File tools/run.ps1 --bg <name> -- <cmd...>
#   powershell -File tools/run.ps1 --wait <name> [--max 3000]
#
# Output goes to .run/logs/<name>.log; the caller sees at most 40 lines.

$ErrorActionPreference = 'Continue'

function Show-Usage {
  Get-Content -LiteralPath $PSCommandPath -TotalCount 7 |
    ForEach-Object { $_ -replace '^# ?', '' }
  exit 0
}

function Deny($msg) { Write-Output "refused: $msg"; exit 1 }

function Find-Root {
  $d = (Get-Location).Path
  while ($d) {
    if (Test-Path (Join-Path $d '.claude/pa.json')) { return $d }
    $p = Split-Path $d -Parent
    if ($p -eq $d) { break }
    $d = $p
  }
  $top = (& git rev-parse --show-toplevel 2>$null)
  if ($LASTEXITCODE -eq 0 -and $top) { return ($top -replace '/', '\') }
  return (Get-Location).Path
}

$argv = @($args)
if ($argv.Count -eq 0) { Show-Usage }
if ($argv[0] -eq '-h' -or $argv[0] -eq '--help') { Show-Usage }

$mode = 'fg'
if ($argv[0] -eq '--bg') { $mode = 'bg'; $argv = $argv[1..($argv.Count - 1)] }
elseif ($argv[0] -eq '--wait') { $mode = 'wait'; $argv = $argv[1..($argv.Count - 1)] }
if ($argv.Count -eq 0) { Deny 'no <name>' }

$name = $argv[0]
if ($name -match '[\\/\s]' -or $name -like '--*') { Deny "<name> must be a bare token (got '$name')" }
$argv = @($argv[1..($argv.Count - 1)])

$tailN = 39; $pattern = ''; $timeoutSec = 0; $maxWait = 3000; $cmd = @()
$i = 0
while ($i -lt $argv.Count) {
  switch ($argv[$i]) {
    '--tail'    { $tailN = [int]$argv[$i + 1]; $i += 2 }
    '--grep'    { $pattern = [string]$argv[$i + 1]; $i += 2 }
    '--timeout' { $timeoutSec = [int]$argv[$i + 1]; $i += 2 }
    '--max'     { $maxWait = [int]$argv[$i + 1]; $i += 2 }
    '--'        { $cmd = @($argv[($i + 1)..($argv.Count - 1)]); $i = $argv.Count }
    default     { Deny "unknown option '$($argv[$i])' (commands go after --)" }
  }
}
if ($tailN -gt 39) { $tailN = 39 }

$root = Find-Root
$logdir = Join-Path $root '.run\logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Force -Path $logdir | Out-Null }
$log = Join-Path $logdir "$name.log"
$errf = Join-Path $logdir "$name.err.log"
$pidf = Join-Path $logdir "$name.pid"
$exitf = Join-Path $logdir "$name.exit"
$rel = ".run/logs/$name.log"

function Merge-Err {
  if (Test-Path $errf) {
    $e = Get-Content -LiteralPath $errf -ErrorAction SilentlyContinue
    if ($e) { Add-Content -LiteralPath $log -Value $e -Encoding utf8 }
    Remove-Item -LiteralPath $errf -Force -ErrorAction SilentlyContinue
  }
}

function Write-Report($code) {
  $n = 0
  if (Test-Path $log) { $n = (Get-Content -LiteralPath $log | Measure-Object -Line).Lines }
  Write-Output "exit=$code log=$rel lines=$n"
  if (-not (Test-Path $log)) { return }
  if ($pattern -ne '') {
    Select-String -LiteralPath $log -Pattern $pattern |
      Select-Object -First $tailN | ForEach-Object { "$($_.LineNumber):$($_.Line)" }
  } else {
    Get-Content -LiteralPath $log -Tail $tailN
  }
}

function Read-ExitCode($proc) {
  if (Test-Path $exitf) {
    $v = (Get-Content -LiteralPath $exitf -TotalCount 1)
    if ($null -ne $v -and "$v".Trim() -ne '') { return "$v".Trim() }
  }
  if ($null -ne $proc) {
    try { if ($null -ne $proc.ExitCode) { return "$($proc.ExitCode)" } } catch {}
  }
  return '?'
}

function Start-Child($cmdline) {
  $inner = "$cmdline; `$ok = `$?; `$c = `$LASTEXITCODE; if (`$null -eq `$c) { if (`$ok) { `$c = 0 } else { `$c = 1 } }; Set-Content -LiteralPath '$exitf' -Value `$c -Encoding ascii"
  $proc = Start-Process -FilePath 'powershell' -PassThru -WindowStyle Hidden `
    -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', $inner) `
    -RedirectStandardOutput $log -RedirectStandardError $errf -WorkingDirectory $root
  $null = $proc.Handle        # cache the handle or .ExitCode is lost on PS 5.1
  return $proc
}

if ($mode -eq 'fg') {
  if ($cmd.Count -eq 0) { Deny 'no command after --' }
  Remove-Item -LiteralPath $exitf -Force -ErrorAction SilentlyContinue
  $p = Start-Child ($cmd -join ' ')
  if ($timeoutSec -gt 0) {
    if (-not $p.WaitForExit($timeoutSec * 1000)) {
      try { $p.Kill() } catch {}
      Merge-Err
      Write-Report "timeout(${timeoutSec}s)"
      exit 1
    }
  } else {
    $p.WaitForExit()
  }
  Merge-Err
  $code = Read-ExitCode $p
  Write-Report $code
  if ($code -match '^\d+$') { exit [int]$code }
  exit 0
}

if ($mode -eq 'bg') {
  if ($cmd.Count -eq 0) { Deny 'no command after --' }
  Remove-Item -LiteralPath $exitf -Force -ErrorAction SilentlyContinue
  $p = Start-Child ($cmd -join ' ')
  Set-Content -LiteralPath $pidf -Value $p.Id -Encoding ascii
  Write-Output "bg=$name pid=$($p.Id) log=$rel"
  Write-Output "wait: powershell -File tools/run.ps1 --wait $name --max $maxWait"
  exit 0
}

if (-not (Test-Path $pidf)) { Deny "no $rel pid file (start it with run.ps1 --bg $name)" }
$procId = [int]((Get-Content -LiteralPath $pidf -TotalCount 1).Trim())
$waited = 0
while ($true) {
  $live = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if (-not $live) { break }
  if ($waited -ge $maxWait) {
    Write-Output "still running after ${waited}s (pid $procId) log=$rel"
    if (Test-Path $log) { Get-Content -LiteralPath $log -Tail $tailN }
    exit 0
  }
  Start-Sleep -Seconds 30
  $waited += 30
}
Merge-Err
Write-Report (Read-ExitCode $null)
exit 0
