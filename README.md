# nightly-consolidation

An automated, scheduled Claude Code skill that reviews your recent coding
sessions, notices mistakes and improvements, and — once something has
happened more than once — writes it down as a durable lesson in a file
Claude Code automatically loads into every future session. No fine-tuning,
no manual note-taking.

Concretely: it distills what happened in your last N hours of sessions into
three things — durable facts about your environment, lessons learned from
failures (phrased as "when X, do Y instead of Z"), and reusable sequences
that worked — then, once a lesson has recurred across two or more separate
sessions (or you confirm it yourself), writes it into one of two places:

- **`~/.claude/rules/learned.md`** — user scope. Machine-wide lessons that
  would be true in any project (a shell quirk, a tool's usage gotcha) go
  here, and Claude Code loads this file into every session on your machine.
- **The project's own `CLAUDE.md`** — project scope. Facts specific to one
  repo (its paths, commands, structure) go into a fenced section of that
  project's `CLAUDE.md` instead, so they only load when you're working
  there.

Both targets are capped at 30 lines with oldest-first eviction, and every
line records which session it came from. A promoted lesson looks like:

```
- Never poll with `sleep N && tail` in Bash — the harness blocks it; use
  run_in_background and wait for the notification — src:1964320c,35814b78 last-seen:2026-07-18
```

## Why

Baseline measured over 5 months of real session history (808 unique
sessions, pre-registered before any promotion was ever applied):

- **73% of sessions hit a tool error already seen in an earlier session.**
- The single worst pattern ("File has not been read yet before Edit")
  recurred in **189 sessions across 5 months** without ever being learned
  spontaneously.
- Monthly re-hit rate is flat (~60-80%): no tenure effect emerges on its own
  — an agent doesn't get better at avoiding a mistake just because it made
  the mistake before, since each session starts with no memory of it.
- Claude Code purges local transcripts after ~30 days — episodic memory has
  a built-in half-life unless it's archived and distilled somewhere durable.

Full record: `research/BASELINE.md`.

## How it works

A 6-phase pipeline, run nightly (or on any cadence you choose):

1. **Collect** — parse recent session transcripts into compact digests
   (~4KB for a 10-minute session, ~55KB for a 60-hour marathon). Never loads
   raw transcripts into the model's context; mechanical parsing only.
2. **Triage** — classify what happened in each session (success, failure,
   abandoned, meta).
3. **Semantic extraction** — durable facts worth remembering long-term.
4. **Counterfactual extraction** — what would have prevented a failure,
   specifically (not "be more careful" — a concrete, checkable precondition).
5. **Procedural extraction** — sequences that reliably worked, worth
   repeating.
6. **Write + promote** — journal the findings, then promote qualifying
   lessons into `~/.claude/rules/learned.md` or the relevant `CLAUDE.md`.

Promotion is deliberately conservative: a lesson only gets promoted after
**two or more independent session occurrences** (or explicit user
confirmation), and every promoted line **cites its source session** for
auditability. Nothing reads the dated journals in place — promotion into a
loaded file is the only path by which a past session can influence a future
one.

```
skill/                 the Claude Code skill (junction/symlink this into ~/.claude/skills/)
  SKILL.md              the 6-phase pipeline spec, in full
  scripts/collect.py    JSONL → digest. Streams large files, redacts secrets,
                         reads live sessions + a local archive + SDK stream-json
  scripts/archive.py    purge-proof delta-sync to a local brotli archive, so
                         transcripts survive Claude Code's ~30-day purge
  references/kill-test.md   pre-registered abandonment criterion
learned/                journals + ledger state (gitignored — contains real
                         session excerpts, stays local to your machine)
research/               baseline measurements and methodology notes
```

## Architecture principle

Mechanical parsing lives in `scripts/`; judgment (triage, lesson extraction,
promotion decisions) is the model's. The model never reads raw transcripts —
only the compact digests `collect.py` produces.

## Setup

**Requires:** Python 3.9+, the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code), and the `brotli` package. No environment variables to configure — everything resolves relative to wherever you clone this repo.

