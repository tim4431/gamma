#!/usr/bin/env bash
# Runs the PR's slow suites on this machine the way CI runs them: CI's worker
# counts (pytest -n 4, the browser suite in three shards at --jobs 3 each),
# each job on CPUs of its own, all jobs at once, in about the time CI takes.
# A test that acts before the state it needs passes where each worker has
# several fast cores and fails on a runner, whose 4 vCPU are slower: the
# merges of November 2026 met one such race a PR round. Run in CI's shape it
# fails here, where its fix costs minutes (docs/dev/debugging.md "Order and
# timing"). The `merge` skill's local step runs it.
#
#   bash tools/ci_local.sh                     # backend, backend-mobile, e2e-1..3
#   bash tools/ci_local.sh e2e                 # the three browser shards
#   bash tools/ci_local.sh backend e2e-2       # some of the jobs
#
# Jobs: backend (Python 3.12), backend-mobile (the apps' Python 3.13 and
# pins), e2e-1, e2e-2, e2e-3 (or e2e for all three). The browser suite
# runs the checkout's frontend/dist: build it first (npm run build).
#
# Environment:
#   GAMMA_CI_CPUS       logical CPUs a job (default 2, one core and its SMT
#                       sibling). On the 16-core Ryzen 9 9950X3D2 it was
#                       measured on, 2 times the suites about as a runner
#                       does: the backend suite 559 s here against 772 s in
#                       CI, a browser shard 415-446 s against 392-506 s (at 4,
#                       1.4-2.2 times faster than CI, the known flakes CI met
#                       passed here). Another machine: compare a run's times.
#   GAMMA_CI_LOGS       where the logs go (default a new directory in TMPDIR);
#                       each job writes <job>.log, the browser shards their
#                       failures' screenshots under <job>.tmp/gamma-e2e-*/failures/
#   GAMMA_CI_PYTHON     the backend's Python (default the main checkout's
#                       backend/venv), also the browser suite's server
#   GAMMA_CI_PYTHON313  the apps' Python (default backend/venv313)
#
# Without taskset (macOS, Windows) the jobs run unpinned: CI's worker counts,
# not its pace.

set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MAIN=$(git -C "$ROOT" worktree list --porcelain | sed -n '1s/^worktree //p')
CPUS=${GAMMA_CI_CPUS:-2}
WORKERS=4  # pytest -n auto on a runner's 4 vCPU: CI's count whatever CPUS is, so fewer CPUs slow each test
LOGS=${GAMMA_CI_LOGS:-$(mktemp -d "${TMPDIR:-/tmp}/gamma-ci-XXXXXX")}
mkdir -p "$LOGS"

venv_python() {  # a venv's interpreter, on Linux/macOS or Windows
  local dir=$1
  if [ -x "$dir/bin/python" ]; then echo "$dir/bin/python"
  elif [ -x "$dir/Scripts/python.exe" ]; then echo "$dir/Scripts/python.exe"
  fi
}
PY=${GAMMA_CI_PYTHON:-$(venv_python "$MAIN/backend/venv")}
PY313=${GAMMA_CI_PYTHON313:-$(venv_python "$MAIN/backend/venv313")}

ALL=(backend backend-mobile e2e-1 e2e-2 e2e-3)
JOBS=()
for arg in "${@:-${ALL[@]}}"; do
  case $arg in
    e2e) JOBS+=(e2e-1 e2e-2 e2e-3) ;;
    backend|backend-mobile|e2e-1|e2e-2|e2e-3) JOBS+=("$arg") ;;
    *) echo "unknown job: $arg (jobs: ${ALL[*]}, or e2e)"; exit 2 ;;
  esac
done

# The CPU sets: whole cores with their siblings, CPUS logical CPUs a job,
# taken in order so two jobs never share a core while there are cores enough.
CORES=()
if command -v taskset >/dev/null && [ -d /sys/devices/system/cpu/cpu0/topology ]; then
  mapfile -t CORES < <(cat /sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list | sort -u -t, -k1,1n)
fi
if [ ${#CORES[@]} -eq 0 ]; then
  echo "taskset or the CPU topology is missing: the jobs run unpinned"
else
  THREADS=$(echo "${CORES[0]}" | tr ',-' '  ' | wc -w)  # "0,16", or "0-1" where siblings are adjacent
  PER=$(( (CPUS + THREADS - 1) / THREADS ))
  if (( ${#JOBS[@]} * PER > ${#CORES[@]} )); then
    echo "warning: ${#JOBS[@]} jobs of $PER cores need more than this machine's ${#CORES[@]}: some share"
  fi
fi
cpuset_for() {  # cpuset_for <job index>: that job's CPUs, as taskset -c takes them
  local i=$1 n set=()
  [ ${#CORES[@]} -eq 0 ] && return
  for ((n = 0; n < PER; n++)); do set+=("${CORES[$(( (i * PER + n) % ${#CORES[@]} ))]}"); done
  (IFS=,; echo "${set[*]}")
}

run_job() {  # run_job <job> <cpuset>
  local job=$1 cpus=$2 pin=() tmp="$LOGS/$1.tmp"
  mkdir -p "$tmp"
  [ -n "$cpus" ] && pin=(taskset -c "$cpus")
  case $job in
    backend)
      (cd "$ROOT/backend" && TMPDIR=$tmp "${pin[@]}" "$PY" -m pytest tests -q -n "$WORKERS" --dist loadfile) ;;
    backend-mobile)
      if [ -z "$PY313" ]; then echo "no Python 3.13 (backend/venv313, docs/dev/debugging.md): skipped"; return 3; fi
      (cd "$ROOT/backend" && TMPDIR=$tmp "${pin[@]}" "$PY313" -m pytest tests -q -n "$WORKERS" --dist loadfile) ;;
    e2e-*)
      (cd "$ROOT/frontend" && TMPDIR=$tmp GAMMA_E2E_PYTHON=$PY "${pin[@]}" \
        npm run e2e -- --continue --jobs 3 --shard "${job#e2e-}/3") ;;
  esac
}

if [[ " ${JOBS[*]} " == *" e2e-"* ]] && [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "frontend/dist has no build: run npm run build in frontend/ first"; exit 2
fi

echo "logs: $LOGS"
declare -A PIDS
i=0
start=$(date +%s)
for job in "${JOBS[@]}"; do
  cpus=$(cpuset_for $i)
  echo "  $job${cpus:+ on CPUs $cpus}"
  run_job "$job" "$cpus" > "$LOGS/$job.log" 2>&1 &
  PIDS[$job]=$!
  i=$((i + 1))
done

status=0
echo
for job in "${JOBS[@]}"; do
  wait "${PIDS[$job]}"; code=$?
  # the suite's own last word: pytest's "N passed ... in X s", the browser
  # suite's "N/M steps passed in X s; failed: ..."
  last=$(grep -E '(passed|failed|error).* in [0-9.]+ ?s|steps passed in|skipped$' "$LOGS/$job.log" | tail -1)
  if [ $code -eq 0 ]; then echo "ok    $job: $last"
  elif [ $code -eq 3 ]; then echo "skip  $job: $(tail -1 "$LOGS/$job.log")"
  else
    status=1
    echo "FAIL  $job: ${last:-exit $code, see $LOGS/$job.log}"
  fi
done
echo "in $(( $(date +%s) - start )) s; logs in $LOGS"
exit $status
