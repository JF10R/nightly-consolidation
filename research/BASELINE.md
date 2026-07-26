# Re-derivation baseline (kill-test reference, measured before first promotion)

Pre-registered in `skill/references/kill-test.md`. Measured mechanically
from digests (`collect.py`), zero LLM judgment in the numbers. This is the
project's own reference measurement, included as evidence and as an example
of the method — run it again on your own session history if you want your
own numbers.

## Corpus

| corpus | files | notes |
|---|---|---|
| Interactive main transcripts | 57 found, 30 non-trivial digested | ~30-day retention window — Claude Code prunes old transcripts. |

## A. Interactive sessions — cross-session repeated errors (primary baseline)

**16/30 sessions (53%) hit at least one error signature already seen in a different session.**
10 distinct signatures recur across 2 or more sessions. Top offenders (sessions affected, out of 30):

| pattern | sessions |
|---|---|
| Edit/Write "File has not been read yet" | **12** |
| Bash blocked `sleep N && tail/cat` polling | 6 |
| Read "exceeds maximum allowed tokens" (no offset/limit) | 5 |
| Edit/Write "modified since read" | 5 |

This is the **gain ceiling** for the interactive track: at best, consolidation
eliminates these repeats. Post-promotion runs must compare against 53%.

## B. Extended — a local session archive unlocks a longer window

Claude Code purges transcripts after roughly 30 days, but this project's own
local archive (`~/.claude/nightly-consolidation/archive/sessions/<project>/*.br`,
brotli-compressed, same JSONL schema, written by `archive.py`) can retain
sessions well past that window. `collect.py` reads `.br` archives
transparently (a live transcript wins on an id collision if both exist). In
this project's own reference run: 959 archived files spanning roughly 5
months, combined with the live window into **808 unique non-trivial
sessions**.

**Re-derivation at that longer scale: 73% of sessions (591/808) hit an error
signature already seen in an earlier session.** 132 signatures recur; the
lessons this project promoted from that run were validated at that scale and
were never learned spontaneously:

| signature | sessions |
|---|---|
| Edit/Write "File has not been read yet" | 138+51 |
| Read "exceeds maximum tokens" (2 wordings) | 82+19 |
| Read "file does not exist" (+cwd note) | 68+14 |
| Edit/Write "modified since read" | 55 |
| ExitPlanMode plan rejected | 208 (self-resolved after a workflow change partway through the window; closed, no lesson needed) |

Monthly re-hit rate across that window was flat/noisy (roughly 60-80% every
month) — no spontaneous "tenure effect" emerged on its own; that absence is
precisely the gap this skill targets. Caveats on that number: monthly
session counts weren't even across the window (work shifted toward fewer,
longer sessions over time); the underlying model changed across the window
too; a generic `Bash|exit code #` signature (54 occurrences) is a
normalization artifact and excluded from interpretation; a "related tool
call also errored" signature (70 occurrences across 3 tools) is cascade
noise from a single root cause, not an independent signal.

Operational note: `archive.py` already runs as step one of every nightly
job, so this corpus keeps growing past every purge automatically — no
separate tool or sync step to maintain.

## Success criterion recall

Re-derivation rate must trend down vs the 53% session-level baseline (or 73%
over a longer archive-included window, if you have one) AND promoted-lesson
precision must stay at 80% or higher, else the experiment stops and
`learned/KILL-TEST-RESULT.md` documents why. If you're also running another
active mechanism against the same failure patterns (see
`skill/references/kill-test.md`'s note on this), measure its effect
separately before trusting a drop in this number.
