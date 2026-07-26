# Kill-test — nightly-consolidation

Two-week evaluation window from the first `--apply` run. Pre-registered before
any consolidated knowledge existed, to keep the abandonment criterion honest.

## Hypothesis

Consolidating episodic session memory into `learned/` + a capped CLAUDE.md
section measurably reduces cross-session re-derivation (sessions re-paying
discoveries already made) at acceptable cost and without poisoning context
with wrong or stale lessons.

## Metrics

### 1. Re-derivation rate (primary)

Definition: fraction of new sessions that re-discover a fact already present
in `learned/` at session start.

**Note if you run another active mechanism on the same failure patterns:**
if something else in your setup (e.g. a PreToolUse hook) is also actively
suppressing some of the same failure signatures this skill tracks passively,
a day-14 drop in re-derivation is unattributable between the two mechanisms
without separating their effects first — "hooks enforce, text reminds"
predicts the hook would get all the credit even if the consolidation fence
did nothing. If that applies to you, measure the two mechanisms' effects
separately before trusting this checkpoint's number. If you don't run
anything like that, this doesn't apply — proceed as below.

Measurement: during each consolidation run, for every semantic fact extracted
today, check whether an equivalent fact already exists in a prior journal.
If yes → count as a re-derivation event (the earlier consolidation failed to
prevent the re-payment). Report per run:

```
re-derivation: X facts re-derived / Y facts extracted (rate = X/Y)
```

Baseline: run the first consolidation over a multi-day window (`--since 72`
or more) BEFORE any promotion, and count duplicate discoveries across those
sessions. That duplicate rate is the ceiling of what consolidation can save.

MEASURED (pre-promotion, this project's own reference run): 53% over a
30-day live window (16/30 sessions); 73% over 5 months (591/808) once the
local `.br` archive (see `archive.py`) is included. Full record:
`research/BASELINE.md`.

Success: day-14 re-derivation rate trends down vs baseline (73%, or 53% if
you're only measuring against a live-only window without an archive).
Failure: flat or rising after 2 weeks with ≥10 sessions consolidated.

### 2. Precision of promoted lessons

Definition: of the lines promoted to CLAUDE.md, how many turned out wrong,
stale, or harmful (a session followed the lesson and it hurt, or the user
deleted/contradicted it).

Measurement: at each run, re-verify every promoted line still matches reality
(paths exist, commands still behave as described — spot-check cheaply). Track:

```
precision: (promoted - retracted - contradicted) / promoted
```

Success: ≥80%. Failure: <80% at day 14 → stop.

### 2b. Per-line efficacy analysis (report-only, not a gate)

Addition to the day-14 checkpoint REPORT; the pass/fail criterion above is
untouched (this is finer-grained attribution, not a goalpost move). The fence
already updates `last-seen` on every re-observation, so the data is free.

For every line promoted to a fence (`~/.claude/rules/learned.md` +
project-scope fences), classify at day-14 using promotion date vs `last-seen`:

- **RECURRING** — `last-seen` advanced after promotion: the lesson as TEXT is
  not preventing the failure ("hooks enforce, text reminds") → candidate for
  a real enforcement mechanism (e.g. a PreToolUse hook), if you have one, or
  for rewording.
- **QUIET** — never re-seen since promotion: either effective or obsolete;
  cross-check with the existing 14-day decay policy (Known threats below) —
  verify-or-evict, don't count as a win by default (prompt-presence ≠ causal
  use).
- **PREVENTIVE-USE** — positive sighting of the lesson being applied before
  the failure would have occurred (e.g. a fix from a lesson applied
  proactively, before hitting the error it prevents): the only per-line
  evidence of causal effect; record it.

Report as a table (line, promoted-on, last-seen, class, action).

### 3. Consolidation cost (secondary)

Per run, appended to the journal: sessions parsed, wall-clock duration,
approximate tokens consumed by the consolidation session itself. If the
nightly cost exceeds the plausible savings (re-derived discoveries are
typically 1–10 min of session time each), the economics are negative even if
the metrics pass.

## Abandonment criterion (binding)

At day 14: if the re-derivation rate has not decreased vs baseline (73%/53% —
`research/BASELINE.md`), OR promoted-lesson precision <80%,
the experiment STOPS. Document in `learned/KILL-TEST-RESULT.md`:
what was measured, why it failed, what (if anything) survives (e.g. the
digest tooling may be worth keeping for forensics even if promotion fails).

## Known threats to validity

- Low session volume on a given project → extend window rather than judge early.
- Lessons about churning code go stale silently → retention policy: any
  promoted line not re-observed for 14 days is a decay candidate; verify or
  evict. (A wrong lesson in context is worse than no lesson.)
- The consolidation session itself appears in transcripts → excluded via the
  live-session guard and by state marking; do not consolidate consolidation
  sessions (title/prompt mentions the skill → classify trivial, skip).
- Prompt-presence ≠ causal use: a fact being in CLAUDE.md doesn't prove the
  session used it. Re-derivation rate measures the observable proxy
  (did the session re-pay the discovery), not adoption.
