param([switch]$Force)
$ErrorActionPreference = "Stop"
# Auto-detected from this script's own location (ops/run-nightly.ps1 -> repo
# root is one level up) — no env var needed, and correct no matter what cwd
# Task Scheduler invokes this with.
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force "$root/ops/logs" | Out-Null
Set-Location $root

# Anti-duplicate guard: if this task has multiple triggers (e.g. daily 03:00 +
# logon + unlock), only consolidate once per ~24h. The ledger dedupes sessions
# regardless, but every claude run costs money; skip if the last log is under
# 20h old. -Force overrides (manual run: powershell -File run-nightly.ps1 -Force).
$logDir = "$root/ops/logs"
$lastLog = Get-ChildItem $logDir -Filter *.log -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Force -and $lastLog -and ((Get-Date) - $lastLog.LastWriteTime).TotalHours -lt 20) {
  Write-Output "skip: last run $($lastLog.Name) is under 20h old (multi-trigger guard)"
  exit 0
}

# Paths resolved via env var override, no PATH dependency in a Task Scheduler
# context (see ops/SCHEDULING.md) — set $env:PYTHON_EXE as a persistent env var
# if `python` isn't on the scheduled task's PATH; defaults to relying on PATH.
$pythonExe = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "python" }
& $pythonExe "$root/skill/scripts/archive.py"
# 72h window (not 24h): the .state.json ledger dedupes already-processed
# sessions, so the overlap is free, and up to 3 missed nights (machine off)
# catch up automatically on the next run (if your scheduler supports a
# start-when-available / catch-up option).
# NB: no Edit rule on ~/.claude — protected path, unconditional deny under
# dontAsk. Machine-wide promotion goes through the staging step below instead.
# Effort is EXPLICIT rather than inherited from interactive settings, since
# this script's promotions affect every future session, not just this one.
# The model is pinned (rather than left to drift with the interactive
# default) for the same reason: a scheduled run that writes to shared files
# deserves a deterministic model choice, not whatever the default happens to
# be that day. The JSON log still records modelUsage either way.
# Log in UTF-8 (not PowerShell 5.1's *> UTF-16 BOM) so it stays greppable.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$logFile = "$root/ops/logs/$(Get-Date -Format yyyy-MM-dd_HHmmss).log"
# ErrorActionPreference = Continue for the duration of the pipe: on PS 5.1,
# native stderr redirected via 2>&1 under EAP Stop throws NativeCommandError
# and kills the script. Stringify to avoid ErrorRecord formatting in the log.
$prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
# $env:CLAUDE_EXE as a persistent env var if `claude` isn't on the scheduled task's PATH.
$claudeExe = if ($env:CLAUDE_EXE) { $env:CLAUDE_EXE } else { "claude" }
& $claudeExe -p "Use the nightly-consolidation skill: consolidate the last 72h (the state ledger skips already-processed sessions), apply mode. Machine-wide promotions: ~/.claude is a protected path (any Edit there will be denied) - instead write the COMPLETE updated fence body (every line that belongs between the learned:start/end markers, including your additions and any LRU evictions) to learned/pending/learned-fence.md; the scheduler applies it after the run." `
  --allowedTools "Read Glob Grep Bash(python *) Bash(grep *) Write(learned/**) Edit(learned/**) Edit($root/**/CLAUDE.md)" `
  --model "claude-fable-5[1m]" --permission-mode dontAsk --effort max --output-format json 2>&1 |
  ForEach-Object { "$_" } | Out-File -FilePath $logFile -Encoding utf8
$ErrorActionPreference = $prevEAP

# Apply the machine-wide staging (outside the agent — the only sanctioned way
# to write under ~/.claude from a headless run; the agent already made the
# LRU/30-line-cap judgment call when it wrote the staged file).
$staging = "$root/learned/pending/learned-fence.md"
if (Test-Path $staging) {
  $target = "$HOME/.claude/rules/learned.md"
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  $content = [System.IO.File]::ReadAllText($target, $utf8)
  $body = [System.IO.File]::ReadAllText($staging, $utf8).TrimEnd("`r", "`n")
  $startIdx = $content.IndexOf('<!-- learned:start')
  $bodyStart = $content.IndexOf("`n", $startIdx) + 1
  $endIdx = $content.IndexOf('<!-- learned:end -->')
  if ($startIdx -ge 0 -and $endIdx -ge $bodyStart) {
    $new = $content.Substring(0, $bodyStart) + $body + "`n" + $content.Substring($endIdx)
    [System.IO.File]::WriteAllText($target, $new, $utf8)
    Move-Item $staging "$staging.applied-$(Get-Date -Format yyyy-MM-dd)" -Force
  } else {
    Write-Warning "fence markers learned:start/end not found in $target - staging NOT applied"
  }
}
