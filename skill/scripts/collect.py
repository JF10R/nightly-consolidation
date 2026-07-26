#!/usr/bin/env python3
"""collect.py — Parse Claude Code session transcripts into compact per-session digests.

Part of the nightly-consolidation skill. Mechanical parsing only: no judgment,
no LLM calls. Produces one JSON digest per session so the model never has to
load raw transcripts (which can be 40MB+) into context.

Transcript layout (Claude Code 2.1.x, parse defensively — schemas drift):
  ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl   main transcript
  ~/.claude/projects/<encoded-cwd>/<session-uuid>/subagents/agent-*.jsonl
  Line types seen: user, assistant, system, attachment, ai-title, last-prompt,
  mode, permission-mode, bridge-session, queue-operation, file-history-snapshot,
  agent-name, agent-setting, custom-title. Unknown types must be ignored, not fatal.

Usage:
  python collect.py                          # current project, last 24h
  python collect.py --since 72               # last 72h
  python collect.py --all-projects --since 24
  python collect.py --project D--path-to-some-project
  python collect.py --state learned/.state.json --mark-processed
  python collect.py --session <uuid>         # single session, ignores filters
Output: JSON array of digests on stdout (or --output-dir for one file per session).
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

MAX_TEXT = 300          # truncation for user prompts / errors
MAX_FINAL_TEXT = 700    # final assistant message gets more room
MAX_EVENTS = 150        # per-session event cap (head+tail split beyond this)
MIN_IDLE_MINUTES = 20   # skip sessions still being written (likely live)

# ---------------------------------------------------------------- redaction

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|passwd|secret|token|api[_\-]?key)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
]


def redact(text):
    if not isinstance(text, str):
        return text
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def clip(text, n=MAX_TEXT):
    text = redact(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n] + ("…" if len(text) > n else "")


# ---------------------------------------------------------------- heuristics

CORRECTION_RX = re.compile(
    r"(?i)\b(no[,.]|not what|wrong|stop|don'?t|undo|revert|instead|actually[, ]|"
    r"why did you|you should have|i said|i asked|non[,.]|pas ça|pas ce que|arrête|"
    r"annule|reviens|recommence|c'est pas|mauvais|erreur de ta part|je t'ai dit|"
    r"je t'avais dit|relis|tu n'as pas)"
)
INTERRUPT_MARKERS = ("[Request interrupted by user", "[Request cancelled")
# Test signal: strict patterns, and only evaluated on Bash tool results
# (a Read of a doc mentioning "FAILED" must not trip this).
TEST_FAIL_RX = re.compile(r"test result: FAILED|[1-9]\d* failed;|error\[E\d+\]|panicked at|AssertionError|Traceback \(most recent|FAILED \(|=+ [1-9]\d* failed")
TEST_PASS_RX = re.compile(r"test result: ok\.|0 failed;|=+ \d+ passed[^=]*=|All tests passed")


def first_line(text):
    return (text or "").strip().splitlines()[0][:160] if text else ""


# ---------------------------------------------------------------- extraction helpers

def content_blocks(msg):
    """Return message content as a list of blocks, tolerating string content."""
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def summarize_tool_input(name, inp):
    if not isinstance(inp, dict):
        return clip(str(inp), 120)
    for key in ("command", "file_path", "pattern", "description", "query",
                "prompt", "skill", "url", "path"):
        if key in inp:
            return clip(str(inp[key]), 160)
    return clip(json.dumps(inp, ensure_ascii=False), 120)


def tool_result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------- per-session parse

def parse_session(path):
    """Stream one main-transcript JSONL into a compact digest dict."""
    d = {
        "session_id": path.stem,
        "transcript": str(path),
        "project_dir": path.parent.name,
        "cwd": None, "git_branch": None, "cli_version": None,
        "title": None, "slug": None,
        "started": None, "ended": None,
        "models": {},                 # model -> assistant message count
        "user_prompts": 0,
        "assistant_msgs": 0,
        "tool_calls": {},             # tool name -> count
        "tool_errors": 0,
        "error_samples": [],          # first line of distinct errors, capped
        "corrections": [],            # user messages matching correction heuristics
        "interruptions": 0,
        "test_signal": None,          # last observed: "pass" | "fail"
        "git_commits": [],            # commit subjects attempted via Bash
        "output_tokens": 0,
        "away_summary": None,
        "skills_used": {},            # Skill tool: skill name -> call count
        "agent_types": {},            # Agent/Task tool: subagent_type -> spawn count
        "subagents": [],              # {type, description} from meta.json
        "events": [],                 # chronological compact trace
        "parse_errors": 0,
    }
    tool_names_by_id = {}
    seen_errors = set()
    prompt_index = 0
    last_assistant_full = ""

    try:
        if str(path).endswith(".br"):  # this project's local archive (brotli-compressed jsonl, see archive.py)
            import brotli, io as _io
            raw = brotli.decompress(Path(path).read_bytes())
            fh = _io.StringIO(raw.decode("utf-8", errors="replace"))
            d["session_id"] = Path(path).name.split(".")[0]
            d["archived"] = True
        else:
            fh = open(path, encoding="utf-8", errors="replace")
    except (OSError, ImportError, ValueError) as e:
        d["parse_errors"] += 1
        d["events"].append({"t": "parse_error", "v": str(e)})
        return d

    with fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                d["parse_errors"] += 1
                continue
            if not isinstance(line, dict):
                continue
            ltype = line.get("type")
            ts = line.get("timestamp")
            if ts:
                d["started"] = d["started"] or ts
                d["ended"] = ts
            for field, key in (("cwd", "cwd"), ("git_branch", "gitBranch"),
                               ("cli_version", "version"), ("slug", "slug")):
                if d[field] is None and line.get(key):
                    d[field] = line[key]

            if ltype == "ai-title":
                d["title"] = line.get("aiTitle") or d["title"]

            elif ltype == "user":
                msg = line.get("message") or {}
                blocks = content_blocks(msg)
                is_meta = bool(line.get("isMeta"))
                for b in blocks:
                    btype = b.get("type")
                    if btype == "text":
                        text = b.get("text", "")
                        if any(m in text for m in INTERRUPT_MARKERS):
                            d["interruptions"] += 1
                            d["events"].append({"t": "interrupt", "v": clip(text, 120)})
                        elif is_meta or text.lstrip().startswith(("<command-", "<local-command-", "Caveat:")):
                            continue  # slash-command plumbing, not a real prompt
                        elif "<teammate-message" in text or "<task-notification" in text or text.lstrip().startswith("<system-reminder"):
                            # inter-agent / harness plumbing, not the human
                            d["teammate_msgs"] = d.get("teammate_msgs", 0) + 1
                        elif text.strip():
                            prompt_index += 1
                            d["user_prompts"] += 1
                            d["events"].append({"t": "user", "v": clip(text)})
                            if prompt_index > 1 and CORRECTION_RX.search(text):
                                d["corrections_total"] = d.get("corrections_total", 0) + 1
                                if len(d["corrections"]) < 15:
                                    d["corrections"].append(clip(text, 200))
                    elif btype == "tool_result":
                        text = tool_result_text(b)
                        name = tool_names_by_id.get(b.get("tool_use_id"), "?")
                        if b.get("is_error"):
                            d["tool_errors"] += 1
                            key = f"{name}:{first_line(text)}"
                            if key not in seen_errors and len(d["error_samples"]) < 20:
                                seen_errors.add(key)
                                d["error_samples"].append({"tool": name, "error": clip(text, 240)})
                            d["events"].append({"t": "tool_error", "tool": name, "v": clip(text, 200)})
                        if name == "Bash":
                            if TEST_FAIL_RX.search(text):
                                d["test_signal"] = "fail"
                            elif TEST_PASS_RX.search(text):
                                d["test_signal"] = "pass"

            elif ltype == "assistant":
                msg = line.get("message") or {}
                model = msg.get("model")
                if model:
                    d["models"][model] = d["models"].get(model, 0) + 1
                usage = msg.get("usage") or {}
                d["output_tokens"] += usage.get("output_tokens") or 0
                for b in content_blocks(msg):
                    btype = b.get("type")
                    if btype == "tool_use":
                        name = b.get("name", "?")
                        tool_names_by_id[b.get("id")] = name
                        d["tool_calls"][name] = d["tool_calls"].get(name, 0) + 1
                        summary = summarize_tool_input(name, b.get("input"))
                        d["events"].append({"t": "tool", "tool": name, "v": summary})
                        if name == "Bash":
                            cmd = (b.get("input") or {}).get("command", "")
                            m = re.search(r"git commit[^\n]*", cmd)
                            if m and len(d["git_commits"]) < 15:
                                d["git_commits"].append(clip(m.group(0), 140))
                        elif name == "Skill":
                            sk = str((b.get("input") or {}).get("skill", "?"))[:60]
                            d["skills_used"][sk] = d["skills_used"].get(sk, 0) + 1
                        elif name in ("Agent", "Task"):
                            at = str((b.get("input") or {}).get("subagent_type", "general-purpose"))[:40]
                            d["agent_types"][at] = d["agent_types"].get(at, 0) + 1
                    elif btype == "text":
                        d["assistant_msgs"] += 1
                        last_assistant_full = b.get("text", "")
                        d["events"].append({"t": "assistant", "v": clip(last_assistant_full, 200)})

            elif ltype == "system":
                sub = line.get("subtype")
                if sub == "away_summary":
                    d["away_summary"] = clip(str(line.get("content", "")), 400)
                elif sub == "init":  # SDK stream-json headless runs
                    d["cwd"] = d["cwd"] or line.get("cwd")

            elif ltype == "result":  # SDK stream-json terminal line
                d["result"] = {k: line.get(k) for k in
                               ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms")}
            # every other line type: metadata, intentionally ignored

    # The final assistant message usually states the outcome — keep it at full clip length.
    d["final_assistant"] = clip(last_assistant_full, MAX_FINAL_TEXT)
    for ev in reversed(d["events"]):
        if ev["t"] == "assistant":
            ev["final"] = True
            break

    # Collapse consecutive same-tool events, then cap total events head+tail.
    d["events"] = collapse_events(d["events"])
    if len(d["events"]) > MAX_EVENTS:
        head, tail = MAX_EVENTS * 2 // 3, MAX_EVENTS // 3
        omitted = len(d["events"]) - head - tail
        d["events"] = (d["events"][:head]
                       + [{"t": "omitted", "v": f"{omitted} events omitted"}]
                       + d["events"][-tail:])

    # Subagents (count + metadata only; their transcripts are not parsed here).
    # Use the cleaned session id, not path.stem — for "<uuid>.jsonl.br" archives
    # stem still carries ".jsonl". Archive mirrors the same <uuid>/subagents layout.
    sub_dir = path.parent / d["session_id"] / "subagents"
    if sub_dir.is_dir():
        for meta in sorted(sub_dir.glob("agent-*.meta.json")):
            try:
                m = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
                d["subagents"].append({
                    "type": m.get("agentType"),
                    "description": clip(str(m.get("description", "")), 120),
                })
            except (OSError, ValueError):
                d["parse_errors"] += 1

    d["duration_min"] = _duration_min(d["started"], d["ended"])
    if d["duration_min"] is None and d.get("result", {}).get("duration_ms"):
        d["duration_min"] = round(d["result"]["duration_ms"] / 60000, 1)
    return d


def collapse_events(events):
    """Collapse runs of >=3 consecutive tool events with the same tool name."""
    out, run = [], []
    def flush():
        if len(run) >= 3:
            samples = "; ".join(e["v"] for e in run[:2])
            out.append({"t": "tool", "tool": run[0]["tool"],
                        "v": f"×{len(run)} calls, e.g. {clip(samples, 200)}"})
        else:
            out.extend(run)
        run.clear()
    for ev in events:
        if ev["t"] == "tool" and run and run[-1]["tool"] == ev.get("tool"):
            run.append(ev)
        elif ev["t"] == "tool":
            flush()
            run.append(ev)
        else:
            flush()
            out.append(ev)
    flush()
    return out


def _duration_min(start, end):
    try:
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.strptime(start[:19], fmt)
        e = datetime.strptime(end[:19], fmt)
        return round((e - s).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- discovery + state

def encode_cwd(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def load_state(state_path):
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"processed": {}}


def save_state(state_path, state):
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1), encoding="utf-8")


def discover(root, args):
    """Yield candidate main-transcript paths under the projects root."""
    if args.transcripts_dir:  # arbitrary flat dir (e.g. an SDK headless-run harness)
        cutoff = time.time() - args.since * 3600
        base = Path(args.transcripts_dir).expanduser()
        seen_ids = set()
        for pattern in ("*.jsonl", "*.jsonl.br", "*.br"):
            for f in base.glob(pattern):
                sid = f.name.split(".")[0]
                if sid in seen_ids:
                    continue  # live jsonl takes precedence over its archived copy
                try:
                    if f.stat().st_mtime >= cutoff:
                        seen_ids.add(sid)
                        yield f
                except OSError:
                    continue
        return
    root = Path(root).expanduser()
    if args.session:
        hits = list(root.glob(f"*/{args.session}.jsonl"))
        yield from hits
        return
    if args.all_projects:
        dirs = [p for p in root.iterdir() if p.is_dir()]
    else:
        name = args.project or encode_cwd(os.getcwd())
        dirs = [root / name]
    cutoff = time.time() - args.since * 3600
    idle_cutoff = time.time() - MIN_IDLE_MINUTES * 60
    for dpath in dirs:
        if not dpath.is_dir():
            continue
        seen_ids = set()
        for pattern in ("*.jsonl", "*.jsonl.br", "*.br"):
            for f in dpath.glob(pattern):
                sid = f.name.split(".")[0]
                if sid in seen_ids:
                    continue  # live jsonl takes precedence over its archived copy
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                if mtime > idle_cutoff and not args.include_live:
                    continue  # probably a live session; picked up on the next run
                seen_ids.add(sid)
                yield f


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", type=float, default=24, help="lookback window in hours (default 24)")
    ap.add_argument("--projects-root", default="~/.claude/projects")
    ap.add_argument("--project", help="encoded project dir name (default: encode current cwd)")
    ap.add_argument("--all-projects", action="store_true")
    ap.add_argument("--session", help="single session uuid (ignores time/project filters)")
    ap.add_argument("--transcripts-dir", help="parse a flat directory of *.jsonl instead of the projects root (SDK stream-json supported)")
    ap.add_argument("--state", help="path to .state.json for idempotence (skip processed sessions)")
    ap.add_argument("--mark-processed", action="store_true", help="record parsed sessions in --state")
    ap.add_argument("--include-processed", action="store_true", help="ignore the state file skip-list")
    ap.add_argument("--include-live", action="store_true", help="also parse sessions modified <20 min ago")
    ap.add_argument("--min-events", type=int, default=3, help="drop trivial sessions with fewer events")
    ap.add_argument("--output-dir", help="write one <uuid>.digest.json per session instead of stdout")
    args = ap.parse_args()

    state = load_state(args.state) if args.state else {"processed": {}}
    digests, skipped = [], 0

    for path in discover(args.projects_root, args):
        sid = path.stem
        st = path.stat()
        prev = state["processed"].get(sid)
        if prev and not args.include_processed and prev.get("size") == st.st_size:
            skipped += 1
            continue
        digest = parse_session(path)
        if len(digest["events"]) < args.min_events and not args.session:
            state["processed"][sid] = {"size": st.st_size, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "trivial": True}
            continue
        digests.append(digest)
        if args.mark_processed:
            state["processed"][sid] = {"size": st.st_size, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    if args.state and args.mark_processed:
        save_state(args.state, state)

    if args.output_dir:
        out = Path(args.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        for dg in digests:
            (out / f"{dg['session_id']}.digest.json").write_text(
                json.dumps(dg, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"written": len(digests), "skipped_processed": skipped,
                          "dir": str(out)}, indent=1))
    else:
        json.dump({"digests": digests, "skipped_processed": skipped}, sys.stdout,
                  indent=1, ensure_ascii=False)
        print()


if __name__ == "__main__":
    main()
