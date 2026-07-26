# nightly-consolidation

Instructions for agents (and humans) working in this repository.

## What this is

A Claude Code skill, plus supporting scripts, that consolidates Claude Code
session transcripts into durable memory: semantic facts, procedural
patterns, and counterfactual lessons from failures, written into the files
future sessions actually load (`~/.claude/rules/learned.md` and per-project
`CLAUDE.md` fences).

## Layout

- `skill/` — the Claude Code skill
  - `SKILL.md` — the 6-phase pipeline and guardrails (the agent-facing spec)
  - `scripts/collect.py` — JSONL to digest. Streams, never loads a whole
    transcript into context; reads live sessions, the local brotli archive,
    and SDK stream-json transcripts.
  - `scripts/archive.py` — purge-proof delta-sync to a local brotli archive
    owned by this project.
  - `references/kill-test.md` — pre-registered metrics and abandonment
    criterion.
- `learned/` — journals plus a `.state.json` ledger (gitignored: contains
  real session excerpts, stays local, never tracked). Nothing reads a
  journal in place — promotion into a fence is the only path from a journal
  into a future session's context.
- `research/` — baseline measurements and methodology notes.

## Data sources

- Live transcripts: `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` — purged
  after roughly 30 days by Claude Code; subagent transcripts nested under
  `<uuid>/subagents/`.
- Local archive: `~/.claude/nightly-consolidation/archive/sessions/<project>/<uuid>.jsonl.br` —
  brotli-compressed, survives the purge, written by `scripts/archive.py`.

## Rules

**Mechanical parsing vs. judgment.** Mechanical parsing lives in `scripts/`;
judgment (triage, lesson extraction, promotion) is the model's. Never add an
LLM call to `collect.py` or `archive.py`. Never make the model read a raw
transcript — only the digests `collect.py` produces.

**Parse defensively.** Transcript schemas are undocumented and drift across
Claude Code versions: ignore unknown line types, wrap JSON parsing per-line
in a try/except, tolerate message content as either a string or an array.
After a Claude Code major-version upgrade, re-verify parsing on a fresh
sample before trusting a run.

**Promotion discipline.** A lesson is promoted only after two or more
independent occurrences (distinct sessions) or explicit user confirmation.
Two-tier routing: machine-wide lessons go to `~/.claude/rules/learned.md`;
project-specific facts go to that project's own `CLAUDE.md`, inside a
fenced section. Both targets have a hard cap of 30 lines with
least-recently-seen eviction, and every promoted line cites its source
session.

**Tooling proposals only.** The pipeline's final phase also evaluates the
tooling layer itself (skills, agent definitions): whether one should be
created, adjusted, or is stale. It writes journal proposals only —
consolidation never creates or edits a skill, agent, or command, even in
apply mode, since executable behavior is a different risk class than inert
prose. A user confirming a proposal is what turns it into an actual change,
in a normal session.

**Kill-test binding.** At the day-14 checkpoint from your first apply-mode
run, the re-derivation rate must be trending down and promoted-lesson
precision must stay at 80% or higher, else the experiment stops and
`research/KILL-TEST-RESULT.md` documents why. The baseline is pre-registered
before any promotion, so the threshold doesn't move after the fact.
