"""`teamlead` command line: argparse subcommands, JSON on stdout.

This is the only module allowed to read the clock, and the only one that
decides an exit code. Everything below it is either pure or takes an injected
herdr client, which is what keeps the tests off the real binary.

I/O contract:

* success -> the command's JSON document on stdout, exit 0
* failure -> a JSON error object on stderr, exit 1 (2 for an argparse error)
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import __version__
from .assign import apply as apply_assignments
from .assign import APPLY_SCHEMA_VERSION, dry_run, native_context_session, normalize_assignments, resolve_paths, validate_fix_history
from . import attention, capabilities, composition, engagement, foreman_queue, foreman_reset, historical, load_set, memory, oracle, partition, recovery, report_delivery, restoration, role_clear, retrospective, retrospective_runtime, supervision, supervision_gate, supervision_runtime, triggers
from .config import default_config_path, load_config, load_judge, load_role_costs, select_agents
from .errors import PlanError, StateError, TeamLeadError, UsageError
from .herdr import (
    DEFAULT_MARKER_TIMEOUT_MS,
    DEFAULT_SETTLE_TIMEOUT_MS,
    HerdrClient,
    trace_enabled_in_env,
)
from .composer import COMPOSER_SETTLE_SEC, DEFAULT_START_TIMEOUT_MS
from .tiers import SEAT_SEPARATOR, canonical_role, require_seatable
from .diagnostics import PREFIX as DIAGNOSTIC_PREFIX
from .measure import (
    DEFAULT_MARKER_POLL_ATTEMPTS,
    DEFAULT_MARKER_POLL_INTERVAL_SEC,
    DEFAULT_READ_LINES,
    measure,
)
from .planner import plan as build_plan
from .planner import headroom_of
from .tiers import MissingTierError, parse_launch_args, parse_tiers, select_tier
from .launch import start_worker, verify_running
from .state import (
    add_assignment,
    add_snapshot,
    default_state_path,
    latest_snapshot,
    load_state,
    load_state_checked,
    role_counts,
    save_state,
    state_lock,
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
    judge_parser.add_argument("--task")
    judge_parser.add_argument("--judge-mode", choices=recovery.JUDGE_MODES,
                              help="What this judge seat is for; the plan's recorded mode when omitted.")
    judge_parser.add_argument("--now", metavar="ISO")

    for command in ("retro-check", "retro-record"):
        retro_parser = sub.add_parser(command, parents=[common], help="Check or record a lead-authored retrospective.")
        retro_parser.add_argument("--record", required=True, metavar="FILE")
        retro_parser.add_argument("--now", metavar="ISO")
    capability_check = sub.add_parser("capability-check", parents=[common],
                                      help="Whether the model-capability table is due a refresh. Read-only.")
    capability_check.add_argument("--now", metavar="ISO")
    capability_record = sub.add_parser("capability-record", parents=[common],
                                       help="Record a capability consultation's report into the table.")
    capability_record.add_argument("--record", required=True, metavar="FILE")
    capability_record.add_argument("--now", metavar="ISO")
    capability_show = sub.add_parser("capability-show", parents=[common],
                                     help="Read the saved capability table without contacting Herdr.")
    sub.add_parser("supervision-gate", parents=[common],
                   help="Which pending supervision events need the lead. Read-only.")

    retro_list = sub.add_parser("retro-list", parents=[common], help="List saved retrospective notes without contacting Herdr.")
    retro_list.add_argument("--task")
    retro_list.add_argument("--since", metavar="ISO")
    retro_show = sub.add_parser("retro-show", parents=[common], help="Read a saved retrospective without contacting Herdr.")
    retro_show.add_argument("--task")
    retro_show.add_argument("--id", default="latest")
    memory.register_commands(sub, common)
    attention.register_commands(sub, common)
    supervision_runtime.register_commands(sub, common)
    restoration.register_commands(sub, common)
    partition.register_commands(sub, common)
    oracle.register_commands(sub, common)

    triggers.register_command(sub, common)

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
    plan_parser.add_argument("--partition", metavar="FILE",
                             help="Review partition; seats one worker per slice of the role it names. Validate it with `validate-partition` first.")
    plan_parser.add_argument("--judge-mode", choices=recovery.JUDGE_MODES,
                             help="What this round's judge seat is for. Required with --roles judge.")
    plan_parser.add_argument("--fix-round", type=int, help="Task fix number; late fixes use the top tier.")
    plan_parser.add_argument("--task", help="Original task identity; preserve it through every correction.")
    plan_parser.add_argument("--requirements", metavar="FILE",
                             help="Versioned per-role specialty, capabilities, independence and engagement requirements.")
    plan_parser.add_argument("--now", metavar="ISO-8601", help="Reference time for the plan (default: current UTC time).")

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
        "--judge-mode", choices=recovery.JUDGE_MODES,
        help="What this dispatch's judge seat is for. Required when the batch holds a judge.",
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
        "--report", action="append", default=[], dest="reports", metavar="ROLE=ABS_PATH",
        help="Expected report path per assignment; every bound supervision role requires one.",
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
    context_flags.add_argument(
        "--retain-specialist", action="store_true",
        help="Follow up on an assessed consultation in its verified unchanged task, engagement and session.",
    )
    apply_parser.add_argument(
        "--fix-round", type=int, metavar="N",
        help="Fix-round number for this task; the dispatcher validates the cap.",
    )
    apply_parser.add_argument(
        "--task",
        metavar="LABEL",
        help="Original task identity for dispatch recovery, supervision, and pane titles.",
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

    for command_parser in (plan_parser, apply_parser):
        command_parser.add_argument("--correction-plan", help="Recorded bounded approval for extra corrections.")
        command_parser.add_argument("--work", metavar="FILE", help="Correction base, scope, paths and blocking findings as JSON.")
    apply_parser.add_argument("--dispatch-id", help="Stable dispatch identity; retries read its recorded outcome.")

    report_parser = sub.add_parser("probe-report", parents=[common], help="Confirm native UI decoration using completed source and visible rows on stdin.")
    report_parser.add_argument("--agent", required=True)
    report_parser.add_argument("--pane", required=True)
    report_parser.add_argument("--report", required=True)
    report_parser.add_argument("--lines", type=int, required=True)

    for command in ("task", "checkpoint", "authorize-corrections", "authorize-approach", "recover-context", "recover-role-clear", "record-report", "record-refusal", "authorize-refused-dispatch", "diagnose", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report", "assess-specialist", "close-task"):
        record_parser = sub.add_parser(command, parents=[common], help="Record owner-managed {} evidence.".format(command))
        record_parser.add_argument("--record", required=True, metavar="FILE", help="Structured evidence JSON; see dispatch-recovery.md.")
        record_parser.add_argument("--now", metavar="ISO8601")
    sub.add_parser("status", parents=[common], help="Show implementation budgets and paused work separately from active audit workers.")
    reset_parser = sub.add_parser("foreman-reset", parents=[common], help="Schedule the foreman's round-boundary context reset from a reset-ready stow.")
    reset_parser.add_argument("--stow", default="latest", help="Stow id the reset resumes from (default: the latest).")
    reset_parser.add_argument("--now", metavar="ISO8601")
    deliver_parser = sub.add_parser("foreman-reset-deliver", parents=[common], help="Internal: wait for the foreman pane to idle, then clear it and send the resume prompt.")
    deliver_parser.add_argument("--pane", required=True)
    deliver_parser.add_argument("--stow", required=True)
    sub.add_parser("foreman-queue", parents=[common], help="List open tasks waiting for their next seat, oldest first, derived from the owner records.")
    load_parser = sub.add_parser("load-set", parents=[common], help="List the durable records one foreman decision must load, derived from the owner records.")
    load_parser.add_argument("--decision", required=True, choices=load_set.DECISIONS)
    load_target = load_parser.add_mutually_exclusive_group(required=True)
    load_target.add_argument("--task", help="Task identity for plan, brief, gate and diagnose.")
    load_target.add_argument("--enrollment", help="Enrollment (dispatch) id for wake.")

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
    return retrospective.canonical_state(Path(value) if value else default_state_path())


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


def _parse_reports(pairs, assignments):
    """Validate the complete report map before any clear, launch, or send."""
    reports = {}
    for pair in pairs:
        role, separator, path = pair.partition("=")
        if (not separator or role not in assignments or role in reports or not path
                or not Path(path).is_absolute() or path.endswith("/")
                or any(ord(char) < 32 for char in path)):
            raise UsageError("--report requires one ROLE=ABS_PATH for each assigned role; no duplicates, unknown roles, relative paths, or directory paths.", {})
        if any(Path(existing).resolve() == Path(path).resolve() for existing in reports.values()):
            raise UsageError("Each dispatched role needs a distinct report file; shared report paths would overwrite worker evidence.", {})
        if Path(path).is_dir():
            raise UsageError("The expected --report path names a directory; use the absolute report file path from the brief.", {})
        reports[role] = path
    return reports


def _supervision_enrollment(state_path, identifier, task, role, name, report, at, *,
                            pane_id=None, native=None, replay=False, persist=False):
    """Preserve enrollment evidence; prepare a new row before worker input."""
    data = supervision.load(state_path)
    expected = {"id": identifier, "agent": name, "task": task, "report": report,
                "pane_id": pane_id, "native_session": None}
    prior = next((row for row in data["members"] if row["id"] == identifier), None)
    if prior:
        if any(prior["assignment"][key] != expected[key] for key in ("id", "agent", "task", "report")):
            raise UsageError("This dispatch enrollment names different task/report evidence; preserve its original report path and reconcile before retrying.", {"role": role})
        if not replay and not prior["active"]:
            raise UsageError("This enrollment was resolved. Use a new explicit --dispatch-id for a new authorized transport attempt; never reactivate accepted work.", {"role": role})
        expected = prior["assignment"]
        current = supervision.expected_assignment(prior)
        if persist and pane_id is not None and current["pane_id"] is not None and current["pane_id"] != pane_id:
            raise UsageError("This dispatch now points at a different pane. Reconcile the original enrollment before a new authorized dispatch.", {"agent": name})
        if (persist and native is not None and current["native_session"] is not None
                and {key: value for key, value in current["native_session"].items() if key != "pane_id"}
                != {key: value for key, value in native.items() if key != "pane_id"}):
            raise UsageError("This dispatch has different native session evidence. Preserve the enrollment and reconcile the context before continuing.", {"agent": name})
    elif any(row["active"] and row["assignment"]["agent"] == name for row in data["members"]):
        raise UsageError("This worker has another active enrollment. Reconcile and resolve its existing assignment before dispatching another one.", {"agent": name})
    if persist:
        member = supervision.enroll(state_path, expected, at)
        if member["active"] and native is not None:
            known = supervision.expected_assignment(member)
            if known["native_session"] is None:
                supervision.refine(state_path, identifier, known["pane_id"] or pane_id, native, at)
    return expected


def _seat_holds(roles, task, state, state_path):
    """Developer reservations and busy workers, read from the owner records (#483)."""
    busy = {row["assignment"]["agent"]: row["assignment"]["task"]
            for row in supervision.load(state_path)["members"] if row["active"]}
    return composition.seat_holds(roles, task, recovery.developer_reservations(state["recovery"], state["assignments"]), busy)


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


def _snapshot_headroom(snapshot):
    """Each agent's headroom, read the way the planner ranks it, or None.

    Tier resolution and worker ranking must agree on the number: a snapshot
    holding "8" ranks a worker at 8% and must resolve its round at 8% too.
    """
    agents = snapshot.get("agents") if isinstance(snapshot, dict) else None
    if not isinstance(agents, dict):
        return {}
    return {name: headroom_of(name, record, lambda _message: None) for name, record in agents.items()}


def _planned_snapshot_headroom(document, state, state_path):
    """The headroom of the snapshot a plan names, or {} when it cannot be found.

    An unlocatable snapshot reads as unmeasured. A plan that de-escalated on
    it then recomputes without the de-escalation and is refused as stale,
    which is the outcome an unverifiable pressure claim should have.
    """
    ref = document.get("snapshot_ref") if isinstance(document, dict) else None
    if not isinstance(ref, dict) or not isinstance(ref.get("source"), str):
        return {}
    measured_at = ref.get("measured_at")
    if ref["source"] == str(state_path):
        matches = [snap for snap in state.get("snapshots", [])
                   if isinstance(snap, dict) and snap.get("measured_at") == measured_at]
        return _snapshot_headroom(matches[-1]) if matches else {}
    try:
        snapshot = json.loads(Path(ref["source"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(snapshot, dict) or snapshot.get("measured_at") != measured_at:
        return {}
    return _snapshot_headroom(snapshot)


def _candidate_tiers(roles, agents, rounds, fix_round=None, judge=None, excludes=None, headroom=None):
    tiered = any(agent.tiers for agent in agents)
    if not tiered and not (judge and "judge" in roles):
        if rounds:
            raise UsageError("Round selection requires configured tier tables.", {})
        return None
    candidates = {role: {} for role in roles}
    for role in roles:
        inputs = rounds.get(role, {})
        for agent in agents:
            if agent.name in (excludes or {}).get(role, []):
                continue
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
            try:
                tier = select_tier(agent, role, inputs.get("type"), inputs.get("context"), fix_round,
                                   headroom=(headroom or {}).get(agent.name))
            except MissingTierError:
                # A valid round can lack a configured row on one candidate.
                continue
            if tier is None:
                continue
            candidates[role][agent.name] = {key: tier[key] for key in (
                "round", "tier_row", "kind", "model", "effort", "multiplier", "billing_window",
                "effective_multiplier", "pressure_headroom", "de_escalated",
            )}
    return candidates


def _load_state_for_write(path, warn, *, persist_migration=True):
    """Read state a caller intends to write back, or refuse.

    `load_state_checked` leaves an unreadable file exactly as found and hands
    back an empty document. Writing that document over the file would undo
    precisely the preservation it just performed, taking the operator's whole
    ledger with it -- so a write path refuses instead, and says how to keep
    both.
    """
    state, usable = load_state_checked(path, warn=warn, persist_migration=persist_migration)
    if not usable:
        raise StateError(
            "State file {} could not be read (see the warning above), and "
            "writing an empty ledger over it would destroy its contents. "
            "Move it aside with `mv {} {}.bak` to start fresh, or pass "
            "--state at another path to keep it.".format(path, path, path),
            {"path": str(path)},
        )
    return state


def _read_record(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError("Cannot read record {}: {}. Supply readable UTF-8 JSON with the documented fields.".format(path, exc), {}) from None
    if not isinstance(data, dict):
        raise UsageError("A record must be a JSON object; use the documented field names.", {})
    return data


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


def _judge_mode_for(args, document):
    """The judge seat's mode: the plan's, and a supplied one must agree.

    The plan records the choice the lead made when it composed the brief. A
    flag that differs would hold the seat to the other gate than the one it was
    planned for -- a diagnosis plan passing the adjudication gate -- so the
    mismatch refuses before any worker contact (#425).
    """
    supplied = getattr(args, "judge_mode", None)
    block = document.get("judge") if isinstance(document, dict) else None
    if not isinstance(block, dict):
        # Not a plan document -- a bare {role: agent} map carries no seat, so
        # the flag is the only source there is.
        return supplied
    planned = block.get("mode")
    if planned is None:
        # A plan that seats the judge and declares no mode is a plan from
        # before the mode existed. Re-plan rather than let a flag supply what
        # its brief was never composed for (state-schema.md, plan schema 6).
        raise UsageError(
            "This plan seats the judge without a declared mode; re-plan with --judge-mode {} rather than supplying one here.".format(" | ".join(recovery.JUDGE_MODES)),
            {},
        )
    if supplied and supplied != planned:
        raise UsageError(
            "This plan seats the judge for {!r} and --judge-mode says {!r}; the plan's mode is the one its brief was composed for. Re-plan for the other mode rather than overriding it here.".format(planned, supplied),
            {"planned": planned, "supplied": supplied},
        )
    return planned


def _expand_partition_seats(roles, partition_path):
    """Replace the partitioned role with one seat per slice.

    Returns `(roles, {seat: role}, {seat: [glob, ...]})`. Without a partition
    the round is untouched, which is every single-seat round (#409).
    """
    if not partition_path:
        return roles, {}, {}
    document = partition.load_validated(partition_path)
    role = partition.partition_role(document)
    if role not in roles:
        raise PlanError(
            "The partition seats {!r}, which --roles does not request; add it, or drop --partition.".format(role),
            {"role": role, "roles": roles},
        )
    seats = partition.seats_for(document, role)
    expanded = []
    for item in roles:
        expanded.extend(seats) if item == role else expanded.append(item)
    return expanded, seats, partition.seat_paths(document, role)


def _fan_out_seats(mapping, seats):
    """Re-key a role-keyed input onto the seats that replaced the role.

    The expanded role's own key is dropped: the seats ARE the roles this plan
    assigns, and a leftover `reviewer` key names a role it is not filling.
    """
    if not seats or not mapping:
        return mapping
    expanded = set(seats.values())
    fanned = {key: value for key, value in mapping.items() if key not in expanded}
    for seat, role in seats.items():
        if seat in mapping:
            fanned[seat] = mapping[seat]
        elif role in mapping:
            fanned[seat] = mapping[role]
    return fanned


def _fan_out_exclusions(mapping, seats):
    """Re-key an exclusion map onto its seats, unioning the role's bars in.

    An exclusion is a BAR, not a setting a seat overrides. A seat inherits
    every name barred from its role and adds whatever the lead barred from the
    seat itself, so `--exclude reviewer#api=beta` never lifts the contributor
    `alpha` the role already excludes (#434).
    """
    if not seats or not mapping:
        return mapping
    expanded = set(seats.values())
    fanned = {key: value for key, value in mapping.items() if key not in expanded}
    for seat, role in seats.items():
        barred = sorted(set(mapping.get(role, ())) | set(mapping.get(seat, ())))
        if barred:
            fanned[seat] = barred
    return fanned


def cmd_plan(args, client=None, warn=None, trace=None):
    # `canonical` is what every module reasoning about RESPONSIBILITY sees;
    # `roles` carries the seat identity and reaches the planner alone (#434).
    canonical = [require_seatable(role.strip()) for role in args.roles.split(",") if role.strip()]
    # `--roles` names RESPONSIBILITIES. A seat comes from `--partition` alone,
    # which is the declared surface split `validate-partition` checks disjoint
    # and exhaustive; accepting a pre-seated name here would plan seats against
    # no declared partition at all (#434).
    seated = [role for role in canonical if SEAT_SEPARATOR in role]
    if seated:
        raise UsageError(
            "--roles names responsibilities, not seats: {} came pre-seated. Pass {} and "
            "seat the slices with --partition, after validate-partition has checked it "
            "disjoint and exhaustive over the round's change.".format(
                ", ".join(seated), ", ".join(sorted({canonical_role(role) for role in seated}))),
            {"roles": seated})
    roles, seats, seat_paths = _expand_partition_seats(canonical, getattr(args, "partition", None))
    if "judge" in canonical:
        recovery.require_judge_mode(getattr(args, "judge_mode", None))
    excludes = _parse_excludes(args.excludes)
    role_costs = load_role_costs(_config_path(args))
    judge = load_judge(_config_path(args))
    rounds = _round_inputs(args, canonical)
    agents = load_config(_config_path(args)) if _config_path(args).exists() else []
    state_path = _state_path(args)
    state = load_state(state_path, warn=warn)
    requirements = composition.parse_requirements(_read_record(args.requirements) if args.requirements else None, canonical, args.task)
    work = _read_record(args.work) if args.work else None
    recovery.validate_work(state["recovery"], state["assignments"], args.task, args.fix_round,
                           args.correction_plan, work, implementation="developer" in canonical)
    if args.task:
        validate_fix_history({role: None for role in canonical}, state["assignments"], args.task, args.fix_round)
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

    operator_excludes = {role: list(names) for role, names in excludes.items()}
    constraints = composition.selection_constraints(
        canonical, agents, requirements, state["assignments"], args.task,
        dispatches=state["recovery"]["dispatches"], assessments=state["specialist_assessments"],
        candidate_names=snapshot.get("agents", {}).keys() if isinstance(snapshot.get("agents"), dict) else (),
    )
    holds = _seat_holds(canonical, args.task, state, state_path)
    for bars in (constraints, holds):
        for role, names in bars["exclude"].items():
            excludes[role] = sorted(set(excludes.get(role, [])) | set(names))
    constraints = {**constraints, "rationale": constraints["rationale"] + holds["rationale"]}
    # Tier candidacy is decided per RESPONSIBILITY, so it reads the role-keyed
    # bars alone: a seat key would reach the planner's exclusion parser as an
    # unknown role (#434).
    # The SAME measurement the planner ranks workers with also resolves each
    # seat's round, so headroom can buy a cheaper round and not only a cheaper
    # pair. `apply` re-reads it off the plan rather than re-measuring, which is
    # what keeps its recomputed tiers equal to the planned ones (#477).
    measured_headroom = _snapshot_headroom(snapshot)
    tier_candidates = _candidate_tiers(canonical, agents, rounds, args.fix_round, judge,
                                      excludes={role: names for role, names in excludes.items() if role in set(canonical)},
                                      headroom=measured_headroom)
    # Each seat inherits its role's bars, tiers, round type and requirements.
    # `role_costs` is not fanned out: the planner resolves a seat's default
    # weight and rotation history through its role (#434).
    excludes = _fan_out_exclusions(excludes, seats)
    operator_excludes = _fan_out_exclusions(operator_excludes, seats)
    rounds = _fan_out_seats(rounds, seats)
    requirements = _fan_out_seats(requirements, seats)
    tier_candidates = _fan_out_seats(tier_candidates, seats)
    # `rationale` is a list of sentences, not a role-keyed map: fanning it out
    # would crash, and each seat's own line is already in it (#434).
    constraints = {**constraints,
                   "familiarity": _fan_out_seats(constraints["familiarity"], seats)}

    result = build_plan(
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
            judge_mode=getattr(args, "judge_mode", None),
            snapshot_ref={"source": source, "measured_at": snapshot.get("measured_at")},
            warn=warn,
            tier_candidates=tier_candidates,
            rounds=rounds,
            requirements=requirements,
            familiarity=constraints["familiarity"],
            selection_rationale=constraints["rationale"],
            roster=[agent.name for agent in agents],
            operator_exclude=operator_excludes,
        )
    result["task_context"] = ({"task": args.task, "fix_round": args.fix_round,
                               "plan": args.correction_plan, "work": work} if args.task else None)
    # The composer requires each seat's owned paths and reads no partition, so
    # the plan hands them over from the document `validate-partition` accepted.
    if seat_paths:
        accepted = {seat: paths for seat, paths in seat_paths.items() if seat in result["assignments"]}
        result["slice_paths"] = accepted
        # The digest travels with the map, into each brief and back at dispatch,
        # so an edit between the validated partition and the send is refused.
        result["slice_digest"] = partition.slice_digest(accepted)
        # Per seat as well: one digest for the whole round is identical in
        # every brief, so swapping two seats' briefs would pass a check that
        # only asks whether a digest is present.
        result["seat_digests"] = {seat: partition.seat_digest(seat, paths)
                                  for seat, paths in accepted.items()}
    return result, None


def _refusal_moves(store, agents_by_name, assignments, roles, args, paths, reports):
    """Return the refusal move each fresh role carries; see recovery.refusal_move."""
    moves = {}
    for role in roles:
        name = assignments[role]
        if name in agents_by_name:
            move = recovery.refusal_move(store, args.task, role, args.fix_round, agents_by_name[name].kind,
                                         recovery.brief_identity(paths, role, reports.get(role)), reports.get(role))
            if move is not None:
                moves[role] = move
    return moves


def _require_bound_slices(document, seated, briefs):
    """Refuse a seated dispatch whose boundary is not the one that was checked.

    `plan` stamps `slice_digest` over the map `validate-partition` accepted and
    `compose-briefs.sh` renders it into each seat's brief. Recomputing it here
    catches a plan edited after planning, and reading it back out of the brief
    catches a brief composed against a different boundary or written by hand —
    the two places a human artifact sits between the check and the send (#453).
    """
    recorded = document.get("slice_digest")
    slice_paths = document.get("slice_paths")
    if not isinstance(recorded, str) or not isinstance(slice_paths, dict):
        raise UsageError(
            "Seats {} need the plan's slice_paths and slice_digest: seat them with "
            "`plan --partition <validate-partition output>` rather than hand-writing the "
            "assignments, so the boundary that ships is the one that was checked.".format(
                ", ".join(seated)),
            {"roles": seated})
    # Shape before hashing: `slice_paths` rides in an editable `--assignments`
    # document, and a seat mapped to a non-list — or to a list carrying a
    # non-string — reaches the digest as an unhashable value and leaves a
    # traceback where this function promises a refusal.
    malformed = [seat for seat, globs in slice_paths.items()
                 if not isinstance(seat, str) or not isinstance(globs, list) or not globs
                 or any(not isinstance(glob, str) or not glob.strip() for glob in globs)]
    if malformed:
        raise UsageError(
            "The plan's slice_paths maps {} to something other than a non-empty list of "
            "globs; re-run `plan --partition <validate-partition output>` rather than "
            "editing the assignments.".format(
                ", ".join(repr(seat) for seat in sorted(map(str, malformed)))),
            {"seats": [str(seat) for seat in malformed]})
    # A glob is rendered verbatim into the brief, so a backtick or a control
    # character closes the code span and appends instructions of its own.
    # `validate_document` and the composer both refuse these; a hand-written
    # plan reaches the renderer without passing either.
    unsafe = sorted(seat for seat, globs in slice_paths.items()
                    if any(partition.UNSAFE_GLOB.search(glob) for glob in globs))
    if unsafe:
        raise UsageError(
            "The plan's slice_paths gives {} a glob carrying a backtick or a control "
            "character, which the brief renders verbatim; re-run `plan --partition "
            "<validate-partition output>` rather than editing the assignments.".format(
                ", ".join(repr(seat) for seat in unsafe)),
            {"seats": unsafe})
    # Exactly the seated assignments, no more and no less: a plan stripped of a
    # seat would otherwise dispatch the remainder as if the partition still
    # covered the change, and one stripped of all of them a full-surface role.
    if sorted(slice_paths) != sorted(seated):
        raise UsageError(
            "The plan's slice_paths covers {} but this apply seats {}; the boundary and "
            "the round no longer describe the same partition. Replan rather than editing "
            "the assignments.".format(
                ", ".join(repr(seat) for seat in sorted(map(str, slice_paths))) or "nothing",
                ", ".join(repr(role) for role in sorted(seated)) or "nothing"),
            {"slice_paths": sorted(map(str, slice_paths)), "seated": sorted(seated)})
    expected = partition.slice_digest(slice_paths)
    if expected != recorded:
        raise UsageError(
            "The plan's slice_paths no longer match its slice_digest ({} vs {}); the "
            "boundary changed after planning. Re-run validate-partition and plan rather "
            "than editing either.".format(expected, recorded),
            {"expected": expected, "recorded": recorded})
    for role in seated:
        if role not in slice_paths:
            raise UsageError(
                "Seat {!r} is not in the plan's slice_paths, so its boundary was never "
                "checked; plan the round from the validated partition.".format(role),
                {"role": role})
        brief = briefs.get(role)
        try:
            body = Path(brief).read_text(encoding="utf-8") if brief else ""
        except (OSError, UnicodeError) as exc:
            raise UsageError(
                "Cannot read the brief for seat {!r} at {}: {}. Restore a readable UTF-8 brief "
                "at that path, or regenerate the round's briefs with compose-briefs.sh, then "
                "re-run apply.".format(role, brief, exc),
                {"role": role}) from None
        # The whole scope block, not the facts it contains. A brief that
        # scatters the digest, the slice name and a path while directing a
        # whole-repository pass satisfies three substring checks and still
        # dispatches a full-surface verdict as a slice one; the block carries
        # its own restrictions, so requiring it requires those too.
        expected_seat = partition.seat_digest(role, slice_paths[role])
        expected_scope = partition.slice_scope(role, slice_paths[role], expected_seat)
        if expected_scope not in body:
            raise UsageError(
                "The brief for seat {!r} does not carry this seat's scope block, so it "
                "was not composed against the boundary this plan checked. Compose it "
                "with `compose-briefs.sh` from the plan's slice_paths and seat_digests; "
                "the block it renders reads: {}".format(role, expected_scope),
                {"role": role, "expected_scope": expected_scope})


def cmd_apply(args, client=None, warn=None, trace=None):
    agents = load_config(_config_path(args))
    agents_by_name = {agent.name: agent for agent in agents}
    document = _load_assignments(args.assignments, document=True)
    assignments = normalize_assignments(document)
    # The ledger row records the responsibility and the DISPATCH records the
    # seat, and a dispatch exists only under a task. Without one, a seated
    # round would leave nothing that names the slice, so its verdict could
    # never be read back (#434).
    seated = sorted(role for role in assignments if SEAT_SEPARATOR in role)
    if seated and not (isinstance(args.task, str) and args.task.strip()):
        raise UsageError(
            "Seats {} need --task: the slice lives on the dispatch, which a task-less "
            "apply never records, and its verdict would have nothing to be read back "
            "through.".format(", ".join(seated)),
            {"roles": seated})
    requirements = composition.parse_requirements(
        {"schema_version": 1, "assignments": document["requirements"]} if "requirements" in document else None,
        list(assignments), args.task, allow_historical_architect=True,
    )
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
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn, persist_migration=not args.dry_run)
    store = state["recovery"]
    at = args.now or now_iso()
    work = _read_record(args.work) if args.work else None
    task_context = {"task": args.task, "fix_round": args.fix_round, "plan": args.correction_plan, "work": work}
    if document.get("task_context") is not None and document["task_context"] != task_context:
        raise UsageError("Saved plan and apply name different task, count or correction bounds; replan from the current ledger.", {})
    paths = resolve_paths(assignments, _parse_briefs(args.briefs), args.common)
    if seated or any(key in document for key in ("slice_paths", "slice_digest", "seat_digests")):
        # Keyed on the metadata, not only on the seats: a saved plan stripped
        # of every seat would otherwise skip the check entirely and dispatch a
        # full-surface role while still carrying the boundary it was planned
        # against. After the briefs resolve, since the check reads each brief.
        _require_bound_slices(document, seated, paths)
    reports = _parse_reports(args.reports, assignments)
    supervised = supervision.dispatch_binding(state_path) is not None
    if requirements and not args.dry_run and not supervised:
        raise UsageError("Bind the lead with supervision-bind before dispatching specialist requirements; every specialist needs durable observation ownership.", {})
    if supervised and (not args.task or set(reports) != set(assignments)):
        raise UsageError("Bound team rounds require --task and one --report ROLE=ABS_PATH for every assigned role before any worker input.", {})
    # The mode is part of what a judge dispatch IS: one brief sent as an
    # adjudication and as a diagnosis are two dispatches, so the mode is
    # resolved before any identity is computed (#478).
    judge_mode = None
    if any(canonical_role(role) == "judge" for role in assignments):
        judge_mode = recovery.require_judge_mode(
            _judge_mode_for(args, document if isinstance(document, dict) else None))
    replayed = []
    dispatches = {}
    # Check retry identities before next-attempt validation: a completed retry
    # returns its original outcome and never consumes a second attempt.
    if args.task and not args.dry_run:
        resolved = []
        for role, name in assignments.items():
            options = {**task_context, "rounds": rounds, "retain_context": args.retain_context, "no_clear": args.no_clear}
            if requirements:
                options["requirements"] = requirements
            if args.retain_specialist:
                options["retain_specialist"] = True
            if canonical_role(role) == "judge":
                # A judge dispatch recorded before the mode joined its identity
                # carries the mode-less fingerprint. Re-running it after the
                # upgrade must not read as new work and send the round twice.
                _legacy_id, legacy = recovery.dispatch_identity(
                    args.task, role, name, args.fix_round, paths, None, options=options)
                bound = supervision.report_bound_fingerprint(legacy, reports[role]) if role in reports else None
                # `not_sent` reached no worker and stays retryable, as ever.
                earlier = next((row for row in store["dispatches"]
                                if row.get("fingerprint") in {legacy, bound} and "judge_mode" not in row
                                and row.get("status") != "not_sent"), None)
                if earlier is not None:
                    raise UsageError(
                        "Judge dispatch {} was recorded before its mode was part of its identity and "
                        "has status {!r}. Inspect its recorded outcome instead of sending it again; "
                        "a fresh judge round needs a changed brief.".format(earlier["id"], earlier["status"]),
                        {"dispatch": earlier["id"]})
                options["judge_mode"] = judge_mode
            identifier, fingerprint = recovery.dispatch_identity(
                args.task, role, name, args.fix_round, paths, args.dispatch_id,
                options=options)
            if supervised:
                # Keep legacy retry IDs, while new bound dispatch fingerprints
                # also bind the explicit report path. Existing legacy receipts
                # cannot retroactively prove a report input they never stored.
                old = next((row for row in store["dispatches"] if row["id"] == identifier), None)
                if old is None or old["fingerprint"] != fingerprint:
                    fingerprint = supervision.report_bound_fingerprint(fingerprint, reports[role])
            prior = recovery.prior_dispatch(store, identifier, fingerprint)
            resolved.append((role, name, identifier, fingerprint, prior))
        fresh = [role for role, _name, _identifier, _fingerprint, prior in resolved if not (prior and prior["status"] == "applied")]
        moves = {}
        if fresh:
            # The batch holds a new send. An unanswered decision or blocker on
            # the task, a same-provider resend of a refused brief, a reworded
            # brief, or a second move refuses it here (#399), before a
            # replayed sibling's enrollment is re-saved, so a refused apply
            # writes nothing. A batch of replays alone returns its saved
            # receipts unconsulted.
            attention.require_dispatch_clear(state_path, args.task, at)
            moves = _refusal_moves(store, agents_by_name, assignments, fresh, args, paths, reports)
        for role, name, identifier, fingerprint, prior in resolved:
            if supervised:
                saved_result = prior["result"] if prior and prior["status"] == "applied" else {}
                _supervision_enrollment(state_path, identifier, args.task, role, name, reports[role], at,
                    pane_id=saved_result.get("pane_id"), native=saved_result.get("context_session"),
                    replay=bool(saved_result), persist=bool(saved_result))
            if prior and prior["status"] == "applied":
                replayed.append({**prior["result"], "dispatch_id": identifier, "replayed": True})
            else:
                dispatches[role] = {"id": identifier, "fingerprint": fingerprint, "role": role, "agent": name,
                                    "task": args.task, "fix_round": args.fix_round,
                                    "plan": args.correction_plan, "work": work,
                                    "brief_identity": recovery.brief_identity(paths, role, reports.get(role)),
                                    "provider": agents_by_name[name].kind}
                if role in moves:
                    dispatches[role]["refusal_move"] = moves[role]
                if role in requirements:
                    dispatches[role]["requirements"] = requirements[role]
                if canonical_role(role) == "judge":
                    dispatches[role]["judge_mode"] = judge_mode
                if canonical_role(role) == "reviewer":
                    dispatches[role]["reviewer_scope"] = "design" if rounds.get(role, {}).get("type") in {"architect", "reconciliation"} else "verification"
        if len(replayed) == len(assignments):
            return {"schema_version": APPLY_SCHEMA_VERSION, "dry_run": False, "applied_at": at, "applied": replayed}, None
        assignments = {role: name for role, name in assignments.items() if role in dispatches}
        requirements = {role: value for role, value in requirements.items() if role in assignments}
    elif args.dispatch_id and not args.task:
        raise UsageError("--dispatch-id requires --task; preserve the task's identity for retry accounting.", {})
    elif args.task:
        # A dry run rehearses a send and meets the same gates (#399).
        attention.require_dispatch_clear(state_path, args.task, at)
        _refusal_moves(store, agents_by_name, assignments, list(assignments), args, paths, reports)
    # A fresh judge seat at an exhausted allowance waits for the assessment it
    # rules on, dry runs included. A completed replay has left `assignments`
    # already, so it is not re-gated (#408).
    if args.task and "judge" in assignments:
        # The RESOLVED mode, not the flag: a planned diagnosis dispatched
        # without one would otherwise reach the gate as None and skip the stop
        # refusal it owes (#425).
        recovery.require_investigation_before_judge(store, state["assignments"], args.task,
                                                    state["specialist_assessments"],
                                                    mode=judge_mode)
    recovery.validate_work(store, state["assignments"], args.task, args.fix_round,
                           args.correction_plan, work,
                           implementation=any(canonical_role(role) == "developer" for role in assignments))
    constraints = composition.selection_constraints(
        list(assignments), agents, {role: value for role, value in requirements.items() if role in assignments},
        state["assignments"], args.task, dispatches=store["dispatches"],
        assessments=state["specialist_assessments"], candidate_names=assignments.values(),
    )
    for role, name in assignments.items():
        if name in constraints["exclude"].get(role, []):
            raise UsageError("Assigned worker {} is ineligible for {} under current capabilities or contribution history; replan an independent capable worker.".format(name, role), {})
    # A plan's holds can be stale by dispatch time; the send re-reads them (#483).
    reserved = recovery.developer_reservations(store, state["assignments"])
    # `apply` measures nothing -- it re-reads the headroom the PLAN resolved its
    # tiers against, so a recomputed tier differs only when the config or the
    # fix context actually drifted, which is what the comparison below is for.
    # The headroom comes from the snapshot the plan names, never from the
    # plan's own `pressure_headroom`: a plan edited to claim scarcity would
    # otherwise recompute its own downgrade and pass the comparison below.
    planned_headroom = _planned_snapshot_headroom(document, state, state_path)
    candidates = _candidate_tiers(list(assignments), agents, rounds, args.fix_round, judge,
                                  excludes=constraints["exclude"], headroom=planned_headroom)
    tiers = {}
    if candidates is not None:
        for role, name in assignments.items():
            if name not in candidates.get(role, {}):
                raise UsageError("Assigned agent {} has no eligible tier for {}; replan from current config.".format(name, role), {})
            if candidates[role][name] is not None:
                tiers[role] = candidates[role][name]
        saved_tiers = {role: tier for role, tier in document.get("tiers", {}).items() if tier is not None and role in assignments} if isinstance(document.get("tiers", {}), dict) else None
        if "tiers" in document and saved_tiers != tiers:
            raise UsageError("Plan tiers differ from current config or fix context; re-run plan before dispatch.", {})
    client = client if client is not None else _client(args, trace=trace)

    if args.retain_specialist:
        engagement.require_followup(state, state_path, assignments)

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
                recovery=store, history=state["assignments"], plan_id=args.correction_plan, work=work,
                retain_specialist=args.retain_specialist, requirements=requirements,
                reserved=reserved,
            ),
            None,
        )

    prepared = []

    def prepare(step, observed):
        if not args.task:
            return
        record = {**dispatches[step["role"]], "observed_before": observed,
                  "brief": step["brief"], "common": step["common"]}
        if supervised:
            _supervision_enrollment(state_path, record["id"], args.task, step["role"], step["agent"], reports[step["role"]], at,
                                    pane_id=step["pane_id"], persist=True)
        recovery.reserve(store, record, at)
        save_state(state_path, state)
        prepared.append(record["id"])

    def observed_native(role, name, context):
        native = context.get("context_session")
        if native is None and role != "developer":
            native = native_context_session(client.agent_get(name), agents_by_name[name].kind)
            prior = next(row for row in store["dispatches"] if row["id"] == dispatches[role]["id"])
            if context.get("cleared") and native == prior["observed_before"].get("context_session"):
                native = None
        return native

    def before_send(step, context):
        if args.task:
            if supervised:
                native = observed_native(step["role"], step["agent"], context)
                _supervision_enrollment(state_path, dispatches[step["role"]]["id"], args.task, step["role"], step["agent"], reports[step["role"]], at,
                                        pane_id=step["pane_id"], native=native, persist=True)
            recovery.mark_sending(store, dispatches[step["role"]]["id"], at, context)
            save_state(state_path, state)

    def record(result):
        seat = result["role"]
        base = canonical_role(seat)
        if base == "reviewer":
            result["reviewer_scope"] = "design" if rounds.get(seat, {}).get("type") in {"architect", "reconciliation"} else "verification"
        context = {key: result[key] for key in ("cleared", "clear_reason", "task", "fix_round", "context_session", "tier")}
        context["requirements"] = result.get("requirements")
        context["reviewer_scope"] = result.get("reviewer_scope")
        context["judge_mode"] = result.get("judge_mode") if base == "judge" else None
        # `add_assignment` records the RESPONSIBILITY a seat fills; the seat
        # stays on the dispatch, which is what a slice's verdict is read back
        # through (#434).
        add_assignment(state, at, seat, result["agent"], status=result["status"], **context)
        if args.task:
            result["dispatch_id"] = dispatches[result["role"]]["id"]
            recovery.finish_dispatch(store, result["dispatch_id"], dict(result), len(state["assignments"]) - 1, at)
        save_state(state_path, state)
        # Commit the real transport outcome before optional identity refinement;
        # a failed sidecar write must never make a confirmed send replayable.
        if supervised and result["status"] == "applied":
            native = observed_native(result["role"], result["agent"], result)
            _supervision_enrollment(state_path, result["dispatch_id"], args.task, result["role"], result["agent"], reports[result["role"]], at,
                                    pane_id=result["pane_id"], native=native, persist=True)

    try:
        result = apply_assignments(
            client,
            assignments,
            agents_by_name,
            paths,
            at,
            no_clear=args.no_clear,
            retain_context=args.retain_context,
            fix_round=args.fix_round,
            judge_mode=judge_mode,
            history=state["assignments"],
            settle_timeout_ms=args.settle_timeout,
            on_prepare=prepare, on_before_send=before_send, on_result=record,
            recovery=store, plan_id=args.correction_plan, work=work,
            warn=warn,
            task=args.task,
            retain_specialist=args.retain_specialist, requirements=requirements,
            settle_sec=args.composer_settle,
            start_timeout_ms=args.start_timeout,
            allow_recovery=args.allow_recovery,
            tiers=tiers,
            reserved=reserved,
            retrospective_guard=retrospective_runtime.Guard(state_path, state, client, agents_by_name, at,
                                                          task=args.task, retain=args.retain_context or args.retain_specialist, no_clear=args.no_clear),
        )
    except TeamLeadError as exc:
        for identifier in prepared:
            recovery.abort_pre_send(store, identifier, at, str(exc))
        if prepared:
            save_state(state_path, state)
        raise
    result["applied"] = replayed + result["applied"]
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


def cmd_status(args, client=None, warn=None, trace=None):
    state = load_state(_state_path(args), warn=warn)
    return {"schema_version": recovery.RECOVERY_SCHEMA_VERSION,
            "tasks": recovery.task_statuses(state["recovery"], state["assignments"])}, None


def cmd_foreman_reset(args, client=None, warn=None, trace=None, spawn=None):
    state_path = Path(_state_path(args)).expanduser().resolve()
    at = args.now or now_iso()
    stow = memory.show(state_path, at, args.stow)["record"]
    data = supervision.load(state_path)
    bound_pane = (data.get("binding") or {}).get("identity", {}).get("pane_id")
    # A retry replays before any new-reset precondition (see foreman_reset.replay).
    caller = os.environ.get("HERDR_PANE_ID")
    if bound_pane and caller != bound_pane:
        raise UsageError("foreman-reset runs from the bound foreman's own pane ({}); this call came from {}.".format(
            bound_pane, caller or "outside Herdr"), {"pane_id": bound_pane})
    existing = foreman_reset.replay(state_path, {"pane_id": bound_pane, "stow": stow["id"]}) if bound_pane else None
    if existing is not None:
        return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "scheduled": True, **existing,
                "log": str(Path(str(state_path) + ".foreman-reset.log"))}, None
    plan = foreman_reset.preflight(stow, data, os.environ.get("HERDR_PANE_ID"))
    log = Path(str(state_path) + ".foreman-reset.log")
    # The deliverer runs from the package directory, so every path it gets is absolute.
    argv = [sys.executable, "-m", "teamlead", "foreman-reset-deliver", "--pane", plan["pane_id"], "--stow", plan["stow"],
            "--state", str(state_path), "--config", str(Path(_config_path(args)).expanduser().resolve())]
    if getattr(args, "herdr_bin", None):
        argv += ["--herdr-bin", _absolute_executable(args.herdr_bin)]

    def start():
        try:
            with open(log, "ab") as sink:
                return (spawn or _spawn_detached)(argv, sink)
        except OSError as exc:
            raise StateError("Could not start the reset deliverer ({}); nothing was sent. Fix the cause named here, then run foreman-reset again.".format(exc),
                             {"log": str(log)}) from None

    row = foreman_reset.schedule(state_path, plan, at, start)
    return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "scheduled": True, **row, "log": str(log),
            "next": "End this turn now; the deliverer clears the pane once it is idle."}, None


