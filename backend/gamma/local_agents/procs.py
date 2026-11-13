"""Running an agent's CLI: no console window, an environment that is the
user's and not this server's, and a process tree that ends with the turn or
with this server, whatever way either ends.

On Windows a child starts with ``CREATE_NO_WINDOW`` (no console pops up on
the desktop) and ``CREATE_NEW_PROCESS_GROUP``, and is put in a Job object
that kills every process in it when its last handle closes
(``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``): the handle is this process's, so
the agent and whatever it started die with this server even when it is
killed outright. ``kill_tree`` ends the job. On POSIX a child leads its own
session, so ``kill_tree`` signals the whole group; on Linux the agent
itself (not what it started) also gets SIGTERM when the thread that started
it ends (``PR_SET_PDEATHSIG``: the signal follows the spawning thread, not
the process, so a Child must not outlive the thread that spawned it; the
runner spawns and finishes a turn on one thread). ``stop_all`` runs at exit
for the children still alive.

The Job is assigned right after the process starts (Popen cannot start it
suspended), so a grandchild started in that instant would escape it; the
CLIs start nothing that early.
"""

import atexit
import collections
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from ..logbuf import log

STDERR_TAIL = 8 * 1024   # characters of a child's stderr kept for the run's record
_EOF = object()

# The parent's own agent session must not leak into the child: an agent run
# from a Claude Code or Codex session would otherwise think it is nested in
# it (and attach to that session's message socket). These are the variables
# a running session sets for what it starts; the user's own settings stay,
# CLAUDE_CODE_* configuration among them (CLAUDE_CODE_USE_BEDROCK,
# CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CODE_GIT_BASH_PATH), as do CODEX_HOME,
# ANTHROPIC_* and OPENAI_*.
_DROP = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED",
         "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SSE_PORT",
         "CLAUDE_CODE_EMIT_STARTUP_TIMING", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "CLAUDE_CODE_ENABLE_TASKS",
         "CLAUDE_CODE_QUESTION_PREVIEW_FORMAT", "CODEX_THREAD_ID"}
_DROP_PREFIXES = ("GAMMA_", "CODEX_SANDBOX", "CODEX_MANAGED_BY_")

_live: set = set()
_live_lock = threading.Lock()


def child_env() -> dict:
    """The environment an agent runs in: this server's minus every GAMMA_*
    variable (the shell secret, the data directory: the server's, never the
    agent's) and minus the parent's agent-session variables, with
    ``NO_COLOR=1``. A frozen Linux build gives back the library path its
    bootloader replaced (or none), so the CLI loads the system's."""
    env = {name: value for name, value in os.environ.items()
           if name.upper() not in _DROP and not name.upper().startswith(_DROP_PREFIXES)}
    env["NO_COLOR"] = "1"
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


