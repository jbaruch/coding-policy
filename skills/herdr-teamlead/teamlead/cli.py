"""`teamlead` command line: argparse subcommands, JSON on stdout.

This is the only module allowed to read the clock, and the only one that
decides an exit code. Everything below it is either pure or takes an injected
herdr client, which is what keeps the tests off the real binary.

I/O contract:

* success -> the command's JSON document on stdout, exit 0
* failure -> a JSON error object on stderr, exit 1 (2 for an argparse error)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .assign import apply as apply_assignments
from .assign import dry_run, normalize_assignments, resolve_paths
from .config import default_config_path, load_config, select_agents
from .errors import PlanError, StateError, TeamLeadError, UsageError
from .herdr import (
    DEFAULT_MARKER_TIMEOUT_MS,
    DEFAULT_SETTLE_TIMEOUT_MS,
    HerdrClient,
    trace_enabled_in_env,
)
from .composer import COMPOSER_SETTLE_SEC, DEFAULT_START_TIMEOUT_MS
from .diagnostics import PREFIX as DIAGNOSTIC_PREFIX
from .measure import (
    DEFAULT_MARKER_POLL_ATTEMPTS,
    DEFAULT_MARKER_POLL_INTERVAL_SEC,
    DEFAULT_READ_LINES,
    measure,
)
from .planner import plan as build_plan
from .state import (
    add_assignment,
    add_snapshot,
    default_state_path,
    latest_snapshot,
    load_state,
    load_state_checked,
    role_counts,
    save_state,
)

EPILOG = (
    "Config defaults to $XDG_CONFIG_HOME/teamlead/config.json (~/.config/...); "
    "copy config.example.json there to get started. State defaults to "
    "$XDG_STATE_HOME/teamlead/state.json (~/.local/state/...)."
)


def now_iso():
    """The wall clock, read here and nowhere else in the package."""
    return datetime.now(timezone.utc).isoformat()


def build_parser():
    # SUPPRESS keeps a subparser's copy of these flags from clobbering a value
    # given before the subcommand, so `teamlead --state F plan` and
    # `teamlead plan --state F` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="Agent config JSON (default: $XDG_CONFIG_HOME/teamlead/config.json).",
    )
    common.add_argument(
        "--state",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="State file (default: $XDG_STATE_HOME/teamlead/state.json).",
    )
    common.add_argument(
        "--herdr-bin",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="herdr executable to invoke (default: $TEAMLEAD_HERDR_BIN or `herdr`).",
    )
    common.add_argument(
        "--trace",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print every herdr command and its raw stdout, stderr, and exit "
        "status to stderr. Same as TEAMLEAD_TRACE=1.",
    )

    parser = argparse.ArgumentParser(
        prog="teamlead",
        parents=[common],
        description="Load-balance coding agents running inside Herdr.",
        epilog=EPILOG,
    )
    parser.add_argument("--version", action="version", version="teamlead " + __version__)

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    measure_parser = sub.add_parser(
        "measure",
        parents=[common],
        help="Read each idle agent's remaining subscription budget.",
        description=(
            "Send each idle agent its usage command, wait for the report, parse "
            "it, and save the snapshot. Agents that are working or blocked are "
            "reported as skipped, not interrupted."
        ),
    )
    measure_parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        metavar="NAME",
        help="Measure only this agent; repeat for several (default: all configured).",
    )
    measure_parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Timestamp to stamp the snapshot with (default: the current UTC time).",
    )
    measure_parser.add_argument(
        "--marker-timeout",
        type=int,
        default=DEFAULT_MARKER_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for a usage report to appear (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_READ_LINES,
        metavar="N",
        help="Pane lines to read for a usage report (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--marker-poll-attempts",
        type=int,
        default=DEFAULT_MARKER_POLL_ATTEMPTS,
        metavar="N",
        help="Re-reads of the pane after `pane wait-output` fails to deliver "
        "the marker (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Let teamlead clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
    )
    measure_parser.add_argument(
        "--composer-settle",
        type=float,
        default=COMPOSER_SETTLE_SEC,
        metavar="SECONDS",
        help="Seconds to let a TUI repaint before re-reading its composer "
        "(default: %(default)s).",
    )
    measure_parser.add_argument(
        "--marker-poll-interval",
        type=float,
        default=DEFAULT_MARKER_POLL_INTERVAL_SEC,
        metavar="SECONDS",
        help="Seconds between those re-reads (default: %(default)s).",
    )

    plan_parser = sub.add_parser(
        "plan",
        parents=[common],
        help="Assign roles to agents by remaining headroom. Touches no agent.",
        description=(
            "Deterministic assignment: roles are given heaviest first and agents "
            "are ranked by their smallest remaining usage window."
        ),
    )
    plan_parser.add_argument(
        "--roles",
        default="developer,tester,reviewer",
        metavar="LIST",
        help="Comma-separated roles, heaviest first (default: %(default)s).",
    )
    plan_parser.add_argument(
        "--snapshot",
        metavar="FILE",
        help="Snapshot to plan against (default: the newest one in the state file).",
    )

    apply_parser = sub.add_parser(
        "apply",
        parents=[common],
        help="Clear each agent's context and hand it its brief.",
        description=(
            "Refuses to type into an agent that is working or blocked. Statuses "
            "are checked for every target before the first keystroke is sent."
        ),
    )
    apply_parser.add_argument(
        "--assignments",
        required=True,
        metavar="FILE_OR_JSON",
        help="`teamlead plan` output, a {role: agent} object, or a path to either.",
    )
    apply_parser.add_argument(
        "--brief",
        action="append",
        default=[],
        dest="briefs",
        metavar="ROLE=PATH",
        help="Brief for one role; repeat once per role.",
    )
    apply_parser.add_argument(
        "--common",
        required=True,
        metavar="PATH",
        help="Shared instructions every agent reads before its own brief.",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the herdr commands and send nothing. Makes no herdr calls at all.",
    )
    apply_parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Skip the context-clearing prompt and send only the assignment.",
    )
    apply_parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Timestamp for the ledger entries (default: the current UTC time).",
    )
    apply_parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Let teamlead clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
    )
    apply_parser.add_argument(
        "--composer-settle",
        type=float,
        default=COMPOSER_SETTLE_SEC,
        metavar="SECONDS",
        help="Seconds to let a TUI repaint before re-reading its composer "
        "(default: %(default)s).",
    )
    apply_parser.add_argument(
        "--start-timeout",
        type=int,
        default=DEFAULT_START_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for an agent to start its turn after the "
        "assignment lands (default: %(default)s).",
    )
    apply_parser.add_argument(
        "--settle-timeout",
        type=int,
        default=DEFAULT_SETTLE_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for an agent to settle after clearing (default: %(default)s).",
    )

    sub.add_parser(
        "state",
        parents=[common],
        help="Print the state file.",
        description="Print the state file, or a fresh empty document if none exists.",
    )
    return parser


def _config_path(args):
    value = getattr(args, "config", None)
    return Path(value) if value else default_config_path()


def _state_path(args):
    value = getattr(args, "state", None)
    return Path(value) if value else default_state_path()


def _client(args, trace=None):
    tracing = getattr(args, "trace", False) or trace_enabled_in_env()
    return HerdrClient(
        binary=getattr(args, "herdr_bin", None),
        trace=trace if tracing else None,
    )


def _parse_briefs(pairs):
    briefs = {}
    for pair in pairs:
        role, separator, path = pair.partition("=")
        if not separator or not role or not path:
            raise UsageError(
                "--brief expects ROLE=PATH, got {!r} - for example "
                "--brief developer=/path/to/developer-brief.md.".format(pair),
                {"value": pair},
            )
        briefs[role] = path
    return briefs


def _load_assignments(value):
    """`--assignments` takes inline JSON or a path to a JSON file."""
    text = value.strip()
    if not text.startswith("{"):
        path = Path(text)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise UsageError(
                "--assignments file {} not found - pass the path to `teamlead "
                "plan` output, or the JSON object itself.".format(path),
                {"path": str(path)},
            ) from None
        except IsADirectoryError:
            raise UsageError(
                "--assignments path {} is a directory - point it at a JSON "
                "file.".format(path),
                {"path": str(path)},
            ) from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            "--assignments is not valid JSON ({} at line {} column {}) - expected "
            "`teamlead plan` output or a {{\"role\": \"agent\"}} object.".format(
                exc.msg, exc.lineno, exc.colno
            ),
            {},
        ) from None
    return normalize_assignments(payload)


def _load_state_for_write(path, warn):
    """Read state a caller intends to write back, or refuse.

    `load_state_checked` leaves an unreadable file exactly as found and hands
    back an empty document. Writing that document over the file would undo
    precisely the preservation it just performed, taking the operator's whole
    ledger with it -- so a write path refuses instead, and says how to keep
    both.
    """
    state, usable = load_state_checked(path, warn=warn)
    if not usable:
        raise StateError(
            "State file {} could not be read (see the warning above), and "
            "writing an empty ledger over it would destroy its contents. "
            "Move it aside with `mv {} {}.bak` to start fresh, or pass "
            "--state at another path to keep it.".format(path, path, path),
            {"path": str(path)},
        )
    return state


def cmd_measure(args, client=None, warn=None, trace=None):
    agents = select_agents(load_config(_config_path(args)), args.agents)
    client = client if client is not None else _client(args, trace=trace)
    snapshot = measure(
        client,
        agents,
        args.now or now_iso(),
        marker_timeout_ms=args.marker_timeout,
        read_lines=args.lines,
        warn=warn,
        poll_attempts=args.marker_poll_attempts,
        poll_interval_sec=args.marker_poll_interval,
        settle_sec=args.composer_settle,
        allow_recovery=args.allow_recovery,
    )
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)
    add_snapshot(state, snapshot)
    save_state(state_path, state)
    if not snapshot["failed_agents"]:
        return snapshot, None
    return snapshot, {
        "error": "measure_incomplete",
        "message": "Could not measure {} - see the `error` field on each agent "
        "in the snapshot on stdout.".format(", ".join(snapshot["failed_agents"])),
        "details": {"failed_agents": snapshot["failed_agents"]},
    }


def cmd_plan(args, client=None, warn=None, trace=None):
    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    state_path = _state_path(args)
    state = load_state(state_path)

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PlanError(
                "Snapshot file {} not found - run `teamlead measure` or point "
                "--snapshot at a saved snapshot.".format(snapshot_path),
                {"path": str(snapshot_path)},
            ) from None
        except json.JSONDecodeError as exc:
            raise PlanError(
                "Snapshot file {} is not valid JSON ({} at line {} column {}).".format(
                    snapshot_path, exc.msg, exc.lineno, exc.colno
                ),
                {"path": str(snapshot_path)},
            ) from None
        source = str(snapshot_path)
    else:
        snapshot = latest_snapshot(state)
        if snapshot is None:
            raise PlanError(
                "No snapshot in {} - run `teamlead measure` first, or pass "
                "--snapshot FILE.".format(state_path),
                {"state": str(state_path)},
            )
        source = str(state_path)

    if not isinstance(snapshot, dict):
        raise PlanError(
            "Snapshot from {} is not a JSON object with an `agents` field.".format(source),
            {"source": source},
        )

    return (
        build_plan(
            roles,
            snapshot,
            role_counts(state),
            snapshot_ref={"source": source, "measured_at": snapshot.get("measured_at")},
            warn=warn,
        ),
        None,
    )


def cmd_apply(args, client=None, warn=None, trace=None):
    agents = load_config(_config_path(args))
    agents_by_name = {agent.name: agent for agent in agents}
    assignments = _load_assignments(args.assignments)
    paths = resolve_paths(assignments, _parse_briefs(args.briefs), args.common)
    client = client if client is not None else _client(args, trace=trace)

    if args.dry_run:
        return (
            dry_run(
                client,
                assignments,
                agents_by_name,
                paths,
                no_clear=args.no_clear,
                settle_timeout_ms=args.settle_timeout,
            ),
            None,
        )

    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)

    def record(role, agent, at, status):
        add_assignment(state, at, role, agent, status=status)
        save_state(state_path, state)

    result = apply_assignments(
        client,
        assignments,
        agents_by_name,
        paths,
        args.now or now_iso(),
        no_clear=args.no_clear,
        settle_timeout_ms=args.settle_timeout,
        on_assigned=record,
        warn=warn,
        settle_sec=args.composer_settle,
        start_timeout_ms=args.start_timeout,
        allow_recovery=args.allow_recovery,
    )
    not_started = [
        record["agent"]
        for record in result["applied"]
        if record.get("status") == "sent_but_not_started"
    ]
    if not not_started:
        return result, None
    return result, {
        "error": "sent_but_not_started",
        "message": "Sent the assignment to {} but neither saw it in the "
        "transcript nor saw the agent start a turn. Check the pane before "
        "assuming it is working.".format(", ".join(not_started)),
        "details": {"sent_but_not_started": not_started},
    }


def cmd_state(args, client=None, warn=None, trace=None):
    return load_state(_state_path(args)), None


COMMANDS = {
    "measure": cmd_measure,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "state": cmd_state,
}


def main(argv=None, stdout=None, stderr=None, client=None):
    """Entry point. Returns the process exit code rather than calling sys.exit."""
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    args = build_parser().parse_args(argv)

    def trace(message):
        # Trace lines identify themselves ("herdr> ..."), so they go out bare.
        stderr.write(message + "\n")

    def warn(message):
        stderr.write(DIAGNOSTIC_PREFIX + message + "\n")

    try:
        payload, failure = COMMANDS[args.command](args, client=client, warn=warn, trace=trace)
    except TeamLeadError as exc:
        json.dump(exc.to_dict(), stderr, indent=2)
        stderr.write("\n")
        return 1

    json.dump(payload, stdout, indent=2)
    stdout.write("\n")
    if failure:
        # stdout keeps the full document either way; stderr says why the exit
        # code is non-zero.
        json.dump(failure, stderr, indent=2)
        stderr.write("\n")
        return 1
    return 0
