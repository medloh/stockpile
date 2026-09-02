"""Tests for the positions console's action log (history.py)."""

import json

import pytest

import history


@pytest.fixture
def hist(tmp_path, monkeypatch):
    """Point the module at a throwaway history file."""
    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "h.jsonl")
    monkeypatch.setattr(history, "TRACKER_LOG", tmp_path / "tracker.log")
    return history


class TestRecordAndRead:
    def test_empty_when_missing(self, hist):
        assert hist.entries() == []

    def test_roundtrip_newest_first(self, hist):
        hist.record("merge", file="a.csv", added=2)
        hist.record("run", accounts=["x"], exit_code=0)
        rows = hist.entries()
        assert [r["action"] for r in rows] == ["run", "merge"]
        assert rows[1]["added"] == 2
        assert "ts" in rows[0]

    def test_limit(self, hist):
        for i in range(5):
            hist.record("delete", file=f"{i}.csv")
        assert len(hist.entries(limit=2)) == 2

    def test_corrupt_line_skipped(self, hist):
        hist.record("merge", file="good.csv")
        with open(hist.HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write("{ this is not json\n")
        hist.record("merge", file="also_good.csv")
        rows = hist.entries()
        assert [r["file"] for r in rows] == ["also_good.csv", "good.csv"]

    def test_record_never_raises(self, hist, monkeypatch):
        """A failed log must not undo the action it was describing."""
        monkeypatch.setattr(
            hist, "HISTORY_PATH", hist.HISTORY_PATH / "nope" / "deep.jsonl")
        hist.record("merge", file="x.csv")  # must not raise

    def test_clear(self, hist):
        hist.record("merge", file="a.csv")
        hist.record("merge", file="b.csv")
        assert hist.clear() == 2
        assert hist.entries() == []

    def test_written_as_jsonl(self, hist):
        hist.record("delete", file="a.csv", bytes=10)
        lines = hist.HISTORY_PATH.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["file"] == "a.csv"


# ── tracker.log parsing ───────────────────────────────────────────────────

# The tracker writes whatever paths its own platform uses, and the console
# may well read a log written on the other one, so every path-bearing case
# below runs under both styles.
WIN_DIR = "C:\\x\\input\\"
POSIX_DIR = "/home/x/input/"
PATH_STYLES = pytest.mark.parametrize(
    "path_prefix", [WIN_DIR, POSIX_DIR], ids=["win", "posix"])


def ok_run(path_prefix=WIN_DIR):
    return f"""[2026-08-07 10:49:28] === Run started ===
[2026-08-07 10:49:28] Connecting to Google Sheets...
[2026-08-07 10:49:29] Processing: fidelity | CSV: {path_prefix}fidelity_522.csv
[2026-08-07 10:49:45] Done: fidelity / sheet-id
[2026-08-07 10:49:45] === Run completed successfully ===
"""


OK_RUN = ok_run()

ERR_RUN = """[2026-08-07 10:41:23] === Run started ===
[2026-08-07 10:41:23] ERROR: No account named nope.
"""


def killed_run(path_prefix=WIN_DIR):
    return f"""[2026-08-07 09:00:00] === Run started ===
[2026-08-07 09:00:01] Processing: schwab | CSV: {path_prefix}schwab556.csv
"""


class TestTrackerRuns:
    def test_no_log(self, hist):
        assert hist.tracker_runs() == []

    @PATH_STYLES
    def test_successful_run(self, hist, path_prefix):
        hist.TRACKER_LOG.write_text(ok_run(path_prefix), encoding="utf-8")
        (run,) = hist.tracker_runs()
        assert run["status"] == "ok"
        assert run["csvs"] == ["fidelity_522"]
        assert run["seconds"] == 17

    def test_error_run(self, hist):
        hist.TRACKER_LOG.write_text(ERR_RUN, encoding="utf-8")
        (run,) = hist.tracker_runs()
        assert run["status"] == "error"
        assert "No account named nope" in run["error"]

    def test_completed_run_not_marked_incomplete_by_the_next_one(self, hist):
        """The first run finished; starting a second must not restate that."""
        hist.TRACKER_LOG.write_text(OK_RUN + ERR_RUN, encoding="utf-8")
        runs = hist.tracker_runs()
        assert [r["status"] for r in runs] == ["error", "ok"], "newest first"

    @PATH_STYLES
    def test_interrupted_run_kept_as_incomplete(self, hist, path_prefix):
        hist.TRACKER_LOG.write_text(
            ok_run(path_prefix) + killed_run(path_prefix), encoding="utf-8")
        runs = hist.tracker_runs()
        assert runs[0]["status"] == "incomplete"
        assert runs[0]["csvs"] == ["schwab556"]
        assert runs[1]["status"] == "ok"

    @PATH_STYLES
    def test_multi_account_run(self, hist, path_prefix):
        two = ok_run(path_prefix).replace(
            "[2026-08-07 10:49:45] Done: fidelity / sheet-id",
            "[2026-08-07 10:49:45] Done: fidelity / sheet-id\n"
            f"[2026-08-07 10:49:46] Processing: schwab | CSV: {path_prefix}schwab556.csv",
        )
        hist.TRACKER_LOG.write_text(two, encoding="utf-8")
        (run,) = hist.tracker_runs()
        assert run["csvs"] == ["fidelity_522", "schwab556"]

    def test_garbage_lines_ignored(self, hist):
        hist.TRACKER_LOG.write_text(
            "not a log line\n" + OK_RUN + "trailing junk\n", encoding="utf-8")
        (run,) = hist.tracker_runs()
        assert run["status"] == "ok"

    def test_limit(self, hist):
        hist.TRACKER_LOG.write_text(OK_RUN * 5, encoding="utf-8")
        assert len(hist.tracker_runs(limit=2)) == 2