# --- Windows: no window, a Job object ---------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    # An instance of our own, so these prototypes touch no one else's.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _job_for(proc):
        """A Job object holding ``proc`` that kills it and its descendants
        when closed, or None (logged) when one cannot be made: the child
        then runs without it and ``kill_tree`` uses taskkill."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            log.warning(f"[agents] no job object for process {proc.pid}: error {ctypes.get_last_error()}")
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (_kernel32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(limits),
                                                  ctypes.sizeof(limits))
                and _kernel32.AssignProcessToJobObject(job, int(proc._handle))):
            log.warning(f"[agents] no job object for process {proc.pid}: error {ctypes.get_last_error()}")
            _kernel32.CloseHandle(job)
            return None
        return job

    def _popen_flags() -> dict:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    def _pdeathsig():
        """Linux: the preexec function asking for SIGTERM when the spawning
        thread ends, prctl looked up here (not in the forked child); None
        elsewhere or when libc has no prctl."""
        if not sys.platform.startswith("linux"):
            return None
        try:
            import ctypes

            prctl = ctypes.CDLL(None, use_errno=True).prctl
        except (OSError, AttributeError):
            return None

        def set_pdeathsig():
            try:
                prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
            except Exception:  # noqa: BLE001 — the child runs without it
                pass
        return set_pdeathsig

    _PREEXEC = _pdeathsig()

    def _popen_flags() -> dict:
        flags = {"start_new_session": True}
        if _PREEXEC is not None:
            flags["preexec_fn"] = _PREEXEC
        return flags


# --- a running child ----------------------------------------------------------------

class Child:
    """A spawned CLI: ``proc``, its stdout as a queue of lines read by a
    daemon thread, the tail of its stderr, and one lock for writes to its
    stdin (the turn's thread and a watchdog may both write)."""

    def __init__(self, proc: subprocess.Popen, job=None):
        self.proc = proc
        self._job = job
        self._lines: queue.Queue = queue.Queue()
        self._tail: collections.deque = collections.deque()
        self._tail_size = 0
        self._tail_lock = threading.Lock()
        self._stderr_done = threading.Event()
        self._write = threading.Lock()
        self._kill = threading.Lock()
        threading.Thread(target=self._read_stdout, name=f"agent-out-{proc.pid}", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"agent-err-{proc.pid}", daemon=True).start()

    def _read_stdout(self) -> None:
        try:
            for line in self.proc.stdout:
                self._lines.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        self._lines.put(_EOF)

    def _read_stderr(self) -> None:
        try:
            for line in self.proc.stderr:
                with self._tail_lock:
                    self._tail.append(line)
                    self._tail_size += len(line)
                    while self._tail_size > STDERR_TAIL and len(self._tail) > 1:
                        self._tail_size -= len(self._tail.popleft())
        except (OSError, ValueError):
            pass
        self._stderr_done.set()

    def send(self, text: str) -> bool:
        """Write ``text`` to the child's stdin; False when it is closed or
        the child is gone."""
        with self._write:
            try:
                self.proc.stdin.write(text)
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):  # BrokenPipeError, a closed stdin
                return False

    def close_stdin(self) -> None:
        """Close the child's stdin (its end of input: the CLIs finish and
        exit). A write still blocked on a child that reads nothing gets two
        seconds; the stdin is left to ``kill_tree`` then."""
        if not self._write.acquire(timeout=2):
            return
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        finally:
            self._write.release()

    def line(self, timeout: float) -> str | None:
        """The next line of stdout, None when none came within ``timeout``
        seconds; EOFError once stdout has ended (and on every call after)."""
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is _EOF:
            self._lines.put(_EOF)
            raise EOFError
        return line

    def stderr_tail(self, wait: float = 0) -> str:
        """The last ``STDERR_TAIL`` characters of stderr; ``wait`` seconds at
        most for stderr to end first (after the child exited)."""
        if wait:
            self._stderr_done.wait(wait)
        with self._tail_lock:
            return "".join(self._tail)[-STDERR_TAIL:]

    def wait(self, timeout: float) -> int | None:
        """The exit code, None while it still runs after ``timeout``."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


def spawn(argv: list[str], *, cwd: str) -> Child:
    """Start ``argv`` in ``cwd`` with pipes for all three streams (UTF-8
    text, line buffered), in a Job object on Windows, and register it for
    ``stop_all``. OSError when it cannot start."""
    proc = subprocess.Popen(argv, cwd=cwd, env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                            **_popen_flags())
    job = None
    if sys.platform == "win32":
        try:
            job = _job_for(proc)
        except Exception as e:  # noqa: BLE001 — the child runs without one
            log.warning(f"[agents] no job object for process {proc.pid}: {e}")
    child = Child(proc, job)
    with _live_lock:
        _live.add(child)
    return child


def run_quick(argv: list[str], *, timeout: float, cwd: str | None = None) -> tuple[int, str]:
    """Run a short command (``--version``, a status) to its end:
    ``(returncode, stdout + stderr)``, decoded as UTF-8; ``(-1, "timed
    out")`` past ``timeout`` seconds (the tree killed), ``(-1, reason)``
    when it cannot start. Never raises."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **_popen_flags())
    except (OSError, ValueError) as e:
        return -1, str(e)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc, grace=0.5)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001 — a grandchild may hold the pipes
            pass
        return -1, "timed out"
    except Exception as e:  # noqa: BLE001
        kill_tree(proc, grace=0.5)
        return -1, str(e)
    return proc.returncode, (out or b"").decode("utf-8", "replace") + (err or b"").decode("utf-8", "replace")


def kill_tree(child_or_proc, *, grace: float = 3.0) -> None:
    """End a child and everything it started: on Windows its Job object
    (taskkill /T /F when it has none); on POSIX SIGTERM to its group, then
    SIGKILL after ``grace`` seconds. Idempotent; never raises."""
    child = child_or_proc if isinstance(child_or_proc, Child) else None
    proc = child.proc if child else child_or_proc
    lock = child._kill if child else threading.Lock()
    with lock:
        try:
            if sys.platform == "win32":
                job = child._job if child else None
                if job:
                    _kernel32.TerminateJobObject(job, 1)
                    _kernel32.CloseHandle(job)
                    child._job = None
                elif proc.poll() is None:  # a pid that ended may be someone else's by now
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)  # its own session: the group is its pid
                except (ProcessLookupError, PermissionError):
                    pass
                else:
                    try:
                        proc.wait(timeout=grace)
                    except subprocess.TimeoutExpired:
                        pass
                    try:  # what ignored SIGTERM, the leader or a grandchild
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
        except Exception as e:  # noqa: BLE001 — never raises
            log.warning(f"[agents] killing process {proc.pid}: {e}")
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    if child:
        with _live_lock:
            _live.discard(child)


def stop_all(timeout: float = 5.0) -> None:
    """Every child still alive: stdin closed (a graceful end), up to
    ``timeout`` seconds for all of them, then killed. At exit, and when the
    runner shuts down."""
    with _live_lock:
        children = list(_live)
    for child in children:
        child.close_stdin()
    deadline = time.monotonic() + timeout
    for child in children:
        child.wait(max(0.0, deadline - time.monotonic()))
    for child in children:
        kill_tree(child, grace=1.0)


atexit.register(stop_all)
