---
name: nightly-consolidation
description: Nightly memory consolidation for Claude Code — distill the last 24h of sessions into durable semantic facts, procedural patterns, and counterfactual lessons from failures. Use whenever the user says "consolidate", "consolidation", "nightly review", "what did we learn", "review yesterday's sessions", "what went wrong recently", or when a scheduled/headless run asks to consolidate sessions. Also use when the user asks why the agent keeps repeating the same mistakes across sessions.
---

# Nightly Consolidation

Agents have no sleep: every session starts from zero and re-pays the same
discoveries. This skill is the offline consolidation phase — it re-reads the
last 24h of Claude Code session transcripts, distills episodic memory (what
happened) into semantic memory (durable facts about the repo/environment) and
procedural memory (sequences that work), extracts **counterfactual lessons
from failures**, and updates persistent artifacts that future sessions read.
Continuous learning without fine-tuning.

**Architecture principle: mechanical parsing goes in scripts; judgment goes to
the model.** `scripts/collect.py` parses raw JSONL transcripts (which can be
40MB+) into compact per-session digests. You NEVER load raw transcripts into
context — only digests.

## Artifacts

All journals and state live under the repo root's `learned/` (nothing reads
them in place — only this consolidation pass and the human):

```
LEARNED = <repo root>/learned/
  YYYY-MM-DD.md      journal per run — everything goes here (triage, facts,
                     lessons, candidates, decisions), organized in
                     `## <project>` sections + a `## machine-wide` section
  .state.json        single machine-wide processed-session ledger
  .digests/          transient digest JSONs for the current run (gitignored)
```

Promotion targets (the ONLY things written outside LEARNED):
```
~/.claude/rules/learned.md      machine-wide lessons — auto-loaded everywhere
<project>/CLAUDE.md             project-specific facts — the fenced section
                                between <!-- learned:start/end --> only
```
Both fences: hard cap 30 lines each.

**Two-tier promotion routing:** a promoted item goes to exactly ONE target.
Machine-wide (harness errors, OS/shell quirks, patterns observed across ≥2
projects) → `~/.claude/rules/learned.md`. Project-specific (this repo's
paths, commands, structure) → that project's CLAUDE.md fence. When in doubt:
if the lesson would be true in an empty new repo, it's user-scope.

## Hard guardrails (read before doing anything)

1. **Dry-run is the default.** Produce the report, write the journal to
   LEARNED, but NEVER touch a promotion target without either (a) explicit
   user confirmation in this conversation, or (b) the invocation explicitly
   saying "apply mode" / `--apply`.
2. **Never modify anything outside LEARNED, the fenced `<!-- learned -->`
   section of a project's CLAUDE.md, and the fence in
   `~/.claude/rules/learned.md`.** No source files, no other docs, no
   settings, no hooks.
3. **One occurrence = candidate, not rule.** Promotion to CLAUDE.md requires
   ≥2 independent occurrences (different sessions) or explicit user
   confirmation. Calibrate wording to evidence strength ("in session X, Y
   failed" vs "Y always fails").
4. **Every written line cites its source session** (first 8 chars of the
   session id are enough).
5. **No secrets, tokens, credentials, or personal data** in any written
   artifact. collect.py redacts mechanically; you are the second filter —
   if a fact's value looks like a credential, drop the fact.
6. **One lesson per failure, maximum.** No psychological speculation — only
   what the transcript shows.
7. CLAUDE.md learned section: hard cap 30 lines. When full, evict the least
   recently useful line (oldest `last-seen` date) to make room.
8. **Tooling is proposals-only, always.** Skills, agent definitions, and
   slash commands are executable behavior — consolidation may propose
   (journal `## Tooling proposals`) but NEVER create or edit them, even in
   apply mode. User confirmation → scaffold in a normal session.

## Pipeline

### Phase 1 — Collect

First, refresh the purge-proof archive (Claude Code deletes transcripts after
~30 days; this delta-syncs them to `~/.claude/nightly-consolidation/archive/sessions/` as brotli):

```bash
python ~/.claude/skills/nightly-consolidation/scripts/archive.py
```

Then run the collector (Python 3.8+, stdlib only). Default sweep = ALL
projects, last 24h (`--since N` for N hours; `--project <encoded>` to narrow).

```bash
LEARNED=learned  # relative to the repo root — run this from inside your clone
python ~/.claude/skills/nightly-consolidation/scripts/collect.py \
  --all-projects --since 24 --state $LEARNED/.state.json --mark-processed \
  --output-dir $LEARNED/.digests
```

Notes:
- Idempotent: sessions already in `.state.json` (same file size) are skipped.
  `--include-processed` overrides.
- Sessions modified <20 min ago are skipped (likely live); `--include-live`
  overrides. The current session (yours) will thus normally be excluded.
- `--session <uuid>` parses a single session regardless of filters.
- All findings go in ONE journal under LEARNED, in `## <project>` sections
  (plus `## machine-wide`); only PROMOTION routes to a specific project's
  CLAUDE.md fence or to ~/.claude/rules/learned.md.
