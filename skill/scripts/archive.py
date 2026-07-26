#!/usr/bin/env python3
"""archive.py — Delta-sync Claude Code session transcripts to a purge-proof archive.

Claude Code prunes transcripts in ~/.claude/projects after ~30 days. This
script brotli-compresses main transcripts into a local archive owned by this
project (`~/.claude/nightly-consolidation/archive/sessions/`) before they
vanish, so `collect.py` can still analyze sessions past that window.

Notable behavior:
- refresh-on-growth: a session archived while still growing is RE-archived
  when the live source's mtime is newer — no permanently-truncated snapshots.
- subagent capture: mirrors <uuid>/subagents/ (agent transcripts compressed,
  meta.json verbatim) so subagent data survives the purge too.
  (tool-results/ and workflows/ are NOT archived — bulky, low signal.)

Usage:
  python archive.py            # sync all projects
  python archive.py --dry-run  # report what would be copied
Requires: pip install brotli
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import brotli
except ImportError:
    sys.exit("archive.py requires the 'brotli' package: pip install brotli")

LIVE = Path.home() / ".claude" / "projects"
ARCHIVE = Path.home() / ".claude" / "nightly-consolidation" / "archive" / "sessions"
QUALITY = 6  # good size/speed tradeoff for this use case (roughly 85%+ size reduction)


def _archive_file(src, dest, stats, dry_run, compress=True):
    """Copy/compress one file if new or grown; update stats in place."""
    try:
        if dest.exists() and src.stat().st_mtime <= dest.stat().st_mtime:
            stats["skipped"] += 1
            return
        stats["refreshed" if dest.exists() else "new"] += 1
        if dry_run:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        if compress:
            data = brotli.compress(data, quality=QUALITY)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError:
        stats["errors"] += 1


def sync(dry_run=False):
    stats = {"new": 0, "refreshed": 0, "skipped": 0, "errors": 0,
             "subagent_files": 0, "dry_run": dry_run}
    for project in sorted(p for p in LIVE.iterdir() if p.is_dir()):
        dest_dir = ARCHIVE / project.name
        for src in project.glob("*.jsonl"):  # main transcripts at top level
            _archive_file(src, dest_dir / (src.name + ".br"), stats, dry_run)
            # Mirror the session's subagent transcripts + metadata (same
            # layout, so collect.py's subagents glob works on the archive).
            sub_src = project / src.stem / "subagents"
            if sub_src.is_dir():
                sub_dest = dest_dir / src.stem / "subagents"
                before = stats["new"] + stats["refreshed"]
                for meta in sub_src.glob("agent-*.meta.json"):
                    _archive_file(meta, sub_dest / meta.name, stats, dry_run, compress=False)
                for tr in sub_src.glob("agent-*.jsonl"):
                    _archive_file(tr, sub_dest / (tr.name + ".br"), stats, dry_run)
                stats["subagent_files"] += stats["new"] + stats["refreshed"] - before
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not LIVE.is_dir():
        sys.exit(f"live projects dir not found: {LIVE}")
    print(json.dumps(sync(args.dry_run)))
