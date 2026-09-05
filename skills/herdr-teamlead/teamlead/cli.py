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
from types import SimpleNamespace

from . import __version__
from .assign import apply as apply_assignments
from .assign import dry_run, normalize_assignments, resolve_paths
from .config import default_config_path, load_config, load_judge, load_role_costs, select_agents
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
from .tiers import parse_launch_args, parse_tiers, select_tier
from .qualification import require_qualification
from .launch import start_worker
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

    judge_parser = sub.add_parser("start-judge", parents=[common],
                                  help="Start the plan's judge in a shell pane and verify its launch argv.")
    judge_parser.add_argument("--assignments", required=True, metavar="PLAN")
    judge_parser.add_argument("--pane", required=True)
    judge_parser.add_argument("--kind", choices=("claude", "codex", "grok"), default="claude")

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
            "Deterministic assignment: each role carries a cost weight, the "
            "heaviest seat is filled first, and every seat goes to the eligible "
            "agent that leaves the round's smallest projected usage window "
            "highest."
        ),
    )
    plan_parser.add_argument(
        "--roles",
        default="developer,tester,reviewer",
        metavar="LIST",
        help="Comma-separated roles to assign (default: %(default)s). The cost "
        "weights decide which seat is filled first, not this order.",
    )
    plan_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        dest="excludes",
        metavar="ROLE=AGENT[,AGENT...]",
        help="Bar these agents from that role; repeat per role. Bar the branch's "
        "author from reviewer and tester -- nobody verifies their own work.",
    )
    plan_parser.add_argument(
        "--snapshot",
        metavar="FILE",
        help="Snapshot to plan against (default: the newest one in the state file).",
    )
    plan_parser.add_argument("--round", action="append", default=[], metavar="ROLE=ROUND",
                             help="Choose a configured round type for a role; never a model override.")
    plan_parser.add_argument("--round-context", metavar="FILE",
                             help="JSON object keyed by role with mechanical/risk evidence for this round.")
    plan_parser.add_argument("--fix-round", type=int, help="Task fix number; late fixes use the top tier.")
    plan_parser.add_argument("--preview-tiers", action="store_true",
                             help="Preview unqualified tiers. Live apply still requires complete qualification evidence.")
    plan_parser.add_argument("--now", metavar="ISO-8601", help="Reference time for qualification expiry (default: current UTC time).")

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
    context_flags = apply_parser.add_mutually_exclusive_group()
    context_flags.add_argument(
        "--no-clear",
        action="store_true",
        help=(
            "Skip the context-clearing prompt and send only the assignment. "
            "For a pane the lead already cleared by hand; the record carries "
            "cleared: false."
        ),
    )
    context_flags.add_argument(
        "--retain-context", action="store_true",
        help="Keep the same developer's context for fix rounds 1–3; requires --task and --fix-round.",
    )
    apply_parser.add_argument(
        "--fix-round", type=int, metavar="N",
        help="Fix-round number for this task; the dispatcher validates the cap.",
    )
    apply_parser.add_argument(
        "--task",
        metavar="LABEL",
        help="Task label for the pane titles, e.g. 12 or #12 (default: none).",
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


def _parse_excludes(pairs):
    """`--exclude ROLE=AGENT[,AGENT...]` into `{role: [agent, ...]}`.

    Repeats of the same role merge rather than replace, so a lead can bar the
    author from two seats in two flags or one.
    """
    excludes = {}
    for pair in pairs:
        role, separator, names = pair.partition("=")
        role = role.strip()
        agents = [name.strip() for name in names.split(",") if name.strip()]
        if not separator or not role or not agents:
            raise UsageError(
                "--exclude expects ROLE=AGENT[,AGENT...], got {!r} - for example "
                "--exclude reviewer=grok or --exclude tester=grok,claude.".format(pair),
                {"value": pair},
            )
        for name in agents:
            if name not in excludes.setdefault(role, []):
                excludes[role].append(name)
    return excludes


def _load_assignments(value, document=False):
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
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError("Cannot read assignments file {}: {}. Supply a readable UTF-8 JSON file with --assignments.".format(path, exc),
                             {"path": str(path)}) from None
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
    return payload if document else normalize_assignments(payload)


def _round_inputs(args, roles):
    """Read only round choices and evidence; models remain config-owned."""
    contexts = {}
    if args.round_context:
        try:
            contexts = json.loads(Path(args.round_context).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UsageError("Cannot read round context {}: {}. Supply a readable UTF-8 JSON file with --round-context.".format(
                args.round_context, exc), {"path": str(args.round_context)}) from None
    if not isinstance(contexts, dict) or set(contexts) - set(roles):
        raise UsageError("Round context must map only roles this plan assigns to evidence objects.", {})
    rounds = {role: {"context": context} for role, context in contexts.items()}
    for pair in args.round:
        if "=" not in pair:
            raise UsageError("Use --round ROLE=ROUND.", {})
        role, round_type = pair.split("=", 1)
        if role not in roles or "type" in rounds.get(role, {}):
            raise UsageError("--round names an unknown or duplicate role; choose it once.", {})
        rounds.setdefault(role, {})["type"] = round_type
    return rounds


def _candidate_tiers(roles, agents, rounds, fix_round=None, judge=None, qualified_at=None):
    tiered = any(agent.tiers for agent in agents)
    if not tiered and not (judge and "judge" in roles):
        if rounds:
            raise UsageError("Round selection requires configured tier tables.", {})
        return None
    candidates = {role: {} for role in roles}
    for role in roles:
        inputs = rounds.get(role, {})
        for agent in agents:
            if judge and agent.name == judge.agent:
                if role == "judge":
                    candidates[role][agent.name] = {"round": "judge", "tier_row": "judge", "kind": agent.kind,
                        "model": judge.model, "effort": judge.effort or None,
                        "billing_window": "unknown", "multiplier": 1.0, "effective_multiplier": 1.0}
                continue
            if role == "judge":
                continue
            if not agent.tiers:
                if not tiered:
                    candidates[role][agent.name] = None
                continue
            tier = select_tier(agent, role, inputs.get("type"), inputs.get("context"), fix_round)
            if tier is None:
                continue
            if qualified_at is not None:
                evidence = [record for entry in agent.tiers.values() for record in entry.get("qualification", [])]
                try:
                    require_qualification({**tier, "qualification": evidence}, role, qualified_at)
                except UsageError:
                    # This candidate is ineligible; another qualified worker may fill the seat.
                    continue
            candidates[role][agent.name] = {key: tier[key] for key in (
                "round", "tier_row", "kind", "model", "effort", "multiplier", "billing_window", "effective_multiplier"
            )}
    return candidates


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
    excludes = _parse_excludes(args.excludes)
    role_costs = load_role_costs(_config_path(args))
    judge = load_judge(_config_path(args))
    rounds = _round_inputs(args, roles)
    agents = load_config(_config_path(args)) if _config_path(args).exists() else []
    tier_candidates = _candidate_tiers(roles, agents, rounds, args.fix_round, judge,
                                      None if args.preview_tiers else (args.now or now_iso()))
    state_path = _state_path(args)
    state = load_state(state_path, warn=warn)

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
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanError("Cannot read snapshot {}: {}. Supply a readable UTF-8 JSON file with --snapshot.".format(snapshot_path, exc),
                            {"path": str(snapshot_path)}) from None
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
            exclude=excludes,
            role_costs=role_costs,
            judge_agent=judge.agent if judge else None,
            judge_tier={
                "model": judge.model,
                "effort": judge.effort,
                "launch_args": next((list(agent.launch_args) for agent in agents if agent.name == judge.agent), []),
            }
            if judge
            else None,
            snapshot_ref={"source": source, "measured_at": snapshot.get("measured_at")},
            warn=warn,
            tier_candidates=tier_candidates,
            rounds=rounds,
        ),
        None,
    )