- Transcripts older than ~30 days are purged by Claude Code, but this
  project's own archive (`~/.claude/nightly-consolidation/archive/sessions/<project>/*.br`,
  brotli, same schema, written by `archive.py`) survives the purge;
  collect.py reads `.br` transparently:
  `--projects-root ~/.claude/nightly-consolidation/archive/sessions --all-projects`.
  Requires the Python `brotli` package. For historical/baseline sweeps, merge
  both roots and dedupe by session id (live jsonl wins).
- If zero digests are produced, say so and stop — do not invent a report.
- Read the digests from `learned/.digests/`. If a digest is large (marathon
  session), read it in slices; the summary fields (counts, error_samples,
  corrections, final_assistant, away_summary) come first and often suffice.

Digest fields you get per session: `session_id`, `title`, `cwd`, `git_branch`,
`duration_min`, `models`, `user_prompts`, `tool_calls` (per-tool counts),
`tool_errors` + `error_samples`, `corrections` (user messages matching
correction heuristics — noisy, judge them) + `corrections_total`,
`interruptions`, `test_signal` (pass/fail hint from Bash results only),
`git_commits`, `output_tokens`, `subagents`, `away_summary`,
`final_assistant`, and a chronological `events` trace (truncated, collapsed).

### Phase 2 — Triage

Classify each session: **success / failure / abandoned / ambiguous**.
Heuristics (from digest, none is conclusive alone — you judge):

- repeated tool errors on the same command → failure signal
- multiple genuine user corrections (read `corrections`; discard false
  positives — the regex over-triggers on words like "not"/"actually")
- `interruptions` > 0 mid-work → frustration or redirect
- session ends with no commit and `test_signal == "fail"` → likely abandoned
- `final_assistant` / `away_summary` state a completed deliverable → success
- trivial sessions (a question answered, config tweak) → classify success and
  skip deep analysis

Produce a triage table (session id8, title, class, one-line rationale).

### Phase 3 — Semantic extraction (facts)

From ALL sessions (not just failures), extract durable facts that were
discovered at high cost and will be true next week:

- real versions and paths (tool versions, binary locations, config files)
- commands that work — and commands that fail on this machine, with the error
- environment quirks (OS, shell, encoding, permissions, network)
- repo structure facts that took exploration to learn

NOT facts: anything derivable in 5 seconds from the repo itself, one-off
states (branch names, in-flight work), opinions. Each fact carries provenance:
`— source: <id8>`.

### Phase 4 — Counterfactual lessons (failures only)

For each failure/abandoned session, re-read its digest and locate the
**divergence point**: the first wrong assumption, the first ignored signal,
or the missing fact that sank the trajectory. Then write ONE conditional,
actionable rule:

```
- When <context/trigger>, do <Y> instead of <Z>. — source: <id8> (YYYY-MM-DD)
```

Rules for lessons:
- grounded strictly in what the transcript shows; no speculation about intent
- actionable by a future session that has NOT read the transcript
- if you cannot locate a divergence point, write no lesson for that session
- max one lesson per failure

### Phase 5 — Procedural & tooling extraction

Beyond facts and lessons, transcripts show whether the TOOLING layer
(skills, agent definitions) matches how work actually happens. Use the
digest fields `skills_used`, `agent_types`, `subagents`, plus events.
Signals to look for (each needs ≥3 occurrences in DIFFERENT sessions,
except staleness which needs 1 confirmed contradiction):

- **Skill creation**: recurring successful action sequence with no skill
  covering it (a build-unlock-rebuild dance, a deploy-verify pipeline), OR
  the user repeatedly typing the same multi-step instructions manually.
- **Skill adjustment / misfire**: a skill invoked and shortly followed by
  user correction or interruption; sessions where agents contradict a
  skill's instructions; a skill that should have triggered but didn't
  (user manually did what its description covers → propose better trigger
  keywords in the description).
