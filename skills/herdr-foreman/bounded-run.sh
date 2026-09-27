#!/usr/bin/env bash
# Run one command under a wall-clock budget, for a hook that must finish.
#
# A hook that talks to origin (fetch, ls-remote, push, gh) can wait on the
# network for as long as the network likes, and a session start or a stop
# waits with it. `timeout` is GNU coreutils, absent from a stock macOS, so the
# budget is enforced here with python3, which every hook in this directory
# already needs.
#
# The command runs in its own process group. At the budget the whole group
# gets SIGTERM, then SIGKILL after KILL_GRACE_SEC, so a git or gh it spawned
# cannot outlive it.
#
# Contract:
#   argv  : <seconds> <command> [args...] — seconds is a positive integer.
#   stdin : passed to the command.
#   stdout: the command's own stdout, unchanged.
#   stderr: the command's own stderr, plus one diagnostic line when the budget
#           ran out or the command could not be started.
#   exit  : the command's own exit code; 124 when the budget ran out (the
#           `timeout` convention); 125 on a usage error, a missing python3, or
#           a command that could not be started.
set -euo pipefail

#: Seconds between SIGTERM and SIGKILL once the budget is spent.
KILL_GRACE_SEC=2

main() {
  if (( $# < 2 )) || [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
    printf 'bounded-run: usage: bounded-run.sh <seconds> <command> [args...]\n' >&2
    return 125
  fi
  if ! command -v python3 >/dev/null; then
    printf 'bounded-run: python3 not found on PATH — install it; %s did not run\n' "$2" >&2
    return 125
  fi
  local rc=0 program
  # The program is an argument, never stdin: stdin belongs to the command.
  program="$(cat <<'PY'
import os
import signal
import subprocess
import sys

grace, budget, command = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3:]
try:
    child = subprocess.Popen(command, start_new_session=True)
except OSError as exc:
    sys.stderr.write("bounded-run: cannot start {}: {} — check the path and its permissions\n".format(command[0], exc))
    sys.exit(125)


def signal_group(sig):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


try:
    sys.exit(child.wait(timeout=budget))
except subprocess.TimeoutExpired:
    signal_group(signal.SIGTERM)
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        signal_group(signal.SIGKILL)
        child.wait()
    sys.stderr.write("bounded-run: {} ran past its {:g}s budget and was stopped — run it by hand to see where it waits\n".format(
        " ".join(command), budget))
    sys.exit(124)
PY
)"
  python3 -c "$program" "$KILL_GRACE_SEC" "$@" || rc=$?
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