def cmd_apply(args, client=None, warn=None, trace=None):
    agents = load_config(_config_path(args))
    agents_by_name = {agent.name: agent for agent in agents}
    document = _load_assignments(args.assignments, document=True)
    assignments = normalize_assignments(document)
    rounds = document.get("rounds", {}) if "assignments" in document else {}
    if not isinstance(rounds, dict) or set(rounds) - set(assignments):
        raise UsageError("Plan rounds must map only assigned roles to round inputs.", {})
    for value in rounds.values():
        if not isinstance(value, dict) or set(value) - {"type", "context"}:
            raise UsageError("Plan round inputs allow only type and context; model overrides are forbidden.", {})
    judge = load_judge(_config_path(args))
    if "judge" in assignments and (judge is None or assignments["judge"] != judge.agent):
        raise UsageError("Judge assignment must match the pinned judge in config.json.", {})
    if judge and any(name == judge.agent and role != "judge" for role, name in assignments.items()):
        raise UsageError("The pinned judge worker cannot hold another role.", {})
    candidates = _candidate_tiers(list(assignments), agents, rounds, args.fix_round, judge)
    tiers = {}
    if candidates is not None:
        for role, name in assignments.items():
            if name not in candidates.get(role, {}):
                raise UsageError("Assigned agent {} has no eligible tier for {}; replan from current config.".format(name, role), {})
            if candidates[role][name] is not None:
                tiers[role] = candidates[role][name]
        saved_tiers = {role: tier for role, tier in document.get("tiers", {}).items() if tier is not None} if isinstance(document.get("tiers", {}), dict) else None
        if "tiers" in document and saved_tiers != tiers:
            raise UsageError("Plan tiers differ from current config or fix context; re-run plan before dispatch.", {})
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
                retain_context=args.retain_context,
                task=args.task,
                fix_round=args.fix_round,
                settle_timeout_ms=args.settle_timeout,
                tiers=tiers,
            ),
            None,
        )

    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)

    def record(role, agent, at, status, context):
        add_assignment(state, at, role, agent, status=status, **context)
        save_state(state_path, state)

    result = apply_assignments(
        client,
        assignments,
        agents_by_name,
        paths,
        args.now or now_iso(),
        no_clear=args.no_clear,
        retain_context=args.retain_context,
        fix_round=args.fix_round,
        history=state["assignments"],
        settle_timeout_ms=args.settle_timeout,
        on_assigned=record,
        warn=warn,
        task=args.task,
        settle_sec=args.composer_settle,
        start_timeout_ms=args.start_timeout,
        allow_recovery=args.allow_recovery,
        tiers=tiers,
        qualifications={role: [record for entry in agents_by_name[name].tiers.values()
                               for record in entry.get("qualification", [])]
                        for role, name in assignments.items()},
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
    return load_state(_state_path(args), warn=warn), None


def cmd_start_judge(args, client=None, warn=None, trace=None):
    document = _load_assignments(args.assignments, document=True)
    tier = document.get("judge") if isinstance(document, dict) else None
    if not isinstance(tier, dict) or not isinstance(tier.get("agent"), str) or not tier["agent"].strip():
        raise UsageError("Plan has no usable judge tier; run plan --roles judge.", {})
    if normalize_assignments(document).get("judge") != tier["agent"]:
        raise UsageError("Plan judge tier and assignment name different workers; replan.", {})
    parsed = parse_tiers({"build": {"model": tier.get("model"), "effort": tier.get("effort")}}, args.kind)["build"]
    agent = SimpleNamespace(name=tier["agent"], kind=args.kind,
                            launch_args=parse_launch_args(tier.get("launch_args", []), args.kind))
    client = client if client is not None else _client(args, trace=trace)
    proof = start_worker(client, agent, args.pane, parsed)
    return {"agent": agent.name, "model": parsed["model"], "effort": parsed["effort"],
            "pane": args.pane, "argv_verified": True, "verified": proof}, None


COMMANDS = {
    "measure": cmd_measure,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "state": cmd_state,
    "start-judge": cmd_start_judge,
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
