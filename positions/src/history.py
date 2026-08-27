"""Append-only action log for the positions console.

Every action the console takes that changes something on disk or in a
sheet gets a line here: merges, archives, deletions, and runs. It is the
answer to "did I already merge that export?" and "when did this sheet
last get rebuilt?" — and, for deletions, the only record that a file ever
existed.

Stored as JSON Lines at ``positions/console_history.jsonl``: append-only,
survives a corrupt line, and readable with any text editor. One record
per action::

    {"ts": "2026-08-07T10:55:01", "action": "merge", "file": "fid2522.csv", ...}

Runs launched from the CLI never touch this file, so :func:`tracker_runs`
reads those back out of ``positions/tracker.log`` instead — together the
two give a complete picture regardless of how the tracker was started.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath

_POSITIONS_DIR = Path(__file__).resolve().parent.parent
HISTORY_PATH = _POSITIONS_DIR / "console_history.jsonl"
TRACKER_LOG = _POSITIONS_DIR / "tracker.log"

_LOG_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")


def record(action: str, **fields) -> None:
    """Append one action. Never raises — a failed log must not undo work."""
    entry = {"ts": datetime.now().isoformat(timespec="seconds"),
             "action": action, **fields}
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def entries(limit: int | None = None) -> list[dict]:
    """Recorded actions, newest first. Unparseable lines are skipped."""
    if not HISTORY_PATH.exists():
        return []
    out = []
    try:
        text = HISTORY_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out[:limit] if limit else out


def clear() -> int:
    """Delete the history file. Returns how many entries were dropped."""
    n = len(entries())
    HISTORY_PATH.unlink(missing_ok=True)
    return n


def tracker_runs(limit: int | None = None) -> list[dict]:
    """Every tracker run in tracker.log, newest first.

    Includes runs started from the CLI, which the console never sees. A
    session runs from "=== Run started ===" to the next terminator; a file
    that ends mid-session (the tracker was killed) yields a session marked
    incomplete rather than being dropped.
    """
    if not TRACKER_LOG.exists():
        return []
    try:
        text = TRACKER_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    runs: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        ts, msg = m.group(1), m.group(2).strip()

        if msg == "=== Run started ===":
            if cur is not None:
                # Only the sessions that never reached a terminator are
                # incomplete — don't clobber an "ok"/"error" already set.
                runs.append(cur)
            cur = {"ts": ts, "csvs": [], "status": "incomplete", "error": ""}
            continue
        if cur is None:
            continue

        if msg.startswith("Processing:"):
            # "Processing: fidelity | CSV: C:\...\fidelity_522.csv"
            csv_part = msg.split("CSV:", 1)[-1].strip()
            if csv_part:
                # PureWindowsPath, not Path: the log may have been
                # written on either platform, and it accepts both
                # separators. Plain Path() on POSIX treats a backslash as
                # an ordinary character and keeps the whole Windows path.
                cur["csvs"].append(PureWindowsPath(csv_part).stem)
        elif msg.startswith("ERROR:"):
            cur["status"] = "error"
            cur["error"] = msg[len("ERROR:"):].strip()
        elif msg == "=== Run completed successfully ===":
            cur["status"] = "ok"

        if msg.startswith("=== Run completed") or msg.startswith("ERROR:"):
            cur["end"] = ts

    if cur is not None:
        runs.append(cur)

    for r in runs:
        try:
            start = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(r.get("end", r["ts"]), "%Y-%m-%d %H:%M:%S")
            r["seconds"] = max((end - start).total_seconds(), 0)
        except ValueError:
            r["seconds"] = 0

    runs.reverse()
    return runs[:limit] if limit else runs
