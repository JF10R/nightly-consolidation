# Scheduling nightly-consolidation

Two options for running this on a schedule. Neither is required — you can
always just run it manually. Pick one mechanism; don't run both (see
"Don't run both" below).

## The command being scheduled

```bash
python skill/scripts/archive.py && claude -p "Use the nightly-consolidation skill: consolidate the last 24h, apply mode." --allowedTools "Read Glob Grep Bash(python *) Write(learned/**) Edit(learned/**) Edit(./**/CLAUDE.md) Edit(~/.claude/rules/learned.md)" --permission-mode dontAsk --output-format json
```

Run this from inside your clone — the `archive.py` path and `claude -p`'s
working directory both assume that.

**Why two steps?** `archive.py` runs outside the agent first (pure Python,
zero tokens): it's a purge-proof delta-sync to this project's own local
archive. Then the headless `claude -p` call runs from the project root so its
`CLAUDE.md`/rules load and `learned/`-relative paths resolve. `dontAsk`
auto-denies anything outside `--allowedTools` instead of prompting, since a
headless run can't answer a prompt. Never use
`--dangerously-skip-permissions`.

## Option A: Windows Task Scheduler

Runs natively, whether or not Claude Desktop is open. Requires the `claude`
CLI to be on PATH for the account that owns the scheduled task — verify with
`where claude` in the same shell context the task will run under, since a
scheduled task can run with a different (often trimmer) PATH than an
interactive shell. Test with `schtasks /Run` (below) before trusting an
unattended overnight run. If PATH resolution fails under the scheduled task,
set `$env:CLAUDE_EXE` (and `$env:PYTHON_EXE`, if needed) to an absolute path
as an override — see `run-nightly.ps1`.

**Step 1 — the wrapper script.** Already provided as `ops/run-nightly.ps1`
in this repo (this avoids the quoting problems of putting the whole command
directly in a scheduled task's arguments). It auto-detects the repo root
from its own location, so no configuration is needed and it's correct
regardless of what working directory Task Scheduler invokes it with. It
creates `ops/logs/` itself on first run — the log file is the only way to
inspect a headless run's output afterward.

**Step 2 — register the task** (run once; elevated PowerShell is not
required for a per-user task). Replace `<path to your clone>` with your
actual clone path — `schtasks` needs a literal path here, it can't run the
auto-detection first:

```
schtasks /Create /TN "nightly-consolidation" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File <path to your clone>\ops\run-nightly.ps1" /SC DAILY /ST 03:00 /RL LIMITED
```

Alternative registration (`Register-ScheduledJob`, keeps it in PowerShell's
own job store instead of raw Task Scheduler XML — pick one, not both):

```powershell
$trigger = New-JobTrigger -Daily -At "3:00 AM"
$options = New-ScheduledJobOption -StartIfOnBattery -RunElevated:$false
Register-ScheduledJob -Name "nightly-consolidation" -FilePath "<path to your clone>\ops\run-nightly.ps1" -Trigger $trigger -ScheduledJobOption $options
```

Verify after install:

```
schtasks /Query /TN "nightly-consolidation" /V /FO LIST
schtasks /Run /TN "nightly-consolidation"     # manual trigger, don't wait overnight to test
```

Remove:

```
schtasks /Delete /TN "nightly-consolidation" /F
```

## Option B: Claude Desktop scheduled task

Claude Desktop's own scheduler (Settings > Scheduled tasks), or a
cloud-scheduled agent if your Claude Code install offers one. Only runs
while the mechanism it's registered under is available — a desktop-app
scheduled task needs the machine to be on; a cloud-scheduled one runs
regardless of local machine state. Check which one you actually have before
assuming "the desktop app must stay open."

Advantages over Task Scheduler: no PATH/env debugging, same account context
as interactive use, visible run history in the app instead of a log file.
Disadvantage: another moving part outside this repo's version control — the
command lives in Desktop's own config, not in a file here, so if you pick
this option, keep the command in sync with what's documented above.

Setup (exact steps depend on your installed Claude Desktop version — check
the current UI rather than following this blindly):

1. Claude Desktop > Settings > Scheduled tasks (or, from a Claude Code
   session, a `schedule`-type skill/command if your install has one for
   creating cron-scheduled cloud agents without leaving the CLI).
2. New task, cron `0 3 * * *` (03:00 daily — match option A's timing if
   you're only trying one at a time).
3. Prompt: use the `claude -p` instruction text from "The command being
   scheduled" above, not the full shell command — a Desktop scheduled task
   runs inside an agent context, not a raw shell, so the `archive.py` step
   needs its own tool call inside the task prompt, e.g. "First run
   `python skill/scripts/archive.py`, then use the nightly-consolidation
   skill: consolidate the last 24h, apply mode."
4. Constrain tools the same way as `--allowedTools` if the UI exposes a
   permission/tool-scope setting. If it doesn't, this option is less safe
   than Task Scheduler + `dontAsk` — worth knowing before you rely on it.

## Don't run both

Installing both options runs consolidation twice nightly: duplicate journal
entries, wasted tokens, and a race on `learned/.state.json` (last writer
wins, no lock). If you want to evaluate both, stagger the trial (one option
this week, the other next week) rather than running them concurrently.

## Reusing this pattern for other scheduled jobs

The same wrapper pattern — a script that auto-detects its own root, an
archive/sync step outside the agent, then a headless `claude -p` call —
generalizes to any other scheduled job you want to run this way. Swap the
command, keep the pattern.