def _absolute_executable(value):
    """A relative executable path resolved now, before the deliverer changes directory."""
    return str(Path(value).expanduser().resolve()) if os.sep in value else value


def _spawn_detached(argv, sink):
    """Start `argv` in its own session so it outlives the foreman's turn."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                               start_new_session=True, cwd=str(Path(__file__).resolve().parents[1]))
    return process.pid


def cmd_foreman_reset_deliver(args, client=None, warn=None, trace=None):
    state_path = Path(_state_path(args)).expanduser().resolve()
    plan = {"pane_id": args.pane, "stow": args.stow}
    if not foreman_reset.claim(state_path, plan, supervision_runtime.process_identity(os.getpid())):
        return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, **plan, "skipped": "not the scheduled owner of this reset"}, None
    try:
        # Setup runs after the claim, so its failure must finish the row too.
        client = client if client is not None else _client(args, trace=trace)
        result = foreman_reset.deliver(
            client, load_config(_config_path(args)), args.pane, args.stow, str(state_path), warn=warn,
            still_ready=lambda: memory.show(state_path, now_iso(), args.stow)["record"].get("reset_ready") is True)
    except TeamLeadError as exc:
        status = "interrupted" if isinstance(exc, foreman_reset.DeliveryInterrupted) else "failed"
        foreman_reset.finish(state_path, plan, status, foreman_reset.failure(exc, args.stow, str(state_path)))
        raise
    foreman_reset.finish(state_path, plan, "delivered", result)
    return result, None


def cmd_foreman_queue(args, client=None, warn=None, trace=None):
    state_path = _state_path(args)
    # Strict and read-only: an unusable ledger must fail, never read as an
    # empty queue that hides every waiting task.
    state, usable = load_state_checked(state_path, warn=warn, persist_migration=False)
    if not usable:
        raise StateError("State file {} is unusable, so the queue cannot be derived; restore it before planning.".format(state_path),
                         {"path": str(state_path)})
    busy = {row["assignment"]["task"] for row in supervision.load(state_path)["members"] if row["active"]}
    return foreman_queue.waiting(state["recovery"], state["assignments"], busy), None


def cmd_load_set(args, client=None, warn=None, trace=None):
    if (args.decision == "wake") != (args.enrollment is not None):
        raise UsageError("Pass --enrollment for wake and --task for every other decision.", {"decision": args.decision})
    state_path = _state_path(args)
    state, usable = load_state_checked(state_path, warn=warn, persist_migration=False)
    if not usable:
        raise StateError("State file {} is unusable, so the load set cannot be derived; restore it before deciding.".format(state_path),
                         {"path": str(state_path)})
    members = supervision.load(state_path)["members"]
    reports = {row["id"]: row["assignment"].get("report") for row in members}
    busy = {row["assignment"]["task"] for row in members if row["active"]}
    _document, entries, _progress = attention.load(state_path)
    if args.decision == "wake" and (args.enrollment not in reports
                                    or not any(row.get("id") == args.enrollment for row in state["recovery"]["dispatches"])):
        raise UsageError("Enrollment {} has no supervision enrollment with a recorded dispatch; read supervision-status for the enrollment id.".format(args.enrollment), {})
    if args.decision != "wake" and args.task not in state["recovery"]["tasks"] and not any(
            row.get("task") == args.task for row in state["assignments"]):
        raise UsageError("Task {!r} is neither registered nor assigned; check its identity with `teamlead state`.".format(args.task), {})
    return load_set.build(state, reports, entries, busy, args.decision, task=args.task, enrollment=args.enrollment,
                          exists=_file_present), None


def _file_present(path):
    """Whether a listed file is readable, or a StateError naming what blocked the probe."""
    try:
        return Path(path).is_file()
    except OSError as exc:
        raise StateError("Cannot check {} ({}); restore access to it before this decision.".format(path, exc),
                         {"path": path}) from None


def _require_independent_report(state, task, reviewer):
    """Apply the same contribution evidence to live and imported reviews."""
    if not isinstance(reviewer, str):
        return  # The owning receipt validator reports malformed input.
    constraints = composition.selection_constraints(
        ["reviewer"], [], {}, state["assignments"], task,
        dispatches=state["recovery"]["dispatches"],
        assessments=state["specialist_assessments"], candidate_names=[reviewer],
    )
    if reviewer in constraints["exclude"]["reviewer"]:
        raise UsageError("This reviewer contributed to the task; collect an independent report before recording approval.", {})


def _record_stopped_task(state_path, diagnosis, at):
    """Surface a terminal diagnosis to the operator.

    A `stop` remedy ends implementation and records the remainder as a tracked
    accepted defect, and no exhausted allowance waits on an operator decision.
    The operator still holds the override, and cannot exercise one they never
    learn they have (#415), so the terminal remedy lands in the attention queue
    the catch-up presents. Its kind sits outside `attention.GATING_KINDS`: this
    surfaces the outcome, it never gates the next dispatch.

    The obligation identity is derived from the diagnosis identity, which is
    free text, so the digest keeps it inside the queue's identifier alphabet
    and keeps a replayed diagnosis on its original obligation.
    """
    name = "diagnosis-stop-" + hashlib.sha256(diagnosis["id"].encode("utf-8")).hexdigest()[:16]
    return attention.write(state_path, "record", {
        "id": name,
        "kind": "failure",
        "task": diagnosis["task"],
        "priority": 80,
        "title": "Task {} stopped at the judge's diagnosis".format(diagnosis["task"])[:300],
        "context": "Diagnosis {} returned REMEDY: stop at fix round {}, ruling on the investigator's assessment.".format(
            diagnosis["id"], diagnosis["fix_round"]),
        "consequence": "Implementation on this task has ended. What is clean ships; the remainder is a tracked accepted defect under rules/review-severity.md Judge-Accepted Defect Carve-Out.",
        "resolution_condition": "Record the acknowledgement, authorize a plan over this remedy, or approve a different approach with `teamlead authorize-approach` to override it.",
        "sources": [{"schema_version": attention.SCHEMA_VERSION, "kind": "artifact",
                     "ref": diagnosis["judge_evidence"]["path"]}],
    }, at)


def cmd_recovery(args, client=None, warn=None, trace=None):
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)
    store, history = state["recovery"], state["assignments"]
    data, at = _read_record(args.record), args.now or now_iso()
    if args.command == "task":
        result = recovery.register_task(store, data, at)
    elif args.command == "close-task":
        result = recovery.close_task(store, history, data, at)
    elif args.command == "checkpoint":
        judge = load_judge(_config_path(args))
        result = recovery.checkpoint(store, history, data, at, judge.agent if judge else None)
    elif args.command == "authorize-corrections":
        result = recovery.authorize_plan(store, history, data, at)
    elif args.command == "authorize-approach":
        result = recovery.authorize_approach(store, history, data, at)
    elif args.command == "diagnose":
        judge = load_judge(_config_path(args))
        # Supervision knows where the pinned judge's report was meant to land;
        # a dispatch marked applied proves only the send (#407). The
        # enrollment is resolved by that dispatch's own identity, so an older
        # enrollment for the same task and judge cannot stand in for it
        # (#412).
        enrolled = None
        if judge is not None and isinstance(data, dict):
            dispatch = recovery.applied_judge_dispatch(store, history, data.get("task"), judge.agent)
            if dispatch is not None:
                member = next((item for item in supervision.load(state_path)["members"]
                               if item["id"] == dispatch["id"]), None)
                if member is not None:
                    enrolled = supervision.expected_assignment(member)["report"]
        result = recovery.diagnose(store, history, data, at, judge.agent if judge else None, enrolled,
                                   supervision.dispatch_binding(state_path) is not None,
                                   state["specialist_assessments"])
        if result["remedy"] == "stop":
            _record_stopped_task(state_path, result, at)
    elif args.command == "record-report":
        if isinstance(data, dict):
            dispatch = next((item for item in store["dispatches"] if item["id"] == data.get("dispatch")), None)
            if dispatch is not None:
                _require_independent_report(state, dispatch["task"], data.get("reviewer"))
        result = recovery.record_report(store, data, at)
    elif args.command == "authorize-refused-dispatch":
        result = recovery.authorize_refused_dispatch(store, data, at)
    elif args.command == "record-refusal":
        agents_by_name = {agent.name: agent for agent in load_config(_config_path(args))}
        dispatch = next((item for item in store["dispatches"] if isinstance(data, dict) and item["id"] == data.get("dispatch")), None)
        if dispatch is not None and dispatch["agent"] not in agents_by_name:
            raise UsageError("Refused worker {} is not in config.json; restore its entry so the refusing provider is recorded.".format(dispatch["agent"]), {})
        member = next((row for row in supervision.load(state_path)["members"] if dispatch is not None and row["id"] == dispatch["id"]), None)
        # The refined assignment, not the original: supervision fills in a
        # pane id the enrollment did not know, and wait-report may have been
        # given that one (#403).
        report, aliases = None, ()
        if member is not None:
            expected = supervision.expected_assignment(member)
            report = expected["report"]
            aliases = (expected["pane_id"], member["assignment"]["pane_id"])
        result = recovery.record_refusal(store, data, at, agents_by_name[dispatch["agent"]].kind if dispatch else None,
                                         report, aliases=aliases)
    elif args.command == "recover-report":
        result = report_delivery.recover(store, history, data, at)
    elif args.command == "assess-specialist":
        result = engagement.record_assessment(state, state_path, data, at)
    elif args.command == "import-correction":
        result = historical.import_attempt(store, history, data, at)
        if result["assignment_index"] == len(history):
            add_assignment(state, data["occurred_at"], "developer", data["agent"], task=data["task"], fix_round=data["fix_round"])
    elif args.command == "record-historical-review":
        if isinstance(data, dict):
            attempt = next((item for item in store["historical_attempts"] if item["id"] == data.get("historical_attempt")), None)
            if attempt is not None:
                _require_independent_report(state, attempt["task"], data.get("reviewer"))
        result = historical.record_review(store, data, at)
    else:
        agents = {agent.name: agent for agent in load_config(_config_path(args))}
        name = recovery.recovery_agent(store, history, args.command, data)
        if name not in agents:
            raise UsageError("The recorded worker is absent from config; restore its original identity before recovering.", {})
        client = client if client is not None else _client(args, trace=trace)
        live = client.agent_get(name)
        recovery.require_recovery_ready(live)
        if args.command == "recover-context":
            result = recovery.authorize_context(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "recover-role-clear":
            result = role_clear.record_role_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "record-release-clear":
            result = historical.record_release_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        else:
            result = recovery.reconcile(store, history, data, at, live)
            if result["status"] == "applied" and (result.get("result") or {}).get("status") != "applied":
                context = result.get("context_before_send", {})
                recovered = {"role": result["role"], "agent": name, "task": result["task"],
                             "fix_round": result["fix_round"], "status": "applied", "at": at,
                             "cleared": context.get("cleared"), "clear_reason": context.get("clear_reason", "unknown"),
                             "context_session": None, "tier": context.get("tier"),
                             "recovered": True, "dispatch_id": result["id"]}
                if result.get("requirements") is not None:
                    recovered["requirements"] = result["requirements"]
                if result.get("reviewer_scope") is not None:
                    recovered["reviewer_scope"] = result["reviewer_scope"]
                if canonical_role(recovered["role"]) == "judge":
                    # The mode travels in the pre-send context and on the dispatch
                    # itself; `unknown` is only for a receipt older than both.
                    recovered["judge_mode"] = context.get("judge_mode") or result.get("judge_mode") or "unknown"
                add_assignment(
                    state, at, recovered["role"], name, status="applied",
                    cleared=recovered["cleared"], clear_reason=recovered["clear_reason"],
                    task=recovered["task"], fix_round=recovered["fix_round"],
                    context_session=recovered["context_session"], tier=recovered["tier"],
                    requirements=recovered.get("requirements"), reviewer_scope=recovered.get("reviewer_scope"),
                    judge_mode=recovered.get("judge_mode"),
                )
                # A dispatch recorded before the mode existed keeps its version-1
                # result: the ledger row says `unknown`, the dispatch says nothing.
                saved = {key: value for key, value in recovered.items()
                         if key != "judge_mode" or "judge_mode" in result}
                recovery.finish_dispatch(store, result["id"], saved, len(history) - 1, at)
    recovery.validate_store(store, history)
    engagement.validate_assessments(state)
    save_state(state_path, state)
    return result, None


def cmd_start_judge(args, client=None, warn=None, trace=None):
    document = _load_assignments(args.assignments, document=True)
    tier = document.get("judge") if isinstance(document, dict) else None
    if not isinstance(tier, dict) or not isinstance(tier.get("agent"), str) or not tier["agent"].strip():
        raise UsageError("Plan has no usable judge tier; run plan --roles judge.", {})
    if normalize_assignments(document).get("judge") != tier["agent"]:
        raise UsageError("Plan judge tier and assignment name different workers; replan.", {})
    # The plan carries the mode the lead declared; the flag overrides it, and
    # neither present is a refusal rather than a default (#425).
    judge_mode = recovery.require_judge_mode(_judge_mode_for(args, document))
    parsed = parse_tiers({"build": {"model": tier.get("model"), "effort": tier.get("effort")}}, args.kind)["build"]
    agent = SimpleNamespace(name=tier["agent"], kind=args.kind, idle_markers=(), working_markers=(),
                            launch_args=parse_launch_args(tier.get("launch_args", []), args.kind))
    client = client if client is not None else _client(args, trace=trace)
    state_path = _state_path(args)
    state = retrospective_runtime.read_history(state_path)
    planned_task = (document.get("task_context") or {}).get("task")
    if args.task is not None and planned_task is not None and args.task != planned_task:
        raise UsageError("Judge --task differs from its plan; use the original task identity.", {})
    at = args.now or now_iso()
    attention.require_dispatch_clear(state_path, args.task or planned_task, at)
    # The judge rules on the investigator's assessment, so the seat is never
    # started at an exhausted allowance before that assessment exists (#408).
    full = _load_state_for_write(state_path, warn, persist_migration=False)
    recovery.require_investigation_before_judge(full["recovery"], full["assignments"],
                                                args.task or planned_task, full["specialist_assessments"],
                                                mode=judge_mode)
    item = retrospective_runtime.request({"transitions": [{"agent": agent.name, "role": "judge",
        "model": parsed["model"], "effort": parsed["effort"], "context": "start", "task": args.task or planned_task,
        "pane": args.pane}]})["transitions"][0]
    guard = retrospective_runtime.Guard(state_path, state, client, {agent.name: agent}, at)
    proof = start_worker(client, agent, args.pane, parsed, before_start=lambda: guard.before_start(item))
    guard.after_transition({"agent": agent.name}, launch_proof=verify_running(client, agent, args.pane, parsed))
    return {"agent": agent.name, "model": parsed["model"], "effort": parsed["effort"],
            "pane": args.pane, "argv_verified": True, "verified": proof}, None


def cmd_capability(args, client=None, warn=None, trace=None):
    """The capability table's cadence, its refresh, and a read of what it holds.

    `capability-check` is read-only and answers one question: is the table due.
    A table never refreshed comes due as soon as the ledger holds any work, and
    stays quiet on a fleet that has dispatched nothing (#481).
    """
    path = _state_path(args)
    document = capabilities.load(path)
    if args.command == "capability-show":
        return document, None
    at = args.now or now_iso()
    if args.command == "capability-check":
        state, usable = load_state_checked(path, warn=warn, persist_migration=False)
        if not usable:
            # An unreadable ledger is not an empty one: whether work exists, and
            # so whether a refresh is due, is unknown.
            raise StateError("The ledger at {} exists but cannot be read, so whether the capability "
                             "table is due is unknown. Repair or migrate it with `teamlead state`, "
                             "then re-run capability-check.".format(path), {"path": str(path)})
        result = capabilities.cadence(document, at, existing_work=bool(state["assignments"]))
        return {"schema_version": capabilities.SCHEMA_VERSION, **result,
                "entries": len(document["entries"])}, None
    refreshed = capabilities.record(path, _read_record(args.record), at)
    return {"schema_version": capabilities.SCHEMA_VERSION,
            "refreshed_at": refreshed["refreshed_at"],
            "entries": len(refreshed["entries"])}, None


def cmd_retrospective(args, client=None, warn=None, trace=None):
    path = _state_path(args)
    if args.command == "retro-list":
        return retrospective.list_notes(path, task=args.task, since=args.since), None
    if args.command == "retro-show":
        return retrospective.show(path, args.id, task=args.task), None
    at = args.now or now_iso()
    data = _read_record(args.record)
    saved = None
    if args.command == "retro-record":
        if not isinstance(data, dict) or not isinstance(data.get("check"), str) or not Path(data["check"]).is_absolute():
            raise UsageError("retro-record requires an absolute check receipt path in its metadata.", {})
        saved = _read_record(data["check"])
        if (not isinstance(saved, dict) or saved.get("schema_version") != retrospective.SCHEMA_VERSION
                or saved.get("state_path") != str(retrospective.canonical_state(path))):
            raise UsageError("Retrospective check receipt belongs to another state or schema; rerun retro-check for this --state.", {})
        retrospective.validate_coverage(saved.get("coverage"))
        value = saved.get("request")
    else:
        value = data
    normalized = retrospective_runtime.request(value)
    state = retrospective_runtime.read_history(path)
    agents = {}
    if normalized["transitions"]:
        agents = {agent.name: agent for agent in load_config(_config_path(args))}
        client = client if client is not None else _client(args, trace=trace)
    result = retrospective_runtime.check(path, state, client, agents, normalized, at, allow_pending=saved is not None)
    if saved is None:
        return result, None
    if saved["coverage"] != result["coverage"]:
        raise UsageError("Retrospective worker, report, assignment or target changed since its check; refresh only the affected coverage and the lead's synthesis before recording.", {})
    checked_at = retrospective.utc(saved.get("checked_at"))
    if checked_at > retrospective.utc(at) or retrospective.utc(data.get("period_end")) < checked_at:
        raise UsageError("Retrospective period must include its evidence checkpoint and cannot come from the future; refresh the note's actual interval.", {})
    covered_names = {row["agent"] for row in result["coverage"]}
    participants, unavailable = data.get("participants"), data.get("unavailable")
    if (not isinstance(participants, list) or any(not isinstance(name, str) for name in participants)
            or not isinstance(unavailable, dict) or not covered_names <= set(participants) | set(unavailable)):
        raise UsageError("Retrospective metadata must account for each checked worker as participating or explicitly unavailable.", {})
    for row in result["coverage"]:
        if not row["first_start"] and row["source"]["report"] is None and not row["source"]["unavailable"]:
            raise UsageError("Outgoing worker {} needs a report path or explicit unavailable reason in the check request; status alone is not retrospective evidence.".format(row["agent"]), {})
    tasks = data.get("tasks")
    actual_tasks = {task for row in result["coverage"] for task in (row["source"]["task"], row["target"]["task"]) if task is not None}
    if not isinstance(tasks, list) or any(not isinstance(task, str) for task in tasks) or not actual_tasks <= set(tasks):
        raise UsageError("Retrospective tasks must include its outgoing and proposed task identities.", {})
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list):
        raise UsageError("Retrospective triggers must list daily and/or transition.", {})
    if (result["cadence"]["due"] and "daily" not in triggers) or (any(row["transition_required"] for row in result["coverage"]) and "transition" not in triggers):
        raise UsageError("Retrospective triggers must include every due daily/transition checkpoint represented by this note.", {})
    return retrospective.record(path, data, result["coverage"], at), None


def cmd_detect_triggers(args, client=None, warn=None, trace=None):
    return triggers.run_command(args)


def cmd_validate_partition(args, client=None, warn=None, trace=None):
    return partition.run_command(args)


def cmd_verify_oracle(args, client=None, warn=None, trace=None):
    return oracle.run_command(args)


def cmd_probe_report(args, client=None, warn=None, trace=None):
    if not Path(args.report).is_absolute() or any(ord(char) < 32 for char in args.report) or args.lines < 1:
        raise UsageError("Report probing needs an absolute one-row report path and positive --lines.", {})
    client = client if client is not None else _client(args, trace=trace)
    return report_delivery.probe(client, args.agent, args.pane, args.report, sys.stdin.read().rstrip("\n"), args.lines), None


def cmd_memory(args, client=None, warn=None, trace=None):
    return memory.run_command(args, _state_path(args), args.now or now_iso()), None


def cmd_attention(args, client=None, warn=None, trace=None):
    return attention.run_command(args, _state_path(args), args.now or now_iso()), None


SUPERVISION_COMMANDS = frozenset("supervision-" + action for action in ("bind", "enroll", "ack", "resolve", "hold", "resume", "drain", "status", "watch"))


def cmd_supervision(args, client=None, warn=None, trace=None):
    return supervision_runtime.run_command(args, _state_path(args), args.now or now_iso(),
                                           client=client, clock=now_iso, sleeper=time.sleep), None


def cmd_supervision_gate(args, client=None, warn=None, trace=None):
    """Which pending supervision events need the lead; see supervision_gate.py."""
    return supervision_gate.pending(supervision.load(_state_path(args))), None


def cmd_restoration(args, client=None, warn=None, trace=None):
    client = client if client is not None else _client(args, trace=trace)
    return restoration.run_command(args, client, sleep=time.sleep), None


COMMANDS = {
    "measure": cmd_measure,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "state": cmd_state,
    "status": cmd_status,
    "foreman-queue": cmd_foreman_queue,
    "foreman-reset": cmd_foreman_reset,
    "foreman-reset-deliver": cmd_foreman_reset_deliver,
    "load-set": cmd_load_set,
    **{command: cmd_recovery for command in ("task", "checkpoint", "authorize-corrections", "authorize-approach", "recover-context", "recover-role-clear", "record-report", "record-refusal", "authorize-refused-dispatch", "diagnose", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report", "assess-specialist", "close-task")},
    "detect-triggers": cmd_detect_triggers,
    "validate-partition": cmd_validate_partition,
    "verify-oracle": cmd_verify_oracle,
    "start-judge": cmd_start_judge,
    "probe-report": cmd_probe_report,
    **{command: cmd_retrospective for command in ("retro-check", "retro-record", "retro-list", "retro-show")},
    **{command: cmd_capability for command in ("capability-check", "capability-record", "capability-show")},
    "supervision-gate": cmd_supervision_gate,
    **{command: cmd_memory for command in memory.COMMANDS},
    **{command: cmd_attention for command in attention.COMMANDS},
    **{command: cmd_supervision for command in SUPERVISION_COMMANDS},
    **{command: cmd_restoration for command in restoration.COMMANDS},
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
        # Commands that may migrate or write state share its canonical lock.
        # Dry runs, probes, and retrospective reads remain read-only.
        readonly = args.command in {"probe-report", "detect-triggers", "validate-partition", "verify-oracle", "retro-check", "retro-list", "retro-show", "capability-check", "capability-show", "supervision-gate", "load-set", "foreman-queue"} or getattr(args, "dry_run", False)
        # The deliverer starts while `foreman-reset` still holds the state lock;
        # it serializes on the reset record's own lock instead.
        separate_owner = args.command in memory.COMMANDS | attention.COMMANDS | SUPERVISION_COMMANDS | restoration.COMMANDS | {"foreman-reset-deliver"}
        lock = nullcontext() if readonly or separate_owner else state_lock(retrospective.canonical_state(_state_path(args)))
        with lock:
            retro_lock = retrospective.lock(_state_path(args)) if not readonly and args.command in {"apply", "start-judge", "retro-record"} else nullcontext()
            with retro_lock:
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
