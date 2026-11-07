"""tests/testrun.py, in a process of its own for each case: what a run leaves in
temp, and how a stalled one ends."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTRUN = Path(__file__).resolve().parents[2] / "tests"


def run(temp, body, worker=False, limits=""):
    """``body`` in a process of its own whose system temp is ``temp``."""
    code = f"""
import pathlib, sys, tempfile, time
sys.path.insert(0, {str(TESTRUN)!r})
import testrun
{limits}
data = testrun.own_folder("run-")
testrun.pytest_configure()
{body}
"""
    env = {**os.environ, "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp)}
    env.pop("PYTEST_XDIST_WORKER", None)
    if worker:
        env["PYTEST_XDIST_WORKER"] = "gw0"
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)


def test_a_finished_process_leaves_nothing(tmp_path):
    done = run(tmp_path, """
pathlib.Path(data, "users.db").write_text("x")
pathlib.Path(tempfile.gettempdir(), "gamma-import-reviews-1").mkdir()
tempfile.NamedTemporaryFile(delete=False).close()
""")
    assert done.returncode == 0, done.stderr
    assert list(tmp_path.iterdir()) == []


def test_temp_files_land_beside_the_data_not_in_it(tmp_path):
    done = run(tmp_path, "print(pathlib.Path(tempfile.gettempdir()).parent == pathlib.Path(data).parent != pathlib.Path(data))")
    assert done.stdout.strip() == "True", done.stderr


def test_a_stalled_process_ends_itself_and_keeps_its_stacks(tmp_path):
    t0 = time.monotonic()
    done = run(tmp_path, "time.sleep(25)", limits="testrun.STALL_LIMIT = 1")
    assert done.returncode == 1
    assert time.monotonic() - t0 < 20
    log = next(tmp_path.glob("run-*/stalled.log")).read_text()
    assert "most recent call first" in log


def test_a_finished_test_counts_as_progress(tmp_path):
    done = run(tmp_path, """
for _ in range(4):
    time.sleep(1)
    testrun.pytest_runtest_logreport()
""", limits="testrun.STALL_LIMIT = 2; testrun.REWATCH_AFTER = 0")
    assert done.returncode == 0, done.stderr


def test_a_worker_idle_while_the_others_finish_is_not_stalled(tmp_path):
    done = run(tmp_path, "time.sleep(3)", worker=True, limits="testrun.STALL_LIMIT = 1")
    assert done.returncode == 0, done.stderr


def test_a_stuck_worker_ends_at_its_own_limit(tmp_path):
    done = run(tmp_path, "time.sleep(25)", worker=True, limits="testrun.WORKER_LIMIT = 1")
    assert done.returncode == 1


def test_a_debugger_stops_the_watch_until_it_leaves(tmp_path):
    waits = run(tmp_path, "testrun.pytest_enter_pdb(); time.sleep(3)", limits="testrun.STALL_LIMIT = 1")
    assert waits.returncode == 0, waits.stderr
    resumes = run(tmp_path, "testrun.pytest_enter_pdb(); testrun.pytest_leave_pdb(); time.sleep(25)",
                  limits="testrun.STALL_LIMIT = 1")
    assert resumes.returncode == 1


def _left_behind(temp, name, age):
    folder = temp / name
    folder.mkdir()
    (folder / "users.db").write_text("x")
    then = time.time() - age
    os.utime(folder, (then, then))
    return folder


@pytest.mark.parametrize("worker, swept", [(False, True), (True, False)])
def test_a_run_sweeps_what_a_killed_run_left_and_a_worker_leaves_that_to_it(tmp_path, worker, swept):
    old = _left_behind(tmp_path, "run-old", 2 * 24 * 60 * 60)
    recent = _left_behind(tmp_path, "run-recent", 60 * 60)
    other = _left_behind(tmp_path, "other-old", 2 * 24 * 60 * 60)
    done = run(tmp_path, "", worker=worker)
    assert done.returncode == 0, done.stderr
    assert old.exists() != swept
    assert recent.exists()
    assert other.exists()