- **Skill staleness**: a skill's documented commands/paths/flags failing in
  transcripts (reality moved, skill didn't). One confirmed contradiction
  suffices to propose the fix.
- **Agent definitions**: repeated `general-purpose` spawns with
  near-identical prompts → candidate for a named agent type with the right
  tool restrictions; existing agent types that consistently error or get
  respawned with corrections → adjustment proposal.
- **Tooling drift**: heavily-used skills/plugins that silently stopped
  appearing (disabled, renamed, broken) — surface it; the user decides if
  the loss was deliberate.

Write findings in the journal under `## Tooling proposals`, each with:
occurrence count, source sessions, target scope (project `.claude/skills/`
or `.claude/agents/` vs user `~/.claude/skills/` or `~/.claude/agents/` —
same test as lessons: true in an empty repo → user scope), and for
adjustments a concrete diff sketch of the change.

**HARD RULE: consolidation NEVER creates or edits a skill, agent definition,
or slash command — not even in apply mode.** A wrong lesson line is inert
prose; a wrong skill edit changes executable behavior. Proposals are
journal-only until the user explicitly confirms one; scaffolding then
happens in a normal session.

### Phase 6 — Write + controlled promotion

1. Write the journal `LEARNED/YYYY-MM-DD.md` (today's date). Template:

```markdown
# Consolidation — YYYY-MM-DD (window: last Nh, M sessions across P projects)

## Triage
| session | project | title | class | rationale |
|---------|---------|-------|-------|-----------|

## <project-A>            (one section per project that yielded items)
### Semantic facts
- <fact> — source: <id8>
### Counterfactual lessons
- When <context>, do <Y> instead of <Z>. — source: <id8>

## machine-wide           (items true in any repo)
- <fact/lesson> — sources: <id8>, <id8>

## Procedural candidates
- <sequence> (N occurrences: <id8>, <id8>, <id8>) — awaiting confirmation

## Tooling proposals
- CREATE/ADJUST/STALE/DRIFT <skill-or-agent> (scope: project|user, N occurrences: <id8>…) — <what + diff sketch> — awaiting confirmation

## Promotion decisions
- PROMOTED (→ target) / HELD (needs 2nd occurrence) / REJECTED / CLOSED — per item, with reason

## Run cost
- sessions parsed, digest bytes, approximate tokens consumed by this run
```

2. **Promotion pass**: scan previous journals in LEARNED for items
   recurring today (≥2 total occurrences) or previously user-confirmed.
   Only those are promotion-eligible. Per-project follow-up = grep the
   project's section header across dated journals.

3. **Promotion write** (only with confirmation or apply mode). Route per the
   two-tier rule: machine-wide → `~/.claude/rules/learned.md`, project-specific
   → the project's CLAUDE.md. Same mechanics for both targets:
   - If the fenced section doesn't exist, append to the target file:
     ```
     <!-- learned:start (managed by nightly-consolidation — do not hand-edit inside) -->
     <!-- learned:end -->
     ```
   - Each line: `- <fact/lesson> — src:<id8>,<id8> last-seen:YYYY-MM-DD`
   - If an existing line is re-observed, update its `last-seen` and add the
     new source id instead of duplicating.
   - Enforce the 30-line cap by evicting oldest `last-seen`.
   - Never touch anything outside the fence.
   - HEADLESS ONLY: `~/.claude` is a protected path (Edit denied under dontAsk) —
     write the complete updated fence body to `learned/pending/learned-fence.md`
     instead; the scheduler applies it (see Headless gotchas below).

4. Present the report: triage table, facts, lessons, promotion decisions,
   run cost. In dry-run, end by asking whether to apply the CLAUDE.md
   promotions.

## Headless / nightly run

Manual in-session: "run nightly consolidation" (dry-run) or "… apply mode".

Headless (cron / n8n / Task Scheduler) — most restrictive permission set that
still allows the writes (verified against `claude --help` 2.1.209; re-check
`--allowedTools` rule syntax after CLI upgrades):

```
python ~/.claude/skills/nightly-consolidation/scripts/archive.py && \
cd /path/to/your/clone && \
claude -p "Use the nightly-consolidation skill: consolidate the last 24h, apply mode. Machine-wide promotions: write the COMPLETE updated fence body to learned/pending/learned-fence.md (~/.claude is protected, Edit there is denied); the scheduler applies it after the run." \
  --allowedTools "Read Glob Grep Bash(python *) Write(learned/**) Edit(learned/**) Edit(./**/CLAUDE.md)" \
  --permission-mode dontAsk --output-format json
```

Headless gotchas (verified empirically; docs: permission-modes → "Protected paths"):
- `~/.claude/**` is a PROTECTED PATH: under `dontAsk`, Edit/Write there is denied
  UNCONDITIONALLY — the check runs BEFORE allow rules, so no `--allowedTools` form
  (`~/...`, `C:/...`, `//c/...`) can authorize it. Machine-wide promotion therefore
  goes through a STAGING file: the agent writes the complete updated fence body
  (additions + LRU evictions — the judgment stays with the agent) to
  `learned/pending/learned-fence.md`, and the scheduler (`ops/run-nightly.ps1`)
  splices it between the learned:start/end markers after the run, outside the agent.
  Interactive sessions edit `~/.claude/rules/learned.md` directly as before.
- `Bash(python *)` does not match heredoc forms (`python - <<'EOF'`) — the
  redirect operator defeats prefix matching. Write helper scripts to
  `learned/.digests/` (covered by `Write(learned/**)`) and run `python <file>`.

(The archive step runs outside the agent — pure Python, no tokens. Running
from this project's root loads its CLAUDE.md rules and makes `learned/`
relative paths resolve to LEARNED.)

`dontAsk` auto-denies anything outside the allowlist instead of prompting
(headless runs cannot answer prompts). Do NOT use `--dangerously-skip-permissions`.

## Kill-test

This skill ships with a pre-registered 2-week kill-test:
`references/kill-test.md`. Every run must append its cost line to the journal
so the test stays measurable. If after 2 weeks the re-derivation rate has not
dropped or promoted-lesson precision is <80%, the experiment stops and the
outcome is documented.