```bash
git clone <this repo> && cd nightly-consolidation
pip install -r requirements.txt
```

Install the skill:

```powershell
# Windows
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\nightly-consolidation" -Target "$PWD\skill"
```

```bash
# macOS/Linux
ln -s "$PWD/skill" ~/.claude/skills/nightly-consolidation
```

### Or ask Claude to set it up

If you already use Claude Code, paste this into a session and the agent does
the steps above for you:

> Clone https://github.com/JF10R/nightly-consolidation and set it up:
> install the Python dependencies from requirements.txt, link its `skill/`
> directory into `~/.claude/skills/nightly-consolidation` (directory
> junction on Windows, symlink on macOS/Linux), then verify the install by
> running a dry-run consolidation over all of my existing sessions. Don't
> schedule anything yet.

Dry-run means the agent produces the report and journal but touches no
promotion target — a safe first look at what the pipeline finds. This
first sweep is the biggest run you'll ever do: it covers every session
still on disk (Claude Code purges transcripts after ~30 days), and it
primes the ledger so every later run is incremental. To then put it on a
schedule:

> Read ops/SCHEDULING.md in my nightly-consolidation clone and schedule the
> nightly run daily at 03:00 with my platform's scheduler (Task Scheduler on
> Windows, cron elsewhere). Trigger it once manually and show me the log.

## Running it

Invoked from a normal Claude Code session ("run nightly consolidation"),
the skill defaults to a dry-run report. The headless command below runs
apply mode — it consolidates the last 24h and writes qualifying promotions
(run it from inside your clone):

```bash
python skill/scripts/archive.py && \
  claude -p "Use the nightly-consolidation skill: consolidate the last 24h, apply mode." \
  --allowedTools "Read Glob Grep Bash(python *) Write(learned/**) Edit(learned/**) Edit(./**/CLAUDE.md) Edit(~/.claude/rules/learned.md)" \
  --permission-mode dontAsk --output-format json
```

Run it however fits your workflow — Task Scheduler, cron, or a Claude
Desktop scheduled task all work; `ops/run-nightly.ps1` is a ready-to-use
wrapper that auto-detects its own location, plus Windows Task Scheduler
instructions in `ops/SCHEDULING.md`. The `--allowedTools` restriction
matters: this runs headless (`dontAsk`), so scope it tightly.

## Configuration

No required environment variables. Two optional ones, only relevant if you
run this from a scheduler with a trimmed PATH (`ops/run-nightly.ps1` falls
back to plain `python`/`claude` on PATH otherwise):

| Variable | Required | Purpose |
|---|---|---|
| `PYTHON_EXE` | no | Absolute path to a specific Python interpreter, if `python` isn't reliably on PATH under your scheduler. |
| `CLAUDE_EXE` | no | Same idea, for the `claude` CLI. |

**Which model runs it:** the bare `claude -p` command under "Running it"
uses whatever default model your Claude Code is configured with. The
shipped scheduler wrapper (`ops/run-nightly.ps1`) instead pins an explicit
model and effort — `--model "claude-opus-5[1m]" --effort xhigh` as shipped;
edit to taste — because a scheduled run that writes lessons into every
future session deserves a deterministic model choice, not whatever the
interactive default happens to be that day. Either way, the run's JSON
output records the model that actually ran (`modelUsage`), so promotion
quality stays attributable to a specific model rather than assumed
constant across upgrades.

## Kill-test

This project ships with a pre-registered abandonment criterion, not just
an aspiration: a day-14 checkpoint (14 days from your first apply-mode run)
compares your re-derivation rate against the pre-registered baseline (73%
over 5 months / 53% over a live-only 30-day window). If the rate hasn't
trended down and promoted-lesson precision isn't ≥80%, the experiment stops
and the negative result gets documented — no moving goalposts. See
`skill/references/kill-test.md` and `research/BASELINE.md`.

## License

MIT — see `LICENSE`.
