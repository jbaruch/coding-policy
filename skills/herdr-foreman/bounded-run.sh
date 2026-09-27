#!/usr/bin/env bash
# Run one command under a wall-clock budget, for a caller that must finish.
#
# A command that talks to origin (fetch, ls-remote, push, gh) can wait on the
# network for as long as the network likes, and a session start, a stop or a
# round preflight waits with it. `timeout` is GNU coreutils, absent from a
# stock macOS, so the budget is enforced here with python3, which every caller
# already needs.
#
# The budget is a SIGALRM the runner schedules for itself. When it arrives —
# at the budget, or earlier from anyone who sends SIGALRM to the runner's pid
# (how the tests end a budget without waiting on a clock) — the command's
# whole process group gets SIGTERM, then SIGKILL after KILL_GRACE_SEC when
# any member is still running, whether or not the direct child has exited, so
# a git or gh it spawned cannot outlive it. The runner execs python3, so its pid
# is this script's pid.
#
# Contract:
#   argv  : <seconds> <command> [args...] — seconds is a positive integer.
#   stdin : passed to the command.
#   stdout: the command's own stdout, unchanged; the runner adds nothing
#           (rules/script-delegation.md, the bounded-run.sh carve-out). On
#           exit 124 or 125 a caller discards whatever the command printed.
#   stderr: the command's own stderr, plus one diagnostic line when the budget
#           ran out or the command could not be started.
#   exit  : the command's own exit code; 124 when the budget ran out (the
#           `timeout` convention); 125 on a usage error, a missing python3, or
#           a command that could not be started.
#   signal: SIGALRM ends the budget now. It is blocked until the command is
#           launched and the alarm armed, and blocked again before the alarm
#           is cancelled, so an expiry at any point stops the command's whole
#           process group and exits 124, never a traceback.
#   env   : BOUNDED_RUN_TEST_EXPIRE_BEFORE_LAUNCH=1 (tests only) delivers the
#           expiry before the command is launched;
#           BOUNDED_RUN_TEST_EXPIRE_AFTER_EXIT=1 (tests only) delivers it
#           after the command exits, before the alarm is cancelled.
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
  local program
  # The program is an argument, never stdin: stdin belongs to the command.
  program="$(cat <<'PY'
import os
import signal
import subprocess
import sys
import time

#: Seconds between checks for a process group still alive inside the grace.
GROUP_POLL_SEC = 0.05


class Expired(Exception):
    pass


def expire(signum, frame):
    raise Expired()


grace, budget, command = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]
ALARM = {signal.SIGALRM}

# SIGALRM stays blocked until the child exists and the alarm is armed: an
# expiry that arrives earlier waits, pending, and is handled below with a
# process group to stop. The child starts with it unblocked again.
signal.pthread_sigmask(signal.SIG_BLOCK, ALARM)
signal.signal(signal.SIGALRM, expire)
if os.environ.get("BOUNDED_RUN_TEST_EXPIRE_BEFORE_LAUNCH") == "1":
    # Test seam: an expiry that lands before the command is launched.
    os.kill(os.getpid(), signal.SIGALRM)
try:
    child = subprocess.Popen(command, start_new_session=True,
                             preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_UNBLOCK, ALARM))
except OSError as exc:
    sys.stderr.write("bounded-run: cannot start {}: {} — check the path and its permissions\n".format(command[0], exc))
    sys.exit(125)


def signal_group(sig):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def group_alive():
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


try:
    signal.alarm(budget)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, ALARM)
    code = child.wait()
    if os.environ.get("BOUNDED_RUN_TEST_EXPIRE_AFTER_EXIT") == "1":
        # Test seam: an expiry that lands as the command exits.
        os.kill(os.getpid(), signal.SIGALRM)
    # Cancelled inside the handler's reach: an expiry landing after the wait
    # but before this is still stopped here, never a traceback.
    signal.pthread_sigmask(signal.SIG_BLOCK, ALARM)
    signal.alarm(0)
except Expired:
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    signal_group(signal.SIGTERM)
    # The grace covers the whole group, not the direct child alone: a child
    # that exits on SIGTERM can leave a grandchild that ignores it.
    end = time.monotonic() + grace
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    while group_alive() and time.monotonic() < end:
        time.sleep(GROUP_POLL_SEC)
    if group_alive():
        signal_group(signal.SIGKILL)
    child.wait()
    # Only the executable is named: an argument can carry a credential.
    sys.stderr.write("bounded-run: {} ran past its {}s budget and was stopped — run it by hand to see where it waits\n".format(
        os.path.basename(command[0]), budget))
    sys.exit(124)
# A command killed by a signal exits 128 + that signal, as a shell reports it.
sys.exit(code if code >= 0 else 128 - code)
PY
)"
  exec python3 -c "$program" "$KILL_GRACE_SEC" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
