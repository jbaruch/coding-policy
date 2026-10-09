"""`foreman` command line: argparse subcommands, JSON on stdout.

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
import shlex
import subprocess
import sys
from typing import NoReturn
import time
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import runnable
from . import __version__
from .assign import apply as apply_assignments
from .assign import APPLY_SCHEMA_VERSION, dry_run, freeze_decision, freeze_paths, read_frozen, native_context_session, normalize_assignments, resolve_paths, validate_context_mode, validate_fix_history
from . import model_unavailability, probe_recovery, renderable
from . import attention, capabilities, chronology, churn, composition, engagement, foreman_queue, foreman_reset, historical, home, lifecycle, load_set, members, memory, oracle, partition, recovery, report_delivery, report_gates, restoration, role_clear, retrospective, retrospective_runtime, supervision, supervision_gate, supervision_runtime, triggers
from .config import FOREMAN_CONFIG_VERSION, default_config_path, load_config, load_foreman, load_judge, load_role_costs, select_agents
from .errors import AgentBusyError, PlanError, StateError, ForemanError, HerdrError, UsageError, owner_recovery
from .herdr import (
    error_code,
    DEFAULT_MARKER_TIMEOUT_MS,
    DEFAULT_SETTLE_TIMEOUT_MS,
    HerdrClient,
    READY_STATES,
    format_argv,
    trace_enabled_in_env,
)
from .composer import COMPOSER_SETTLE_SEC, DEFAULT_START_TIMEOUT_MS, ensure_ready, startup_pending_error
from .tiers import SEAT_SEPARATOR, canonical_role, require_seatable
from .diagnostics import PREFIX as DIAGNOSTIC_PREFIX, stderr_warn
from .measure import (
    DEFAULT_MARKER_POLL_ATTEMPTS,
    DEFAULT_MARKER_POLL_INTERVAL_SEC,
    DEFAULT_READ_LINES,
    measure,
)
from .planner import plan as build_plan
from .planner import ASSIGNMENT_PLAN_SCHEMA_VERSION, headroom_of
from .tiers import (COORDINATION_ROUND, FOREMAN_ROLE, JUDGMENT_ROUNDS, MissingTierError,
                    parse_launch_args, parse_tiers, select_tier, worker_launch_args)
from . import cost_report, selection, successors, tier_routing
from .launch import configured_running_tier, restart_worker, start_foreman, start_worker, verify_foreman, verify_running, require_empty_shell
from .state import (
    add_assignment,
    add_snapshot,
    default_state_path,
    latest_snapshot,
    load_state,
    load_state_checked,
    role_counts,
    save_state,
    STATUS_MAINTENANCE,
    state_lock,
)

EPILOG = (
    "Config defaults to $XDG_CONFIG_HOME/foreman/config.json (~/.config/...); "
    "copy config.example.json there to get started. State defaults to "
    "$XDG_STATE_HOME/foreman/state.json (~/.local/state/...)."
)


def now_iso():
    """The wall clock, read here and nowhere else in the package."""
    return datetime.now(timezone.utc).isoformat()


#: Recovery subcommands whose help says more than the generic line (#625).
RECOVERY_HELP = {
    "assess-specialist": "Record a delivered reviewer, tester or consultation report's contract lines "
                         "(VERDICT, ACCEPTANCE, CONTRIBUTION); a gap records only a declared design or implementation contribution.",
    "record-report": "Record a reviewer's verdict on a developer dispatch; the report's own VERDICT line must match it.",
}


def build_parser():
    # SUPPRESS keeps a subparser's copy of these flags from clobbering a value
    # given before the subcommand, so `foreman --state F plan` and
    # `foreman plan --state F` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="Agent config JSON (default: $XDG_CONFIG_HOME/foreman/config.json).",
    )
    common.add_argument(
        "--state",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="State file (default: $XDG_STATE_HOME/foreman/state.json).",
    )
    common.add_argument(
        "--herdr-bin",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="herdr executable to invoke (default: $FOREMAN_HERDR_BIN or `herdr`).",
    )
    common.add_argument(
        "--trace",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print every herdr command and its raw stdout, stderr, and exit "
        "status to stderr. Same as FOREMAN_TRACE=1.",
    )

    parser = argparse.ArgumentParser(
        prog="foreman",
        parents=[common],
        description="Load-balance coding agents running inside Herdr.",
        epilog=EPILOG,
    )
    parser.add_argument("--version", action="version", version="foreman " + __version__)

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

    relaunch = sub.add_parser("relaunch-worker", parents=[common],
                              help="Relaunch one idle worker on its currently configured argv, outside a dispatch.")
    relaunch.add_argument("name")
    relaunch.add_argument("--now", metavar="ISO")

    start_foreman = sub.add_parser("start-foreman", parents=[common],
                                   help="Start the configured foreman seat in a shell pane and verify its launch argv.")
    start_foreman.add_argument("--pane", required=True)
    verify_foreman = sub.add_parser("verify-foreman", parents=[common],
                                    help="Prove the live foreman pane runs the configured foreman tier. Read-only.")
    verify_foreman.add_argument("--pane", help="The foreman's pane (default: this Herdr pane, $HERDR_PANE_ID).")
    verify_foreman.add_argument("--config-only", action="store_true",
                                help="Report only whether config declares a foreman block; no tier selection, no pane probe.")

    for command in ("retro-check", "retro-record"):
        retro_parser = sub.add_parser(command, parents=[common], help="Check or record a foreman-authored retrospective.")
        retro_parser.add_argument("--record", required=True, metavar="FILE")
        retro_parser.add_argument("--now", metavar="ISO")
    capability_check = sub.add_parser("capability-check", parents=[common],
                                      help="Whether the model-capability table is due a refresh. Read-only.")
    capability_check.add_argument("--now", metavar="ISO")
    capability_record = sub.add_parser("capability-record", parents=[common],
                                       help="Record a capability consultation's report into the table.")
    capability_record.add_argument("--record", required=True, metavar="FILE")
    capability_record.add_argument("--now", metavar="ISO")
    successor_record = sub.add_parser("capability-successor", parents=[common],
                                      help="Bind a verified successor to its predecessor's authorized operating spot.")
    successor_record.add_argument("--record", required=True, metavar="FILE")
    successor_record.add_argument("--now", metavar="ISO")
    capability_show = sub.add_parser("capability-show", parents=[common],
                                     help="Read the saved capability table without contacting Herdr.")
    sub.add_parser("capability-migrate", parents=[common],
                   help="Owner upgrade of an older capability table, preserving historical evidence.")
    sub.add_parser("supervision-gate", parents=[common],
                   help="Which pending supervision events need the foreman. Read-only.")

    retro_list = sub.add_parser("retro-list", parents=[common], help="List saved retrospective notes without contacting Herdr.")
    retro_list.add_argument("--task")
    retro_list.add_argument("--since", metavar="ISO")
    retro_show = sub.add_parser("retro-show", parents=[common], help="Read a saved retrospective without contacting Herdr.")
    retro_show.add_argument("--task")
    retro_show.add_argument("--id", default="latest")
    memory.register_commands(sub, common)
    attention.register_commands(sub, common)
    report_gates.register_commands(sub, common)
    supervision_runtime.register_commands(sub, common)
    restoration.register_commands(sub, common)
    partition.register_commands(sub, common)
    oracle.register_commands(sub, common)
    ruling_parser = sub.add_parser("verify-ruling", parents=[common],
                                   help="Confirm a weighing ruling file is the report supervision enrolled for the pinned judge's adjudication on a task. Read-only.")
    ruling_parser.add_argument("--task", required=True)
    ruling_parser.add_argument("--ruling", required=True, metavar="FILE")

    triggers.register_command(sub, common)
    churn.register_command(sub, common)

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
        help="Let foreman clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
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
        help="`foreman plan` output, a {role: agent} object, or a path to either.",
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
            "For a pane the foreman already cleared by hand; the record carries "
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
        help="Let foreman clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
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
    unavailable_parser = sub.add_parser("probe-unavailable", parents=[common], help="Read-only confirmation of a terminal native model/account error, with visible rows on stdin.")
    unavailable_parser.add_argument("--agent", required=True)
    unavailable_parser.add_argument("--pane", required=True)
    unavailable_parser.add_argument("--report", required=True)
    unavailable_parser.add_argument("--lines", type=int, required=True)
    fit_parser = sub.add_parser("marker-fit", parents=[common], help="Measure a worker's live pane against its `REPORT: <path>` row, for a sender outside apply.")
    fit_parser.add_argument("--agent", required=True)
    fit_parser.add_argument("--report", required=True)
    resolve_probe_parser = sub.add_parser("resolve-probe", parents=[common], help="Prove and close an owner-retained disposable startup or cleanup probe after its native surface resolves.")
    resolve_probe_parser.add_argument("--agent", required=True, help="Exact retained probe name from the normal measure receipt.")

    for command in ("task", "checkpoint", "authorize-corrections", "authorize-approach", "recover-context", "recover-role-clear", "record-report", "record-refusal", "authorize-refused-dispatch", "diagnose", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report", "assess-specialist", "close-task"):
        record_parser = sub.add_parser(command, parents=[common], help=RECOVERY_HELP.get(command, "Record owner-managed {} evidence.".format(command)))
        if command == "reconcile":
            source = record_parser.add_mutually_exclusive_group(required=True)
            source.add_argument("--record", metavar="FILE", help="Actual transport evidence JSON; see dispatch-recovery.md.")
            source.add_argument("--dispatch", metavar="ID", help="Replay only the owner's recorded authoritative not_sent cleanup; never infer a transport outcome.")
        else:
            record_parser.add_argument("--record", required=True, metavar="FILE", help="Structured evidence JSON; see dispatch-recovery.md.")
        record_parser.add_argument("--now", metavar="ISO8601")
    sub.add_parser("status", parents=[common], help="Show implementation budgets and paused work separately from active audit workers.")
    reset_parser = sub.add_parser("foreman-reset", parents=[common], help="Schedule the foreman's round-boundary context reset from a reset-ready stow.")
    reset_parser.add_argument("--stow", default="latest", help="Stow id the reset resumes from (default: the latest).")
    reset_parser.add_argument("--now", metavar="ISO8601")
    deliver_parser = sub.add_parser("foreman-reset-deliver", parents=[common], help="Internal: wait for the foreman pane to idle, then clear it and send the resume prompt.")
    deliver_parser.add_argument("--pane", required=True)
    deliver_parser.add_argument("--stow", required=True)
    deliver_parser.add_argument("--startup-fd", type=int, help=argparse.SUPPRESS)
    reconcile_parser = sub.add_parser("foreman-reset-reconcile", parents=[common], help="Close a reset whose deliverer stopped without an outcome, as delivered or failed.")
    reconcile_parser.add_argument("--pane", required=True)
    reconcile_parser.add_argument("--stow", required=True)
    reconcile_parser.add_argument("--outcome", required=True, choices=["delivered", "failed"])
    close_member = sub.add_parser("close-member", parents=[common], help="Acknowledge an enrollment's pending events and resolve it, once the task ledger records its assessed outcome; an `accepted` reviewer, tester or consultation outcome needs its report's recorded contract lines.")
    close_member.add_argument("--enrollment", required=True)
    close_member.add_argument("--ledger", required=True, help="Absolute path of the task's TASK-LEDGER.md.")
    close_member.add_argument("--now", metavar="ISO8601")
    check_member = sub.add_parser("check-member", parents=[common], help="Run wait-report.sh --once for an enrollment, with its base and send time read from the owner records.")
    check_member.add_argument("--enrollment", required=True)
    check_member.add_argument("--worktree", help="The worker's own checkout, when it has one.")
    sub.add_parser("migrate-home", parents=[common], help="Move the state and config homes from teamlead to foreman, once per machine, with every foreman stopped.")
    sub.add_parser("foreman-queue", parents=[common], help="List open tasks waiting for their next seat, oldest first, derived from the owner records.")
    cost_parser = sub.add_parser("cost-report", parents=[common],
                                 help="Report each task's resource use through acceptance from the owner records. Read-only.")
    cost_parser.add_argument("--task", help="Report this task alone.")
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
                or not renderable.renderable(path)):
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


def _retryable_enrollment_identity(state_path, store, identifier, fingerprint):
    """Give each supervised retry after a cleaned pre-send failure a fresh identity.

    Recovery keeps an unsent dispatch retryable under its original identity,
    while supervision correctly keeps the closed pane's enrollment resolved.
    Reusing that identity would either reactivate accepted history or bind the
    replacement worker to the closed pane. Walk the deterministic retry names
    instead: an existing applied/pending attempt is replayed/reconciled, and
    only a resolved ``not_sent`` attempt advances to the next name.
    """
    members_by_id = {row["id"]: row for row in supervision.load(state_path)["members"]}
    base = identifier
    attempt = 0
    while True:
        dispatch = next((row for row in store["dispatches"] if row["id"] == identifier), None)
        if (dispatch is None or dispatch.get("fingerprint") != fingerprint
                or dispatch.get("status") != "not_sent"):
            return identifier
        member = members_by_id.get(identifier)
        if member is None or member["active"]:
            return identifier
        attempt += 1
        identifier = "{}:transport-retry-{}".format(base, attempt)


def _closed_no_send_retry(state_path, state, client, dispatch, tier, paths, *, recorded=None):
    """Prove an immutable, closed no-brief attempt without changing history."""
    name = dispatch["agent"]
    def refuse(kind, message, operation="reconcile", **evidence):
        return owner_recovery(UsageError(message, {"agent": name}), kind,
            runnable.command(operation + " --state " + shlex.quote(str(state_path))
                + (" --dispatch " + shlex.quote(row["id"]) if operation == "reconcile" else "")),
            "The owner must establish unchanged authoritative not_sent evidence and closure of the recorded identity before normal apply may retry; preserve prior work and never invent config or receipts.",
            agent=name, dispatch=row["id"], **evidence)
    previous = [row for row in state["recovery"]["dispatches"] if row.get("agent") == name]
    if not previous:
        return None
    row = previous[-1] if recorded is None else recorded
    if recorded is not None and row not in previous:
        raise StateError("Closed no-input history is not in the owner ledger; preserve it and inspect `{}`.".format(
            runnable.command("supervision-status")), {})
    members = supervision.load(state_path)["members"]
    member = next((entry for entry in members if entry["id"] == row["id"]), None)
    saved_tier = (row.get("context_before_send") or {}).get("tier") or (row.get("observed_before") or {}).get("tier")
    same = (row.get("status") == "not_sent" and row.get("fingerprint") == dispatch["fingerprint"]
            and all(row.get(key) == dispatch.get(key) for key in
                    ("task", "role", "agent", "fix_round", "brief_identity", "judge_mode", "provider", "worker_kind"))
            and isinstance(saved_tier, dict) and all(saved_tier.get(key) == value for key, value in tier.items())
            and row.get("result") is None and row.get("report") is None
            and not any(item.get("agent") == name for item in state["assignments"])
            and member is not None and member["active"] is False
            and not any(entry["active"] and entry["assignment"]["agent"] == name for entry in members))
    if not same:
        raise refuse("retry_inputs_unproved", "Fresh retry cannot prove identical closed no-send inputs; preserve the previous attempt and its work.")
    assignment = supervision.expected_assignment(member)
    pane, report = assignment.get("pane_id"), assignment.get("report")
    if not pane or not report or Path(report).exists() or Path(report).is_symlink():
        raise refuse("retry_closure_unproved", "Fresh retry has report evidence or no recorded closure surface; preserve the prior attempt.")
    reconciled = row.get("reconciliation")
    if reconciled is not None:
        if reconciled.get("input", {}).get("outcome") != "not_sent":
            raise refuse("retry_transport_unproved", "Fresh retry lacks an authoritative not_sent reconciliation.")
        try:
            receipt, _body = recovery.receipt(reconciled["input"]["evidence"])
        except ForemanError as exc:
            raise refuse("retry_evidence_unavailable", "Fresh retry cannot read its authoritative reconciliation evidence.", cause=exc.to_dict()) from exc
        if receipt != reconciled.get("evidence_receipt"):
            raise refuse("retry_evidence_changed", "Fresh retry reconciliation evidence changed; preserve the prior attempt.")
    elif not any(event.get("kind") == "dispatch_not_sent" and event.get("details", {}).get("dispatch") == row["id"]
                 for event in state["recovery"]["events"]):
        raise refuse("retry_transport_unproved", "Fresh retry lacks the owner's pre-send abort evidence.")
    for read, identifier, absent in ((client.agent_get, name, "agent_not_found"), (client.pane_get, pane, "pane_not_found")):
        try:
            read(identifier)
        except HerdrError as exc:
            if error_code(exc) != absent:
                raise owner_recovery(exc, "retry_identity_unavailable", runnable.command("supervision-status"),
                    "The owner must observe the recorded identity and prove absence before retrying unchanged apply.", agent=name, pane_id=pane, dispatch=row["id"])
        else:
            raise refuse("retry_identity_live", "Fresh retry's old agent or pane is still live; nothing was started.", "supervision-status", pane_id=pane)
    item = {"role": dispatch["role"], "task": dispatch["task"], "model": tier.get("model"),
            "effort": tier.get("effort"), "context": "start", "brief": paths[dispatch["role"]], "common": paths["common"]}
    return {"classification": "reconciled_not_sent", "dispatch": row["id"],
            "pane_id": pane, "target": retrospective_runtime.target(item)}


def _historical_no_send_proof(state_path, state, client, transition):
    """Prove the exact old attempt blocking a different task's index read.

    The caller holds both owner locks. A validated transition supplies only
    the lookup key, never transport authority. Its original enrollment and
    dispatch must independently prove unchanged frozen bytes, no work and
    actual absence of that old pane before retrospective repair can use it.
    """
    members = [member for member in supervision.load(state_path)["members"]
               if member["assignment"]["agent"] == transition["agent"]
               and supervision.expected_assignment(member).get("pane_id") == transition["incoming"]["pane_id"]]
    if len(members) != 1:
        return None
    assignment = supervision.expected_assignment(members[0])
    row = next((entry for entry in state["recovery"]["dispatches"]
                if entry["id"] == members[0]["id"] and entry["agent"] == transition["agent"]), None)
    if row is None or assignment["task"] != row["task"]:
        return None
    saved_tier = (row.get("context_before_send") or {}).get("tier") or (row.get("observed_before") or {}).get("tier")
    if not isinstance(saved_tier, dict) or not row.get("brief") or not row.get("common"):
        return None
    paths = {row["role"]: row["brief"], "common": row["common"]}
    if recovery.brief_identity(paths, row["role"], assignment.get("report")) != row.get("brief_identity"):
        return None
    tier = {key: saved_tier.get(key) for key in ("model", "effort")}
    return _closed_no_send_retry(state_path, state, client, row, tier, paths, recorded=row)


def _recorded_no_send_cleanup(state_path, state, identifier):
    """Select existing owner proof, without creating a reconciliation receipt."""
    row = next((item for item in state["recovery"]["dispatches"] if item["id"] == identifier), None)
    def blocked(kind, message):
        return owner_recovery(UsageError(message, {}), kind,
            runnable.command("supervision-status --state " + shlex.quote(str(state_path))),
            "The owner must establish actual transport evidence and the recorded empty identity before reconciliation can close it; preserve unknown or completed work.", dispatch=identifier)
    if (row is None or row["status"] != "not_sent" or not isinstance(row.get("worker_kind"), str)
            or row.get("result") is not None or row.get("report") is not None
            or any(item.get("agent") == row["agent"] for item in state["assignments"])):
        raise blocked("retry_transport_unproved", "No authoritative assignment-scoped no-send outcome is available.")
    member = next((item for item in supervision.load(state_path)["members"] if item["id"] == identifier), None)
    if member is None:
        raise blocked("retry_closure_unproved", "No original enrollment is available for no-send cleanup.")
    assignment = supervision.expected_assignment(member)
    report = assignment.get("report")
    if (assignment["agent"] != row["agent"] or assignment["task"] != row["task"]
            or not assignment.get("pane_id") or not report or Path(report).exists() or Path(report).is_symlink()):
        raise blocked("retry_closure_unproved", "The original closure identity is missing, changed or has report work.")
    reconciled = row.get("reconciliation")
    if reconciled is not None:
        record = reconciled["input"]
        if record.get("outcome") != "not_sent":
            raise blocked("retry_transport_unproved", "Saved reconciliation does not prove no-send.")
        try:
            evidence, _ = recovery.receipt(record["evidence"])
        except ForemanError as exc:
            raise blocked("retry_evidence_unavailable", "Saved reconciliation evidence is unavailable.") from exc
        if evidence != reconciled["evidence_receipt"]:
            raise blocked("retry_evidence_changed", "Saved reconciliation evidence changed.")
    else:
        abort = next((event for event in state["recovery"]["events"]
            if event["kind"] == "dispatch_not_sent" and event.get("task") == row["task"]
            and event.get("details", {}).get("dispatch") == identifier), None)
        if abort is None:
            raise blocked("retry_transport_unproved", "The owner's pre-send abort evidence is unavailable.")
        record = {"reason": abort["details"]["reason"], "evidence": str(state_path)}
    return row, record, assignment


def _cleanup_reconciled_scoped_not_sent(state_path, dispatch, record, at, client, before_close=None):
    """Close and resolve a scoped send proven not to have reached its worker.

    The caller saves the ``not_sent`` reconciliation before entering here. A
    pane or sidecar failure therefore cannot erase the terminal transport fact;
    replaying the identical reconciliation retries only this cleanup.
    """
    member = next((row for row in supervision.load(state_path)["members"]
                   if row["id"] == dispatch["id"]), None)
    if member is None:
        raise StateError(
            "Dispatch {} is durably not_sent, but its assignment-scoped supervision enrollment is missing. Restore the owner sidecar, then replay the identical reconciliation to close its pane.".format(
                dispatch["id"]),
            {"dispatch": dispatch["id"]},
        )
    assignment = supervision.expected_assignment(member)
    pane = assignment.get("pane_id")
    if not isinstance(pane, str) or not pane:
        raise StateError(
            "Dispatch {} is durably not_sent, but its assignment-scoped enrollment records no pane. Restore the original pane identity, then replay the identical reconciliation.".format(
                dispatch["id"]),
            {"dispatch": dispatch["id"]},
        )
    try:
        closure = lifecycle.close(client, assignment["agent"], pane,
                                  **({"before_close": before_close} if before_close is not None else {}))
        # An operator may resolve supervision independently; inactive is not
        # evidence that the assignment pane was closed. Always prove closure,
        # and skip only the already-recorded sidecar transition.
        if member["active"]:
            supervision.resolve(
                state_path,
                {"id": dispatch["id"],
                 "outcome": "Reconciled not_sent: " + record["reason"],
                 "evidence": [str(Path(record["evidence"]).expanduser().resolve())]},
                at,
            )
        return closure
    except ForemanError as cleanup:
        raise HerdrError(
            "Dispatch {} is durably not_sent, but assignment-scoped pane/enrollment cleanup failed: {} Replay the identical reconciliation after repairing the cleanup failure; its transport outcome will not be rewritten.".format(
                dispatch["id"], cleanup),
            {"dispatch": dispatch["id"], "cleanup_error": cleanup.to_dict()},
        ) from cleanup


def _seat_holds(roles, task, state, state_path):
    """Developer reservations and busy workers, read from the owner records (#483)."""
    busy = {row["assignment"]["agent"]: row["assignment"]["task"]
            for row in supervision.load(state_path)["members"] if row["active"]}
    return composition.seat_holds(roles, task, recovery.developer_reservations(state["recovery"], state["assignments"]), busy)


def _parse_excludes(pairs):
    """`--exclude ROLE=AGENT[,AGENT...]` into `{role: [agent, ...]}`.

    Repeats of the same role merge rather than replace, so a foreman can bar the
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
                "--assignments file {} not found - pass the path to `{}` "
                "output, or the JSON object itself.".format(path, runnable.command("plan")),
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
            "`{}` output or a {{\"role\": \"agent\"}} object.".format(
                exc.msg, exc.lineno, exc.colno, runnable.command("plan")
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


def _planned_snapshot(document, state, state_path):
    """The snapshot a plan names, or None when it cannot be found.

    An unlocatable snapshot reads as unmeasured. A plan that de-escalated on
    it then recomputes without the de-escalation and is refused as stale,
    which is the outcome an unverifiable pressure claim should have.
    """
    ref = document.get("snapshot_ref") if isinstance(document, dict) else None
    if not isinstance(ref, dict) or not isinstance(ref.get("source"), str):
        return None
    measured_at = ref.get("measured_at")
    if ref["source"] == str(state_path):
        matches = [snap for snap in state.get("snapshots", [])
                   if isinstance(snap, dict) and snap.get("measured_at") == measured_at]
        return matches[-1] if matches else None
    try:
        snapshot = json.loads(Path(ref["source"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict) or snapshot.get("measured_at") != measured_at:
        return None
    return snapshot


def _snapshot_groups(snapshot):
    """Account/window identity from measurement, never inferred from a percentage."""
    agents = snapshot.get("agents") if isinstance(snapshot, dict) else None
    if not isinstance(agents, dict):
        return {}
    return {name: record.get("window_group") for name, record in agents.items()
            if isinstance(record, dict)}


def _build_plan_with_refusals(build, refusals, *args, **kwargs):
    """Plan, naming every capability refusal when no candidate is left to plan."""
    try:
        return build(*args, **kwargs)
    except PlanError as exc:
        if not refusals:
            raise
        raise PlanError("{} Capability refusals: {}".format(exc.message, " ".join(
            "{} for {}: {}".format(row["agent"], row["role"], row["message"]) for row in refusals)),
            {**exc.details, "capability_refusals": refusals}) from None


#: Tier fields a plan carries to explain itself and a dispatch never records.
PLAN_ONLY_TIER_FIELDS = frozenset({"capability", "cheaper_adequate", "routing"})



def _candidate_tiers(roles, agents, rounds, fix_round=None, judge=None, excludes=None, headroom=None, table=None, refusals=None,
                     reusable_agents=False, measured_at=None, at=None, capacity_groups=None, worker_kinds=None,
                     unavailable_store=None):
    """Each role's candidate tiers; `refusals` collects a capability refusal per skipped candidate."""
    table = table if table is not None else capabilities.empty()
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
                    # The pinned judge has no substitute, so an inadequate pin refuses the plan.
                    model_unavailability.require_available(unavailable_store, agent, judge.model, at,
                                                           (worker_kinds or {}).get(agent.name))
                    verdict = capabilities.assess(table, judge.model, judge.effort or None,
                                                  capabilities.required("judge", "judge", JUDGMENT_ROUNDS))
                    candidates[role][agent.name] = {"round": "judge", "tier_row": "judge", "kind": agent.kind,
                        "model": judge.model, "effort": judge.effort or None,
                        "billing_window": "unknown", "multiplier": 1.0, "effective_multiplier": 1.0,
                        # A judgment round never meets pressure, but every
                        # schema-9 tier entry carries both fields (#490).
                        "pressure_headroom": None, "de_escalated": False,
                        "capability": verdict, "cheaper_adequate": None}
                    continue
                if not reusable_agents:
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
            needs = capabilities.required(canonical_role(role), tier["round"], JUDGMENT_ROUNDS)
            try:
                tier, routing = tier_routing.decide(agent, role, tier, needs, table,
                                                   (headroom or {}).get(agent.name), measured_at, at,
                                                   (capacity_groups or {}).get(agent.name),
                                                   (worker_kinds or {}).get(agent.name),
                                                   unavailable=lambda model: model_unavailability.exclusion(
                                                       unavailable_store, agent, model, at, (worker_kinds or {}).get(agent.name)))
                model_unavailability.require_available(unavailable_store, agent, tier["model"], at,
                                                       (worker_kinds or {}).get(agent.name))
                verdict = capabilities.assess(table, tier["model"], tier["effort"], needs)
            except UsageError as exc:
                if refusals is not None:
                    refusals.append({"role": role, "agent": agent.name, "message": exc.message, **exc.details})
                continue
            candidates[role][agent.name] = {key: tier[key] for key in (
                "round", "tier_row", "kind", "model", "effort", "multiplier", "billing_window",
                "effective_multiplier", "pressure_headroom", "de_escalated",
            )}
            candidates[role][agent.name].update(
                capability=verdict,
                cheaper_adequate=selection.cheaper_adequate(agent, role, tier, needs, table))
            if routing is not None:
                candidates[role][agent.name]["routing"] = routing
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
    measure_fn = lifecycle.measure_worker_kinds if agents and all(agent.assignment_scoped for agent in agents) else measure
    snapshot = measure_fn(
        client, agents, args.now or now_iso(), marker_timeout_ms=args.marker_timeout,
        read_lines=args.lines, warn=warn, poll_attempts=args.marker_poll_attempts,
        poll_interval_sec=args.marker_poll_interval, settle_sec=args.composer_settle,
        allow_recovery=args.allow_recovery,
        **({"state_path": _state_path(args), "config_path": _config_path(args)} if measure_fn is lifecycle.measure_worker_kinds else {}))
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


def cmd_resolve_probe(args, client=None, warn=None, trace=None):
    templates = load_config(_config_path(args))
    client = client if client is not None else _client(args, trace=trace)
    return probe_recovery.resolve(_state_path(args), args.agent, templates, client, config_path=_config_path(args)), None


def _judge_mode_for(args, document):
    """The judge seat's mode: the plan's, and a supplied one must agree.

    The plan records the choice the foreman made when it composed the brief. A
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

    Returns `(roles, {seat: role}, {seat: [glob, ...]}, proof)`. Without a
    partition the round is untouched, which is every single-seat round (#409).
    The proof comes from the same read as the slices, so the two cannot come
    from different versions of the file (#460).
    """
    if not partition_path:
        return roles, {}, {}, None
    document, proof = partition.load_validated(partition_path)
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
    return expanded, seats, partition.seat_paths(document, role), proof


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
    every name barred from its role and adds whatever the foreman barred from the
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
    if FOREMAN_ROLE in {canonical_role(role) for role in canonical}:
        raise UsageError(
            "The foreman seat is never planned onto a worker; its tier is selected and launched with `{}`.".format(
                runnable.command("start-foreman --pane <pane-id>")), {"roles": canonical})
    # `--roles` names RESPONSIBILITIES. A seat comes from `--partition` alone,
    # which is the declared surface split `validate-partition` checks disjoint
    # and exhaustive; accepting a pre-seated name here would plan seats against
    # no declared partition at all (#434).
    seated = [role for role in canonical if SEAT_SEPARATOR in role]
    if seated:
        raise UsageError(
            "--roles names responsibilities, not seats: {} came pre-seated. Pass {} and "
            "seat the slices with --partition, after `{}` has checked it "
            "disjoint and exhaustive over the round's change.".format(
                ", ".join(seated), ", ".join(sorted({canonical_role(role) for role in seated})),
                runnable.command("validate-partition")),
            {"roles": seated})
    roles, seats, seat_paths, proof = _expand_partition_seats(canonical, getattr(args, "partition", None))
    if "judge" in canonical:
        recovery.require_judge_mode(getattr(args, "judge_mode", None))
    excludes = _parse_excludes(args.excludes)
    role_costs = load_role_costs(_config_path(args))
    judge = load_judge(_config_path(args))
    rounds = _round_inputs(args, canonical)
    agents = load_config(_config_path(args)) if _config_path(args).exists() else []
    scoped = bool(agents) and all(agent.assignment_scoped for agent in agents)
    state_path = _state_path(args)
    state = load_state(state_path, warn=warn)
    requirements = composition.parse_requirements(_read_record(args.requirements) if args.requirements else None, canonical, args.task)
    work = _read_record(args.work) if args.work else None
    recovery.validate_work(state["recovery"], state["assignments"], args.task, args.fix_round,
                           args.correction_plan, work, implementation="developer" in canonical)
    if args.task:
        validate_fix_history({role: None for role in canonical}, state["assignments"], args.task, args.fix_round,
                             recovery=state["recovery"])
    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PlanError(
                "Snapshot file {} not found - run `{}` or point "
                "--snapshot at a saved snapshot.".format(snapshot_path, runnable.command("measure")),
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
        # Apply re-reads this path, possibly from another working directory; a
        # relative one would resolve elsewhere and read as unmeasured (#490).
        source = str(snapshot_path.absolute())
    else:
        snapshot = latest_snapshot(state)
        if snapshot is None:
            raise PlanError(
                "No snapshot in {} - run `{}` first, or pass "
                "--snapshot FILE.".format(state_path, runnable.command("measure")),
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
    capability_refusals = []
    table = capabilities.load(_state_path(args))
    tier_candidates = _candidate_tiers(canonical, agents, rounds, args.fix_round, judge,
                                      excludes={role: names for role, names in excludes.items() if role in set(canonical)},
                                      headroom=measured_headroom, table=table,
                                      refusals=capability_refusals, reusable_agents=scoped,
                                      measured_at=snapshot.get("measured_at"), at=args.now or now_iso(),
                                      capacity_groups=_snapshot_groups(snapshot), unavailable_store=state["recovery"])
    constraints = {**constraints, "rationale": constraints["rationale"] + [
        "{} was not considered for {}: {}".format(row["agent"], row["role"], row["message"]) for row in capability_refusals]}
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

    result = _build_plan_with_refusals(build_plan, capability_refusals,
            roles,
            snapshot,
            role_counts(state, _worker_kind_provenance(state) if scoped else None),
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
            reusable_agents=scoped,
        )
    result["task_context"] = ({"task": args.task, "fix_round": args.fix_round,
                               "plan": args.correction_plan, "work": work} if args.task else None)
    # Why each seat got its model and effort, from the same tiers, table and
    # round inputs the selection read; explanation only, never re-read at apply (#602).
    result["selection"] = selection.records(
        result["assignments"], result.get("tiers"), {agent.name: agent for agent in agents},
        requirements, rounds, args.fix_round, table)
    if scoped:
        # Planning ranks stable worker kinds. The saved plan then allocates a
        # fresh live identity per seat; retries reuse that identity, while a
        # new plan cannot accidentally discover and reuse an idle worker.
        worker_kinds = dict(result["assignments"])
        result["schema_version"] = ASSIGNMENT_PLAN_SCHEMA_VERSION
        result["worker_kinds"] = worker_kinds
        result["assignments"] = lifecycle.identities(result["assignments"])
        if isinstance(result.get("judge"), dict) and "judge" in result["assignments"]:
            result["judge"]["worker_kind"] = worker_kinds["judge"]
            result["judge"]["agent"] = result["assignments"]["judge"]
    # A patch or fixture oracle is a path; pin the bytes behind it now, so
    # `verify-oracle` checks the round against the file it was licensed on (#488).
    pins = oracle.pin_oracles(result.get("rounds"))
    if pins:
        result["oracle_pins"] = pins
    # The composer requires each seat's owned paths and reads no partition, so
    # the plan hands them over from the document `validate-partition` accepted.
    if seat_paths:
        accepted = {seat: paths for seat, paths in seat_paths.items() if seat in result["assignments"]}
        result["slice_paths"] = accepted
        # The digest travels with the map, into each brief and back at dispatch,
        # so an edit between the validated partition and the send is refused.
        result["slice_digest"] = partition.slice_digest(accepted, proof)
        # Per seat as well: one digest for the whole round is identical in
        # every brief, so swapping two seats' briefs would pass a check that
        # only asks whether a digest is present.
        result["seat_digests"] = {seat: partition.seat_digest(seat, paths, proof)
                                  for seat, paths in accepted.items()}
        # What the partition was proven against, for `verify-partition` at the gate (#460).
        result["partition_proof"] = proof
    return result, None


def _refusal_moves(store, agents_by_name, assignments, roles, args, paths, reports, contents=None):
    """Return the refusal move each fresh role carries; see recovery.refusal_move."""
    moves = {}
    for role in roles:
        name = assignments[role]
        if name in agents_by_name:
            move = recovery.refusal_move(store, args.task, role, args.fix_round, agents_by_name[name].kind,
                                         recovery.brief_identity(paths, role, reports.get(role), contents), reports.get(role))
            if move is not None:
                moves[role] = move
    return moves


def _require_bound_slices(document, seated, briefs, bodies=None, contents=None):
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
            "`{}` rather than hand-writing the "
            "assignments, so the boundary that ships is the one that was checked.".format(
                ", ".join(seated), runnable.command("plan --partition <validate-partition output>")),
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
            "globs; re-run `{}` rather than "
            "editing the assignments.".format(
                ", ".join(repr(seat) for seat in sorted(map(str, malformed))),
                runnable.command("plan --partition <validate-partition output>")),
            {"seats": [str(seat) for seat in malformed]})
    # A glob is rendered verbatim into the brief, so a backtick or a control
    # character closes the code span and appends instructions of its own.
    # `validate_document` and the composer both refuse these; a hand-written
    # plan reaches the renderer without passing either.
    unsafe = sorted(seat for seat, globs in slice_paths.items()
                    if any(partition.unsafe_glob(glob) for glob in globs))
    if unsafe:
        raise UsageError(
            "The plan's slice_paths gives {} a glob carrying a backtick or a control "
            "character, which the brief renders verbatim; re-run `{}` "
            "rather than editing the assignments.".format(
                ", ".join(repr(seat) for seat in unsafe), runnable.command("plan --partition <validate-partition output>")),
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
    proof = document.get("partition_proof")
    expected = partition.slice_digest(slice_paths, proof)
    if expected != recorded:
        raise UsageError(
            "The plan's slice_paths no longer match its slice_digest ({} vs {}); the "
            "boundary changed after planning. Re-run `{}` and `{}` rather "
            "than editing either.".format(expected, recorded, runnable.command("validate-partition"), runnable.command("plan")),
            {"expected": expected, "recorded": recorded})
    for role in seated:
        if role not in slice_paths:
            raise UsageError(
                "Seat {!r} is not in the plan's slice_paths, so its boundary was never "
                "checked; plan the round from the validated partition.".format(role),
                {"role": role})
        brief = briefs.get(role)
        try:
            # `bodies` carries text already read and verified from a frozen
            # copy; `contents` the bytes the dispatch's freeze verified (#565).
            body = (bodies[role] if bodies is not None
                    else recovery.briefing_bytes(brief, contents).decode("utf-8") if brief else "")
        except (OSError, UnicodeError) as exc:
            raise UsageError(
                "Cannot read the brief for seat {!r} at {}: {}. Restore a readable UTF-8 brief "
                "at that path, or regenerate the round's briefs with compose-briefs.sh, then "
                "re-run `{}`.".format(role, brief, exc, runnable.command("apply")),
                {"role": role}) from None
        # The whole scope block, not the facts it contains. A brief that
        # scatters the digest, the slice name and a path while directing a
        # whole-repository pass satisfies three substring checks and still
        # dispatches a full-surface verdict as a slice one; the block carries
        # its own restrictions, so requiring it requires those too.
        expected_seat = partition.seat_digest(role, slice_paths[role], proof)
        expected_scope = partition.slice_scope(role, slice_paths[role], expected_seat)
        if expected_scope not in body:
            raise UsageError(
                "The brief for seat {!r} does not carry this seat's scope block, so it "
                "was not composed against the boundary this plan checked. Compose it "
                "with `compose-briefs.sh` from the plan's slice_paths and seat_digests; "
                "the block it renders reads: {}".format(role, expected_scope),
                {"role": role, "expected_scope": expected_scope})


def cmd_apply(args, client=None, warn=None, trace=None):
    # A fresh release's verdict-gate check holds the report-gate lock until
    # apply returns, so no verdict gate is recorded between the check and the
    # send (#646; foreman/report_gates.py `holding`).
    with ExitStack() as stack:
        def hold_gates():
            stack.enter_context(report_gates.holding(_state_path(args)))
        return _apply(args, client, warn, trace, hold_gates)


def _apply(args, client, warn, trace, hold_gates):
    templates = load_config(_config_path(args))
    document = _load_assignments(args.assignments, document=True)
    assignments = normalize_assignments(document)
    scoped = bool(templates) and all(agent.assignment_scoped for agent in templates)
    worker_kinds = document.get("worker_kinds") if isinstance(document, dict) else None
    if scoped and (not isinstance(worker_kinds, dict)
                   or set(worker_kinds) != set(assignments)):
        raise UsageError(
            "This assignment-scoped plan needs a worker_kinds object with exactly one entry for every assigned seat; re-run `{}` rather than editing the plan.".format(
                runnable.command("plan")),
            {},
        )
    if scoped and not args.dry_run and not (isinstance(args.task, str) and args.task.strip()):
        raise UsageError(
            "Assignment-scoped apply needs --task before it can create short-lived panes; the task owns their durable dispatch and closure records.",
            {},
        )
    if not scoped and isinstance(document, dict) and "worker_kinds" in document:
        raise UsageError("This assignment-scoped plan needs a schema-7 worker_kinds config; restore it or replan.", {})
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
    judge_kind = document.get("worker_kinds", {}).get("judge") if scoped else assignments.get("judge")
    if "judge" in assignments and (judge is None or judge_kind != judge.agent):
        raise UsageError("Judge assignment must use the pinned judge worker kind in config.json.", {})
    if not scoped and judge and any(name == judge.agent and role != "judge" for role, name in assignments.items()):
        raise UsageError("The pinned judge worker cannot hold another role.", {})
    active_judge = (SimpleNamespace(agent=assignments["judge"], model=judge.model, effort=judge.effort)
                    if scoped and judge and "judge" in assignments else judge)
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn, persist_migration=not args.dry_run)
    store = state["recovery"]
    at = args.now or now_iso()
    work = _read_record(args.work) if args.work else None
    task_context = {"task": args.task, "fix_round": args.fix_round, "plan": args.correction_plan, "work": work}
    if document.get("task_context") is not None and document["task_context"] != task_context:
        raise UsageError("Saved plan and apply name different task, count or correction bounds; replan from the current ledger.", {})
    if scoped and (args.retain_context or args.retain_specialist):
        raise UsageError(
            "Assignment-scoped workers live for one assignment, so their panes cannot be retained. Re-run without --retain-context or --retain-specialist; the follow-up plan already names a fresh worker identity.",
            {},
        )
    if scoped:
        agents_by_name = lifecycle.materialize(assignments, document.get("worker_kinds"), templates)
        agents = list(agents_by_name.values())
    else:
        agents = templates
        agents_by_name = {agent.name: agent for agent in agents}
    paths = resolve_paths(assignments, _parse_briefs(args.briefs), args.common)
    reports = _parse_reports(args.reports, assignments)
    supervised = supervision.dispatch_binding(state_path) is not None
    if scoped and not args.dry_run and not supervised:
        raise UsageError(
            "Assignment-scoped live apply requires a bound foreman so every short-lived pane has a durable closure record. Run `{}` first.".format(
                runnable.command("supervision-bind")),
            {},
        )
    if requirements and not args.dry_run and not supervised:
        raise UsageError("Bind the foreman with `{}` before dispatching specialist requirements; every specialist needs durable observation ownership.".format(runnable.command("supervision-bind")), {})
    if supervised and (not args.task or set(reports) != set(assignments)):
        raise UsageError("Bound team rounds require --task and one --report ROLE=ABS_PATH for every assigned role before any worker input.", {})
    # The mode is part of what a judge dispatch IS: one brief sent as an
    # adjudication and as a diagnosis are two dispatches, so the mode is
    # resolved before any identity is computed (#478).
    judge_mode = None
    if any(canonical_role(role) == "judge" for role in assignments):
        judge_mode = recovery.require_judge_mode(
            _judge_mode_for(args, document if isinstance(document, dict) else None))

    # The oracle each mechanical round is licensed on, pin included, bound into
    # its dispatch: `verify-oracle` reads it back from the ledger, never from
    # the mutable plan alone (#585).
    oracles = oracle.dispatch_oracles(document, list(assignments)) if "assignments" in document else {}

    def options_for(role):
        options = {**task_context, "rounds": rounds, "retain_context": args.retain_context, "no_clear": args.no_clear}
        if requirements:
            options["requirements"] = requirements
        if args.retain_specialist:
            options["retain_specialist"] = True
        # The rounds already carry the oracle's kind, path or value; the pin is
        # the one input they lack. Absent on every other round, so their
        # identities are unchanged.
        if "sha256" in oracles.get(role, {}):
            options["oracle_pin"] = oracles[role]["sha256"]
        return options

    # The bytes each frozen copy was verified to hold, once the freeze below
    # runs: every later read of a frozen path takes these, never the path
    # again, so an ancestor swapped after the freeze changes nothing sent (#565).
    contents = None

    def identity(role, name, paths_now):
        """The dispatch identity these inputs resolve to: its id and fingerprint."""
        options = options_for(role)
        if canonical_role(role) == "judge":
            options["judge_mode"] = judge_mode
        identifier, fingerprint = recovery.dispatch_identity(
            args.task, role, name, args.fix_round, paths_now, args.dispatch_id, options=options, contents=contents)
        if supervised:
            # Keep legacy retry IDs, while new bound dispatch fingerprints
            # also bind the explicit report path. Existing legacy receipts
            # cannot retroactively prove a report input they never stored.
            old = next((row for row in store["dispatches"] if row["id"] == identifier), None)
            if old is None or old["fingerprint"] != fingerprint:
                fingerprint = supervision.report_bound_fingerprint(fingerprint, reports[role])
            identifier = _retryable_enrollment_identity(
                state_path, store, identifier, fingerprint)
        return identifier, fingerprint

    def legacy_judge_fingerprints(role, name, paths_now):
        """A judge dispatch recorded before the mode joined its identity carries the mode-less fingerprint.

        Re-running it after the upgrade must not read as new work and send the
        round twice (#478).
        """
        if canonical_role(role) != "judge":
            return set()
        _legacy_id, legacy = recovery.dispatch_identity(
            args.task, role, name, args.fix_round, paths_now, None, options=options_for(role), contents=contents)
        return {legacy, supervision.report_bound_fingerprint(legacy, reports[role])} if role in reports else {legacy}

    # A new dispatch reads frozen copies everywhere: every check below, its
    # identity, the prompt it sends (#460). A dispatch recorded before the
    # freeze keeps the source paths its record names, so its replay still
    # matches -- only when the complete identity those paths resolve to is
    # the recorded one. A dry run writes nothing and reads the sources.
    def is_replay(role, name):
        """An applied row these source paths resolve to, by the identity the send loop resolves."""
        identifier, fingerprint = identity(role, name, paths)
        legacy = legacy_judge_fingerprints(role, name, paths)
        return any(row.get("status") == "applied" and (
            (row.get("id"), row.get("fingerprint")) == (identifier, fingerprint) or row.get("fingerprint") in legacy)
            for row in store["dispatches"])

    decision = "frozen" if args.dry_run or not args.task else freeze_decision(assignments, is_replay)
    if decision == "frozen" and not args.dry_run:
        paths = freeze_paths(paths)
        contents = paths.contents
    if seated or any(key in document for key in ("slice_paths", "slice_digest", "seat_digests")):
        # Keyed on the metadata, not only on the seats: a saved plan stripped
        # of every seat would otherwise skip the check entirely and dispatch a
        # full-surface role while still carrying the boundary it was planned
        # against. After the briefs resolve, since the check reads each brief.
        _require_bound_slices(document, seated, paths, contents=contents)
    replayed = []
    dispatches = {}
    # Check retry identities before next-attempt validation: a completed retry
    # returns its original outcome and never consumes a second attempt.
    if args.task and not args.dry_run:
        resolved = []
        for role, name in assignments.items():
            if canonical_role(role) == "judge":
                legacy = legacy_judge_fingerprints(role, name, paths)
                # `not_sent` reached no worker and stays retryable, as ever.
                earlier = next((row for row in store["dispatches"]
                                if row.get("fingerprint") in legacy and "judge_mode" not in row
                                and row.get("status") != "not_sent"), None)
                if earlier is not None:
                    raise UsageError(
                        "Judge dispatch {} was recorded before its mode was part of its identity and "
                        "has status {!r}. Inspect its recorded outcome instead of sending it again; "
                        "a fresh judge round needs a changed brief.".format(earlier["id"], earlier["status"]),
                        {"dispatch": earlier["id"]})
            identifier, fingerprint = identity(role, name, paths)
            prior = recovery.prior_dispatch(store, identifier, fingerprint)
            if decision == "source" and not (prior and prior["status"] == "applied"):
                # The source brief changed after the replay decision read it,
                # so this is no longer the recorded dispatch and would send a
                # mutable file (#460).
                raise UsageError("The source brief for {} changed after it matched its recorded dispatch, so it is "
                                 "new work. Re-run `{}`; a new dispatch is sent from a frozen copy.".format(role, runnable.command("apply")),
                                 {"role": role})
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
            # A fresh release waits for the task's open verdict gates; a
            # completed release replay is not re-gated (#646).
            if any(canonical_role(role) == "release" for role in fresh):
                hold_gates()
                report_gates.require_no_verdict_gate(state_path, args.task, store["dispatches"])
            moves = _refusal_moves(store, agents_by_name, assignments, fresh, args, paths, reports, contents)
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
                                    "brief_identity": recovery.brief_identity(paths, role, reports.get(role), contents),
                                    "provider": agents_by_name[name].kind}
                if prior is None or "launch_scope" in prior:
                    dispatches[role]["launch_scope"] = model_unavailability.launch_scope(
                        agents_by_name[name], document["worker_kinds"][role] if scoped else None)
                if scoped:
                    dispatches[role]["worker_kind"] = document["worker_kinds"][role]
                if role in moves:
                    dispatches[role]["refusal_move"] = moves[role]
                if role in requirements:
                    dispatches[role]["requirements"] = requirements[role]
                if canonical_role(role) == "judge":
                    dispatches[role]["judge_mode"] = judge_mode
                if role in oracles:
                    dispatches[role]["oracle"] = oracles[role]
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
        if any(canonical_role(role) == "release" for role in assignments):
            hold_gates()
            report_gates.require_no_verdict_gate(state_path, args.task, store["dispatches"])
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
    planned_snapshot = _planned_snapshot(document, state, state_path)
    planned_headroom = _snapshot_headroom(planned_snapshot)
    capacity_groups = _snapshot_groups(planned_snapshot)
    if scoped:
        planned_headroom = _scoped_headroom(
            assignments, document["worker_kinds"], planned_headroom)
        capacity_groups = _scoped_headroom(assignments, document["worker_kinds"], capacity_groups)
    capability_refusals = []
    candidates = _candidate_tiers(list(assignments), agents, rounds, args.fix_round, active_judge,
                                  excludes=constraints["exclude"], headroom=planned_headroom,
                                  table=capabilities.load(state_path), refusals=capability_refusals,
                                  measured_at=(planned_snapshot or {}).get("measured_at"), at=at,
                                  capacity_groups=capacity_groups,
                                  worker_kinds={name: document["worker_kinds"][role] for role, name in assignments.items()} if scoped else None,
                                  unavailable_store=store)
    tiers = {}
    if candidates is not None:
        for role, name in assignments.items():
            refused = next((row for row in capability_refusals if (row["role"], row["agent"]) == (role, name)), None)
            if refused is not None:
                raise capabilities.InadequateCapability(refused["message"], {key: refused[key] for key in refused if key != "message"})
            if name not in candidates.get(role, {}):
                raise UsageError("Assigned agent {} has no eligible tier for {}; replan from current config.".format(name, role), {})
            if candidates[role][name] is not None:
                tiers[role] = candidates[role][name]
        saved_tiers = {role: tier for role, tier in document.get("tiers", {}).items() if tier is not None and role in assignments} if isinstance(document.get("tiers", {}), dict) else None
        # An opted-in route revalidates each binding fact above. Changes to
        # its explanatory rejected-candidate data do not change a launch or
        # make unrelated maintenance a prerequisite. Legacy comparison stays
        # unchanged; an actual selected pair/row/cost/context drift refuses.
        def comparison(rows):
            if rows is None:
                return None
            return {role: {key: value for key, value in tier.items() if key not in PLAN_ONLY_TIER_FIELDS}
                    if isinstance(tier, dict) and "routing" in tiers.get(role, {}) else tier for role, tier in rows.items()}
        if "tiers" in document and comparison(saved_tiers) != comparison(tiers):
            raise UsageError("Plan tiers differ from current config or fix context; re-run `{}` before dispatch.".format(runnable.command("plan")), {})
        # The capability verdict explains the plan; it is not part of the tier a
        # dispatch records, so the assignment row keeps its schema (#520).
        tiers = {role: {key: value for key, value in tier.items() if key not in PLAN_ONLY_TIER_FIELDS}
                 for role, tier in tiers.items()}
    client = client if client is not None else _client(args, trace=trace)

    if args.retain_specialist:
        engagement.require_followup(state, state_path, assignments)

    fresh_workers = scoped
    # Pure context and cumulative-count gates precede any fresh surface.
    if fresh_workers:
        validate_context_mode(
            assignments, args.no_clear, args.retain_context, args.task, args.fix_round,
            recovery=store, history=state["assignments"], plan_id=args.correction_plan, work=work,
            retain_specialist=args.retain_specialist, requirements=requirements,
            assignment_scoped=scoped, fresh=fresh_workers,
        )
        validate_fix_history(assignments, state["assignments"], args.task, args.fix_round, recovery=store)

    if args.dry_run:
        rehearsal = dry_run(
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
                fresh=fresh_workers, assignment_scoped=scoped,
            )
        if fresh_workers:
            rehearsal["spawns"] = [
                {"role": role, "agent": name, "worker_kind": document["worker_kinds"][role],
                 "commands": lifecycle.rendered_commands(client, agents_by_name[name], tiers.get(role))}
                for role, name in assignments.items()
            ]
        return rehearsal, None

    prepared = []

    def prepare(step, observed):
        if not args.task:
            return
        record = {**dispatches[step["role"]], "observed_before": observed,
                  "brief": step["brief"], "common": step["common"]}
        if supervised:
            _supervision_enrollment(state_path, record["id"], args.task, step["role"], step["agent"], reports[step["role"]], at,
                                    pane_id=step["pane_id"], persist=True)
        if record["id"] in prepared:
            recovery.observe_reserved(store, record["id"], observed)
        else:
            recovery.reserve(store, record, at)
            prepared.append(record["id"])
        save_state(state_path, state)

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
            # Protect uncertain persistence/transport only once all pre-send
            # observation and enrollment work has completed.
            sending.add(step["agent"])
            recovery.mark_sending(store, dispatches[step["role"]]["id"], at, context)
            save_state(state_path, state)

    def record(result):
        seat = result["role"]
        base = canonical_role(seat)
        if scoped:
            result["worker_kind"] = document["worker_kinds"][seat]
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
            if "launch_scope" in dispatches[result["role"]]:
                result["launch_scope"] = dispatches[result["role"]]["launch_scope"]
            recovery.finish_dispatch(store, result["dispatch_id"], dict(result), len(state["assignments"]) - 1, at)
        save_state(state_path, state)
        # Commit the real transport outcome before optional identity refinement;
        # a failed sidecar write must never make a confirmed send replayable.
        if supervised and result["status"] == "applied":
            native = observed_native(result["role"], result["agent"], result)
            _supervision_enrollment(state_path, result["dispatch_id"], args.task, result["role"], result["agent"], reports[result["role"]], at,
                                    pane_id=result["pane_id"], native=native, persist=True)

    guard = retrospective_runtime.Guard(
        state_path, state, client, agents_by_name, at,
        task=args.task, retain=args.retain_context or args.retain_specialist,
        no_clear=args.no_clear,
    )
    if fresh_workers:
        guard.retries = {name: proof for role, name in assignments.items()
                         if (proof := _closed_no_send_retry(state_path, state, client, dispatches[role], tiers[role], paths)) is not None}
        repaired = retrospective.recover_no_send_transitions(state_path, guard.retries,
            proof_for=lambda transition: _historical_no_send_proof(state_path, state, client, transition))
        if repaired:
            warn("Restored original retrospective start provenance for proved closed no-input retries: {}. "
                 "Incoming identities, timestamps and immutable targets are preserved.".format(", ".join(repaired)))
    cleanup_evidence = str(Path(state_path).expanduser().resolve())

    def clean_pre_send(primary, names, reason):
        """Best-effort cleanup that never replaces the active failure."""
        failures = []
        closed = []
        retained = []
        if isinstance(primary, ForemanError) and primary.details.get("failure_kind") == "startup_dialog_pending":
            name = primary.details.get("agent")
            if name in names and spawned[name] == primary.details.get("pane_id") and name not in sending:
                retained.append(name)
        for name in names:
            if name in retained:
                continue
            pane = spawned[name]
            try:
                lifecycle.close(client, name, pane)
                closed.append(name)
            except ForemanError as cleanup:
                failures.append({
                    "operation": "close_pane", "target": pane,
                    "error": cleanup.to_dict(),
                })
        for identifier in prepared:
            try:
                recovery.abort_pre_send(store, identifier, at, reason)
            except ForemanError as cleanup:
                failures.append({
                    "operation": "abort_pre_send", "target": identifier,
                    "error": cleanup.to_dict(),
                })
        state_saved = True
        if prepared or enrolled:
            try:
                save_state(state_path, state)
            except ForemanError as cleanup:
                state_saved = False
                failures.append({
                    "operation": "save_state", "target": str(state_path),
                    "error": cleanup.to_dict(),
                })
        if state_saved:
            for name in closed:
                if name not in enrolled:
                    continue
                identifier = enrolled[name]
                try:
                    supervision.resolve(
                        state_path,
                        {"id": identifier, "outcome": "Pre-send pane cleanup: " + reason,
                         "evidence": [cleanup_evidence]},
                        at,
                    )
                except ForemanError as cleanup:
                    failures.append({
                        "operation": "resolve_enrollment", "target": identifier,
                        "error": cleanup.to_dict(),
                    })
        if isinstance(primary, ForemanError):
            known_closed = (bool(names) and bool(prepared) and not retained and not failures and not sending
                and state_saved and all(next(row for row in store["dispatches"] if row["id"] == identifier)["status"] == "not_sent" for identifier in prepared))
            cleanup_id = next((row["id"] for row in store["dispatches"] if row["id"] in prepared
                and row["status"] == "not_sent" and (not retained or row["agent"] in retained)), None)
            operation = "apply" if known_closed else ("reconcile --dispatch " + shlex.quote(cleanup_id) if cleanup_id and state_saved and (not sending or retained) else "supervision-status")
            owner_recovery(primary, primary.details.get("failure_kind", primary.code),
                runnable.command(operation + " --state " + shlex.quote(str(state_path))),
                ("Owned pre-send surfaces are closed and not_sent is durable. Repeat the identical normal apply; no retrospective, configuration or receipt repair is required."
                 if known_closed else "Read the retained native pane and follow Runtime Dialogs under existing task authority, without a redundant operator approval. After the same target returns to its empty composer, run the named reconciliation, then repeat unchanged apply. Do not repeat apply while its dialog remains."
                 if retained and state_saved and cleanup_id else "The owner must complete recorded transport/cleanup reconciliation before the unchanged apply may retry; unknown or sent work is preserved."),
                outcome="retryable" if known_closed else "blocked",
                closed_agents=closed, retained_agents=retained, dispatches=list(prepared), cleanup_failures=failures,
                state_saved=state_saved, sending_agents=sorted(sending))
            if failures:
                primary.details["cleanup_failures"] = failures
        elif failures:
            action = "Owner cleanup remains blocked; reconcile the recorded transport before retrying normal apply."
            action += " Cleanup failures: " + json.dumps(failures, sort_keys=True)
            if primary is not None and hasattr(primary, "add_note"):
                primary.add_note(action)
            elif primary is None:
                raise owner_recovery(HerdrError(action, {"cleanup_failures": failures}),
                    "pre_send_cleanup_failed", runnable.command("reconcile"),
                    "The owner must prove closure and durable not_sent; preserve unknown or sent work.")

    spawned = {}
    enrolled = {}
    sending = set()
    if fresh_workers:
        spawn_complete = False
        try:
            for role, name in assignments.items():
                if not isinstance(role, str):
                    raise UsageError("Assignment role must be text; replan from an unedited document.", {})
                tier = tiers.get(role)
                if not isinstance(tier, dict):
                    raise UsageError("Assignment-scoped seat {} has no selected tier; replan from the current config.".format(role), {})
                def preflight_start(pane, role=role, name=name, tier=tier):
                    guard.before_start({
                        "agent": name, "role": role,
                        "model": tier.get("model"), "effort": tier.get("effort"),
                        "context": "start", "task": args.task,
                        "brief": paths[role], "common": paths["common"],
                        "report": None, "unavailable": None, "pane": pane,
                    })
                pane = lifecycle.spawn(
                    client, agents_by_name[name], tier,
                    history=state["assignments"], before_start=preflight_start,
                )
                spawned[name] = pane
                identifier = dispatches[role]["id"]
                _supervision_enrollment(
                    state_path, identifier, args.task, role, name, reports[role], at,
                    pane_id=pane, persist=True)
                enrolled[name] = identifier
                recovery.reserve(store, {
                    **dispatches[role], "observed_before": None,
                    "brief": paths[role], "common": paths["common"],
                    "context_before_send": {"tier": {**tier, "verified": verify_running(client, agents_by_name[name], pane, tier)},
                                            **({"judge_mode": judge_mode} if canonical_role(role) == "judge" else {})},
                }, at)
                prepared.append(identifier)
                save_state(state_path, state)
                live = client.agent_get(name)
                if live.get("pane_id") == pane and live.get("agent_status") == "blocked":
                    recovery.observe_reserved(store, identifier, {
                        "state": "blocked", "herdr_state": "blocked", "state_source": "herdr", "pane_id": pane,
                        "context_session": native_context_session(live, agents_by_name[name].kind),
                    })
                    save_state(state_path, state)
                    raise startup_pending_error(agents_by_name[name], pane)
            spawn_complete = True
        finally:
            if not spawn_complete:
                primary = sys.exc_info()[1]
                reason = (str(primary) or type(primary).__name__) if primary is not None else "spawn failed"
                clean_pre_send(primary, list(spawned), reason)

    apply_complete = False
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
            reports=reports,
            contents=contents,
            retrospective_guard=guard,
            fresh=fresh_workers, assignment_scoped=scoped,
        )
        apply_complete = True
    finally:
        if not apply_complete:
            primary = sys.exc_info()[1]
            reason = (str(primary) or type(primary).__name__) if primary is not None else "dispatch failed"
            clean_pre_send(primary, [name for name in spawned if name not in sending], reason)
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
    # A retry replays before every precondition the reset itself can change
    # (stow readiness, supervision work); reading the stow and supervision and
    # checking the caller's pane still come first (see foreman_reset.replay).
    # A pane id alone can be set by any process; a Herdr pane also carries HERDR_ENV.
    # rules/agent-team-operation.md Two Modes: a team round is HERDR_ENV set, any value.
    caller = os.environ.get("HERDR_PANE_ID") if "HERDR_ENV" in os.environ else None
    if bound_pane and caller != bound_pane:
        raise UsageError("foreman-reset runs from the bound foreman's own pane ({}); this call came from {}.".format(
            bound_pane, caller or "outside Herdr"), {"pane_id": bound_pane})
    options = _resume_options(args)
    existing = foreman_reset.replay(state_path, {"pane_id": bound_pane, "stow": stow["id"]}) if bound_pane else None
    if existing is not None:
        return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "scheduled": True, **existing,
                "log": str(Path(str(state_path) + ".foreman-reset.log"))}, None
    plan = foreman_reset.preflight(stow, data, caller)
    native_session = foreman_reset.bound_session(data)
    log = Path(str(state_path) + ".foreman-reset.log")
    # The deliverer runs from the package directory, so every path it gets is absolute.
    argv = [sys.executable, "-m", "foreman", "foreman-reset-deliver", "--pane", plan["pane_id"], "--stow", plan["stow"],
            "--state", str(state_path), "--config", str(Path(_config_path(args)).expanduser().resolve())]
    herdr_bin = _herdr_bin_setting(args)
    if herdr_bin:
        argv += ["--herdr-bin", herdr_bin]

    def start():
        try:
            # No-follow, like the reset record: a planted link would send Herdr
            # diagnostics into another file.
            descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "ab") as sink:
                return (spawn or _spawn_detached)(argv, sink)
        except OSError as exc:
            raise StateError("Could not start the reset deliverer ({}); nothing was sent.".format(exc),
                             {"log": str(log)}) from None

    row = foreman_reset.schedule(state_path, plan, at, start, native_session=native_session, options=options)
    return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "scheduled": True, **row, "log": str(log),
            "next": "End this turn now; the deliverer clears the pane once it is idle."}, None


def _absolute_executable(value):
    """A relative executable path resolved now, before the deliverer changes directory."""
    return str(Path(value).expanduser().resolve()) if os.sep in value else value


def _herdr_bin_setting(args):
    """The non-default herdr executable this call uses, flag first, then FOREMAN_HERDR_BIN.

    The detached deliverer runs from another directory and a resumed foreman
    from a fresh context, so a relative path from either source is resolved
    here, against this call's directory, or it names a different file there.
    """
    value = getattr(args, "herdr_bin", None) or os.environ.get("FOREMAN_HERDR_BIN")
    return _absolute_executable(value) if value else None


def _spawn_detached(argv, sink):
    """Start an owned child that proves its loaded identity and durable claim."""
    return foreman_reset.DetachedReset(argv, sink, str(Path(__file__).resolve().parents[1]))


def _resume_options(args):
    """The non-default owner settings a resumed foreman must keep passing."""
    options = {"config": str(Path(_config_path(args)).expanduser().resolve())}
    herdr_bin = _herdr_bin_setting(args)
    if herdr_bin:
        options["herdr_bin"] = herdr_bin
    return options


def _raise_reset_failure(state_path, stow, outcome, record) -> NoReturn:
    """Record a deliverer failure, then exit with the error it earned.

    `reset_ended` authorizes the operator's recovery, so it is raised only once
    the row shows `failed` or `interrupted`. The record is also the durable
    blocker: `catch-up` reads it (`foreman_reset.outstanding`), so a failure
    that could not be recorded still surfaces there as a reset with no outcome.
    """
    status = record()
    if status not in foreman_reset.TERMINAL_FAILURES:
        raise StateError("The reset for stow {} failed here, but its record shows {!r}, which another process set; this "
                         "deliverer authorizes no recovery. Inspect {}.".format(stow, status, foreman_reset.record_path(state_path)),
                         {"record": str(foreman_reset.record_path(state_path)), "status": status})
    raise foreman_reset.delivery_failed(state_path, stow, outcome)


def _log_safe(warn):
    """A warning sink for the detached deliverer, whose stderr is a persistent log.

    Composer warnings can quote raw pane or subprocess text, so the log gets a
    fixed line per warning instead: the warning stays visible, and its body
    never reaches the file (rules/no-secrets.md Logging).
    """
    def sink(_message):
        if warn is not None:
            warn("composer warning during reset delivery; its text is withheld from this log")
    return sink


def cmd_foreman_reset_deliver(args, client=None, warn=None, trace=None):
    state_path = Path(_state_path(args)).expanduser().resolve()
    plan = {"pane_id": args.pane, "stow": args.stow}
    options = _resume_options(args)
    startup_fd = getattr(args, "startup_fd", None)
    try:
        identity = supervision_runtime.process_identity(os.getpid())
        foreman_reset.startup_notify(startup_fd, "ready", identity)
        claimed = foreman_reset.claim(state_path, plan, identity)
    except ForemanError as exc:
        # Nothing was typed. The row must show a terminal failure before the
        # operator's recovery is authorized.
        outcome = foreman_reset.failure(exc, args.stow, str(state_path), **options)
        _raise_reset_failure(state_path, args.stow, outcome,
                             lambda: foreman_reset.fail_unclaimed(state_path, plan, outcome))
    if not claimed:
        foreman_reset.startup_notify(startup_fd, "unclaimed", identity)
        return {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, **plan, "skipped": "not the scheduled owner of this reset"}, None
    try:
        foreman_reset.startup_notify(startup_fd, "claimed", identity)
        # Setup runs after the claim, so its failure must finish the row too.
        client = client if client is not None else _client(args, trace=trace)
        result = foreman_reset.deliver(
            client, load_config(_config_path(args)), args.pane, args.stow, str(state_path), warn=_log_safe(warn), options=options,
            native_session=claimed["native_session"],
            still_ready=lambda: memory.show(state_path, now_iso(), args.stow)["record"].get("reset_ready") is True)
    except ForemanError as exc:
        status = "interrupted" if isinstance(exc, foreman_reset.DeliveryInterrupted) else "failed"
        outcome = foreman_reset.failure(exc, args.stow, str(state_path), **options)

        def record():
            foreman_reset.finish(state_path, plan, status, outcome)
            return status
        _raise_reset_failure(state_path, args.stow, outcome, record)
    try:
        foreman_reset.finish(state_path, plan, "delivered", result)
    except ForemanError as exc:
        # The pane is resumed; only the record lags. Catch-up shows the row as a
        # delivery with no outcome, and this says which way it actually went.
        raise StateError("The reset from stow {} was delivered and the foreman resumed, but the record could not say so: "
                         "{} Once the record is readable, run `{}`; do not recover the pane.".format(
                             args.stow, exc.message,
                             foreman_reset.reconcile_command(state_path, args.pane, args.stow, "delivered")),
                         {"record": str(foreman_reset.record_path(state_path)), "delivered": result}) from None
    return result, None


def cmd_foreman_reset_reconcile(args, client=None, warn=None, trace=None):
    state_path = Path(_state_path(args)).expanduser().resolve()
    return foreman_reset.reconcile(state_path, {"pane_id": args.pane, "stow": args.stow}, args.outcome,
                                   getattr(args, "now", None) or now_iso()), None


def cmd_close_member(args, client=None, warn=None, trace=None):
    client = client if client is not None else _client(args, trace=trace)
    return members.close(_state_path(args), args.enrollment, args.ledger, args.now or now_iso(), client=client), None


def cmd_check_member(args, client=None, warn=None, trace=None):
    # A checkpoint verdict travels in `exit` and `wait`; a wait that could not
    # run (exit 2, or any code outside the verdicts) fails this command.
    payload, code = members.check(_state_path(args), args.enrollment, args.worktree, warn=warn)
    if code not in members.VERDICT_EXITS:
        return payload, {"error": "wait_failed", "message": "wait-report.sh exited {} without a verdict: {} Resolve "
                         "that diagnostic, then run `{}` again.".format(code, payload["diagnostics"] or "(no diagnostic)",
                                                                 runnable.command("check-member")),
                         "details": {"exit": code}}
    return payload, None


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


def cmd_cost_report(args, client=None, warn=None, trace=None):
    state_path = _state_path(args)
    # An unusable ledger is not an empty one: reporting no tasks would read as
    # no resource spent.
    state, usable = load_state_checked(state_path, warn=warn, persist_migration=False)
    if not usable:
        raise StateError("State file {} is unusable, so no task's resource use can be derived; repair or migrate it with `{}` first.".format(
            state_path, runnable.command("state")), {"path": str(state_path)})
    return cost_report.report(state, args.task), None


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
        raise UsageError("Enrollment {} has no supervision enrollment with a recorded dispatch; read `{}` for the enrollment id.".format(
            args.enrollment, runnable.command("supervision-status")), {})
    if args.decision != "wake" and args.task not in state["recovery"]["tasks"] and not any(
            row.get("task") == args.task for row in state["assignments"]):
        raise UsageError("Task {!r} is neither registered nor assigned; check its identity with `{}`.".format(args.task, runnable.command("state")), {})
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
        "resolution_condition": "Record the acknowledgement, authorize a plan over this remedy, or approve a different approach with `{}` to override it.".format(runnable.command("authorize-approach")),
        "sources": [{"schema_version": attention.SCHEMA_VERSION, "kind": "artifact",
                     "ref": diagnosis["judge_evidence"]["path"]}],
    }, at)


def _pinned_judge_identity(store, history, task, judge):
    """Latest live assignment identity supplied by the configured judge kind."""
    if judge is None:
        return None
    matches = [row for row in store["dispatches"]
               if canonical_role(row.get("role")) == "judge" and row.get("task") == task
               and row.get("status") == "applied"
               and (row.get("agent") == judge.agent or row.get("worker_kind") == judge.agent)]
    if not matches:
        return judge.agent
    by_index = {row["assignment_index"]: row for row in matches}
    timed = [(chronology.timestamp(history[index].get("at"), "Assignment {} chronology".format(index)), index, row)
             for index, row in by_index.items()]
    latest_time = max(item[0] for item in timed)
    latest = [(index, row) for at, index, row in timed if at == latest_time]
    if len(latest) != 1:
        raise UsageError(
            "Latest pinned-judge assignment chronology is uncertain at indices {}; recover the original event times before ruling.".format(
                ", ".join(str(index) for index, _row in latest)),
            {},
        )
    return latest[0][1]["agent"]


def _scoped_headroom(assignments, worker_kinds, headroom):
    """Map current live identities to kind headroom after replay filtering."""
    return {name: headroom.get(worker_kinds[role])
            for role, name in assignments.items()}


def _worker_kind_provenance(state):
    """Stable planner names keyed by the assignment rows their dispatches produced."""
    assignments = state.get("assignments", [])
    return {
        row["assignment_index"]: row["worker_kind"]
        for row in state.get("recovery", {}).get("dispatches", [])
        if (isinstance(row, dict) and isinstance(row.get("worker_kind"), str)
            and type(row.get("assignment_index")) is int
            and 0 <= row["assignment_index"] < len(assignments))
    }


def cmd_recovery(args, client=None, warn=None, trace=None):
    state_path = _state_path(args)
    # A review receipt's gate check and its commit are one transaction: the
    # report-gate lock is held from the check through the state save, so no
    # gate can be recorded between them (foreman/report_gates.py `holding`).
    with report_gates.holding(state_path) if args.command == "record-report" else nullcontext():
        return _run_recovery(args, state_path, warn, client, trace)


def _run_recovery(args, state_path, warn, client, trace):
    state = _load_state_for_write(state_path, warn)
    store, history = state["recovery"], state["assignments"]
    cleanup_dispatch = getattr(args, "dispatch", None) if args.command == "reconcile" else None
    selected_cleanup = _recorded_no_send_cleanup(state_path, state, cleanup_dispatch) if cleanup_dispatch else None
    data, at = (_read_record(args.record) if args.record is not None else {}), args.now or now_iso()
    if args.command == "task":
        result = recovery.register_task(store, data, at)
    elif args.command == "close-task":
        result = recovery.close_task(store, history, data, at)
    elif args.command == "checkpoint":
        judge = load_judge(_config_path(args))
        task = data.get("task") if isinstance(data, dict) else None
        result = recovery.checkpoint(store, history, data, at, _pinned_judge_identity(store, history, task, judge))
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
        judge_identity = _pinned_judge_identity(store, history, data.get("task") if isinstance(data, dict) else None, judge)
        if judge is not None and isinstance(data, dict):
            dispatch = recovery.applied_judge_dispatch(store, history, data.get("task"), judge_identity)
            if dispatch is not None:
                member = next((item for item in supervision.load(state_path)["members"]
                               if item["id"] == dispatch["id"]), None)
                if member is not None:
                    enrolled = supervision.expected_assignment(member)["report"]
        result = recovery.diagnose(store, history, data, at, judge_identity, enrolled,
                                   supervision.dispatch_binding(state_path) is not None,
                                   state["specialist_assessments"])
        if result["remedy"] == "stop":
            _record_stopped_task(state_path, result, at)
    elif args.command == "record-report":
        if isinstance(data, dict):
            dispatch = next((item for item in store["dispatches"] if item["id"] == data.get("dispatch")), None)
            if dispatch is not None:
                _require_independent_report(state, dispatch["task"], data.get("reviewer"))
        # The report's contract lines first, then its classifier gates (#625);
        # a refusal from either leaves the state unsaved.
        result = recovery.record_report(store, data, at)
        report_gates.require_clear(state_path, data["report"], data["verdict"] == "approved")
        # A blocking verdict holds the task's release until a re-check clears
        # it (#646); recorded under the lock this command already holds.
        if result["verdict"] == "blocking":
            report_gates.record_verdict(state_path, result["report"], result["evidence"]["sha256"],
                                        result["dispatch"], at)
    elif args.command == "authorize-refused-dispatch":
        result = recovery.authorize_refused_dispatch(store, data, at)
    elif args.command == "record-refusal":
        agents_by_name = {agent.name: agent for agent in load_config(_config_path(args))}
        dispatch = next((item for item in store["dispatches"] if isinstance(data, dict) and item["id"] == data.get("dispatch")), None)
        configured = dispatch.get("worker_kind") if dispatch is not None else None
        configured = configured or (dispatch.get("agent") if dispatch is not None else None)
        if dispatch is not None and configured not in agents_by_name:
            raise UsageError("Refused worker {} is not in config.json; restore its entry so the refusing provider is recorded.".format(dispatch["agent"]), {})
        member = next((row for row in supervision.load(state_path)["members"] if dispatch is not None and row["id"] == dispatch["id"]), None)
        # The refined assignment, not the original: supervision fills in a
        # pane id the enrollment did not know, and wait-report may have been
        # given that one (#403).
        report, aliases, expected = None, (), None
        if member is not None:
            expected = supervision.expected_assignment(member)
            report = expected["report"]
            aliases = (expected["pane_id"], member["assignment"]["pane_id"])
        result = recovery.record_refusal(store, data, at, agents_by_name[configured].kind if dispatch else None,
                                         report, aliases=aliases, binding=expected)
    elif args.command == "recover-report":
        result = report_delivery.recover(store, history, data, at)
    elif args.command == "assess-specialist":
        try:
            result = engagement.record_assessment(state, state_path, data, at)
        except engagement.ContractGap:
            # The refused report's declared contribution is kept before the
            # refusal propagates: an exclusion is never lost (#625).
            recovery.validate_store(store, history)
            engagement.validate_assessments(state)
            save_state(state_path, state)
            raise
        # A blocking verdict holds the task's release (#646). A replay records
        # it too, so a crash between the two writes heals on retry.
        if result["source"] == "report" and result["verdict"] == "blocking":
            report_gates.record_verdict(state_path, result["report"], result["report_evidence"]["sha256"],
                                        result["dispatch"], at)
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
        templates = load_config(_config_path(args))
        agents = {agent.name: agent for agent in templates}
        name = selected_cleanup[0]["agent"] if selected_cleanup else recovery.recovery_agent(store, history, args.command, data)
        if name not in agents and templates and all(agent.assignment_scoped for agent in templates):
            dispatch = next((row for row in reversed(store["dispatches"])
                             if row.get("agent") == name and isinstance(row.get("worker_kind"), str)), None)
            if dispatch is not None:
                agents = lifecycle.materialize({"recovery": name}, {"recovery": dispatch["worker_kind"]}, templates)
        if name not in agents:
            raise UsageError("The recorded worker is absent from config; restore its original identity before recovering.", {})
        client = client if client is not None else _client(args, trace=trace)
        if selected_cleanup:
            dispatch, proof_record, assignment = selected_cleanup
            pane = assignment["pane_id"]
            tier = (dispatch.get("context_before_send") or {}).get("tier") or (dispatch.get("observed_before") or {}).get("tier")
            original_session = (dispatch.get("observed_before") or {}).get("context_session")
            original_process = tier.get("verified") if isinstance(tier, dict) else None
            cleanup_identity = None
            def observe_cleanup():
                nonlocal cleanup_identity
                try:
                    current = client.agent_get(name)
                except HerdrError as exc:
                    if error_code(exc) != "agent_not_found":
                        raise
                    try:
                        client.pane_get(pane)
                    except HerdrError as pane_error:
                        if error_code(pane_error) != "pane_not_found":
                            raise
                    else:
                        require_empty_shell(client, pane)
                    return None
                observation = dispatch.get("observed_before")
                if (not isinstance(observation, dict) or "context_session" not in observation
                        or not isinstance(original_process, dict)
                        or original_process.get("source") != "process_argv"
                        or type(original_process.get("pid")) is not int or original_process["pid"] <= 0):
                    raise UsageError("No original native-session observation/process proof is available for cleanup; preserve the worker and inspect its recorded evidence.", {})
                running = verify_running(client, agents[name], pane, tier)
                identity = (native_context_session(current, agents[name].kind), running)
                if (current.get("pane_id") != pane or current.get("agent_status") not in READY_STATES
                        or identity != (original_session, original_process)
                        or (cleanup_identity is not None and identity != cleanup_identity)):
                    raise HerdrError("Recorded cleanup identity differs from the original session/process; preserve the worker and inspect its recorded evidence.", {})
                cleanup_identity = identity
                return identity
            try:
                try:
                    live = client.agent_get(name)
                except HerdrError as exc:
                    if error_code(exc) != "agent_not_found":
                        raise
                    try:
                        client.pane_get(pane)
                    except HerdrError as pane_error:
                        if error_code(pane_error) != "pane_not_found":
                            raise
                    else:
                        require_empty_shell(client, pane)
                else:
                    recovery.require_recovery_ready(live)
                    ensure_ready(client, agents[name], pane, startup_observe=observe_cleanup)
                    observe_cleanup()
                closure = _cleanup_reconciled_scoped_not_sent(state_path, dispatch, proof_record, at, client,
                                                            before_close=observe_cleanup)
            except ForemanError as exc:
                raise owner_recovery(exc, exc.details.get("failure_kind", "retry_cleanup_unproved"),
                    runnable.command("supervision-status --state " + shlex.quote(str(state_path))),
                    "The owner must observe the original identity and retain any draft, changed tier or unknown work; repeat recorded no-send reconciliation only when this surface is proved empty.",
                    dispatch=dispatch["id"], pane_id=pane)
            return {**dispatch, "pane_closure": closure, "cleanup_replayed": True}, None
        scoped_not_sent_replay = (recovery.scoped_not_sent_replay(store, data)
                                  if args.command == "reconcile" else None)
        live = None
        if scoped_not_sent_replay is None:
            live = client.agent_get(name)
            recovery.require_recovery_ready(live)
        if args.command == "recover-context":
            result = recovery.authorize_context(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "recover-role-clear":
            result = role_clear.record_role_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "record-release-clear":
            result = historical.record_release_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        else:
            result = scoped_not_sent_replay or recovery.reconcile(store, history, data, at, live)
            if result["status"] == "not_sent" and isinstance(result.get("worker_kind"), str):
                # The transport fact becomes durable before destructive pane
                # cleanup. An interrupted or failed close can be replayed
                # without changing the reconciled outcome.
                recovery.validate_store(store, history)
                engagement.validate_assessments(state)
                save_state(state_path, state)
                result = {**result, "pane_closure": _cleanup_reconciled_scoped_not_sent(
                    state_path, result, data, at, client)}
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
                if result.get("worker_kind") is not None:
                    recovered["worker_kind"] = result["worker_kind"]
                    recovered["assignment_scoped"] = True
                if "launch_scope" in result:
                    recovered["launch_scope"] = result["launch_scope"]
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
    if isinstance(document, dict) and "worker_kinds" in document:
        raise UsageError("Schema-7 plans spawn the pinned judge during `{}`; do not start a standing judge worker.".format(
            runnable.command("apply")), {})
    tier = document.get("judge") if isinstance(document, dict) else None
    if not isinstance(tier, dict) or not isinstance(tier.get("agent"), str) or not tier["agent"].strip():
        raise UsageError("Plan has no usable judge tier; run `{}`.".format(runnable.command("plan --roles judge")), {})
    if normalize_assignments(document).get("judge") != tier["agent"]:
        raise UsageError("Plan judge tier and assignment name different workers; replan.", {})
    # The plan carries the mode the foreman declared; the flag overrides it, and
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
    # The pinned judge has no substitute: a table recording its model
    # inadequate refuses the start, before anything launches (#476).
    capabilities.assess(capabilities.load(state_path), parsed["model"], parsed["effort"],
                        capabilities.required("judge", "judge", JUDGMENT_ROUNDS))
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


def cmd_relaunch_worker(args, client=None, warn=None, trace=None):
    """Relaunch one idle configured worker without creating a dispatch."""
    agent = select_agents(load_config(_config_path(args)), [args.name])[0]
    client = client if client is not None else _client(args, trace=trace)
    live = client.agent_get(agent.name)
    pane = live.get("pane_id")
    if live.get("agent_status") not in READY_STATES or not isinstance(pane, str) or not pane:
        raise AgentBusyError("Worker {!r} is not idle in a live pane; wait for readiness before relaunching it.".format(agent.name),
                             {"agent": agent.name, "state": live.get("agent_status"), "pane": pane})
    tier, previous = configured_running_tier(client, agent, pane)
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)
    latest = chronology.latest_assignment(state["assignments"], agent=agent.name)
    if latest is None:
        row = None
        role, task = "idle", None
    else:
        offset, row = latest
        dispatch = next((entry for entry in reversed(state.get("recovery", {}).get("dispatches", []))
                         if entry.get("assignment_index") == offset and entry.get("agent") == agent.name), None)
        role, task = (dispatch or {}).get("role") or row.get("role") or "idle", row.get("task")
    item = retrospective_runtime.request({"transitions": [{
        "agent": agent.name, "role": role, "model": tier["model"], "effort": tier.get("effort"),
        "context": "clear", "task": task, "pane": pane,
    }]})["transitions"][0]
    at = args.now or now_iso()
    guard = retrospective_runtime.Guard(state_path, state, client, {agent.name: agent}, at)
    guard.prepare_relaunch(item)
    step = {"agent": agent.name}
    restart_worker(client, agent, pane, tier, before_transition=lambda: guard.before(step),
                   before_start=lambda: guard.before_launch(step), expected_process=previous,
                   recovery_command=runnable.command(
                       "relaunch-worker {}".format(shlex.quote(agent.name))
                   ))
    verified = verify_running(client, agent, pane, tier)
    guard.after_transition(step, launch_proof=verified)
    tier_record = {"kind": agent.kind, "model": tier["model"], "effort": tier.get("effort"),
                   "launch_args": worker_launch_args(agent.kind, agent.launch_args),
                   "verified": verified}
    try:
        context_session = native_context_session(client.agent_get(agent.name), agent.kind)
    except HerdrError:
        (warn or stderr_warn)(
            "The worker restarted and its process tier was verified, but its optional native session "
            "evidence could not be read; recording the maintenance relaunch with context_session null."
        )
        context_session = None
    add_assignment(
        state, at, role, agent.name, status=STATUS_MAINTENANCE,
        cleared=True, clear_reason="automatic", task=task,
        fix_round=row.get("fix_round") if row else None,
        context_session=context_session,
        tier=tier_record,
        requirements=row.get("requirements") if row else None,
        reviewer_scope=row.get("reviewer_scope") if row else None,
        judge_mode=row.get("judge_mode") if row and canonical_role(role) == "judge" else None,
    )
    save_state(state_path, state)
    return {"agent": agent.name, "pane": pane, "tier": tier, "previous": previous,
            "argv_verified": True, "verified": verified, "dispatch": None,
            "assignment_index": len(state["assignments"]) - 1}, None


#: The minimal `foreman` block the unconfigured warning tells the operator to add.
FOREMAN_BLOCK_SNIPPET = ('"foreman": {"agent": "foreman", "kind": "claude", "window_group": "<window-group>", '
                         '"tiers": {"coordination": {"model": "<model>", "effort": "<effort>"}}}')


def _foreman_unconfigured(path):
    return ("Config at {} has no `foreman` block, so the foreman's tier is neither selected nor proven. Edit that "
            "file: set `schema_version` to {}, and add {}. Then launch the foreman with `{}`.".format(
                path, FOREMAN_CONFIG_VERSION, FOREMAN_BLOCK_SNIPPET, runnable.command("start-foreman --pane <pane-id>")))


def _foreman_headroom(seat, snapshot):
    """The measured headroom of the usage window the foreman shares, or None.

    `measure` probes each configured worker's own pane with its usage prompt;
    the foreman cannot be probed from the pane it runs in. Its window is the
    `window_group` it declares, and that window's headroom is the minimum
    across the measured workers in it, the way `plan` charges a shared window.
    No group, or no measured member, reads as unmeasured. The value is recorded
    with the selection (`pressure_headroom`); it never changes the row.
    """
    if not seat.window_group or not isinstance(snapshot, dict):
        return None
    agents = snapshot.get("agents")
    if not isinstance(agents, dict):
        return None
    readings = [headroom_of(name, record, lambda _message: None) for name, record in agents.items()
                if isinstance(record, dict) and record.get("window_group") == seat.window_group]
    readings = [value for value in readings if value is not None]
    return min(readings) if readings else None


def _select_foreman_tier(args, seat, warn, *, persist_migration):
    """The tier the seat's coordination round resolves to, by the workers' own machinery.

    `persist_migration` rewrites an older state file while reading it: only a
    caller holding the state lock (`start-foreman`) passes True; the read-only
    `verify-foreman` passes False and leaves the file as found.

    The operator's tier table supplies the seat's rows, as it does for every
    worker. The tier is the `coordination` row, resolved through `select_tier`;
    the capability table refuses it when it records it inadequate. The
    coordination round carries no escalation context, so no escalation applies
    and measured headroom never changes the row. Headroom is passed only so the
    selection records it. No rule, plugin default or hardcoded value pins this
    seat's model or effort.
    """
    state_path = _state_path(args)
    state, _usable = load_state_checked(state_path, warn=warn, persist_migration=persist_migration)
    snapshot = latest_snapshot(state)
    headroom = _foreman_headroom(seat, snapshot)
    if not seat.tiers:
        raise UsageError(
            "The foreman seat has no tier table: add `foreman.tiers`, or configure a {} worker with one, so tier "
            "selection can choose its model and effort.".format(seat.kind), {"agent": seat.agent})
    needs = capabilities.required(FOREMAN_ROLE, COORDINATION_ROUND, JUDGMENT_ROUNDS)
    # The configured coordination row is the tier: no escalation context, so
    # select_tier returns that row and headroom is only recorded; no cheaper
    # unrelated row ever substitutes for it. The capability table assesses the
    # row; an inadequate verdict refuses the start.
    try:
        tier = select_tier(seat, FOREMAN_ROLE, headroom=headroom)
    except MissingTierError:
        raise UsageError(
            "The foreman's tier table has no `{}` row. Add one naming the model and effort the foreman's "
            "coordination round runs on.".format(COORDINATION_ROUND), {"agent": seat.agent}) from None
    verdict = capabilities.assess(capabilities.load(state_path), tier["model"], tier["effort"], needs)
    return {**tier, "capability": verdict, "cheaper_adequate": None}


def _foreman_seat_result(args, seat, pane, tier, proof):
    # The same selection record a planned seat carries (#602), for the one
    # seat the planner never selects.
    record = selection.records({FOREMAN_ROLE: seat.agent}, {FOREMAN_ROLE: tier}, {seat.agent: seat},
                               None, None, None, capabilities.load(_state_path(args)))[FOREMAN_ROLE]
    return {**seat.as_dict(), "configured": True, "pane": pane, "tier": tier, "selection": record,
            "argv_verified": True, "verified": proof}


def cmd_start_foreman(args, client=None, warn=None, trace=None):
    seat = load_foreman(_config_path(args))
    if seat is None:
        raise UsageError(_foreman_unconfigured(_config_path(args)), {"config": str(_config_path(args))})
    tier = _select_foreman_tier(args, seat, warn, persist_migration=True)
    client = client if client is not None else _client(args, trace=trace)
    return _foreman_seat_result(args, seat, args.pane, tier, start_foreman(client, seat, args.pane, tier)), None


def cmd_verify_foreman(args, client=None, warn=None, trace=None):
    if getattr(args, "config_only", False):
        # The preflight's view when headroom did not pass: whether the seat is
        # configured is independent of any measurement.
        seat = load_foreman(_config_path(args))
        if seat is None:
            return {"configured": False, "warning": _foreman_unconfigured(_config_path(args))}, None
        return {"configured": True, "agent": seat.agent}, None
    pane = args.pane
    if not pane:
        # rules/agent-team-operation.md Two Modes: a team round is HERDR_ENV set, any value.
        pane = os.environ.get("HERDR_PANE_ID") if "HERDR_ENV" in os.environ else None
    if not pane:
        raise UsageError("verify-foreman reads the foreman's own pane; run it from the foreman's Herdr pane or pass "
                         "--pane <pane-id>.", {})
    seat = load_foreman(_config_path(args))
    if seat is None:
        # A visible warning, never a round block: the operator has not opted
        # the seat into tier selection yet.
        return {"configured": False, "pane": pane, "warning": _foreman_unconfigured(_config_path(args))}, None
    # Registered read-only, so it runs without the state lock: it never
    # persists a migration (#626).
    tier = _select_foreman_tier(args, seat, warn, persist_migration=False)
    client = client if client is not None else _client(args, trace=trace)
    return _foreman_seat_result(args, seat, pane, tier, verify_foreman(client, seat, pane, tier)), None


def cmd_capability(args, client=None, warn=None, trace=None):
    """The capability table's cadence, its refresh, and a read of what it holds.

    `capability-check` is read-only and answers one question: is the table due.
    A table never refreshed comes due as soon as the ledger holds any work, and
    stays quiet on a fleet that has dispatched nothing (#481).
    """
    path = _state_path(args)
    if args.command == "capability-migrate":
        return capabilities.migrate(path), None
    if args.command == "capability-show":
        return capabilities.load(path), None
    at = args.now or now_iso()
    if args.command == "capability-successor":
        report = _read_record(args.record)
        if not isinstance(report, dict) or not isinstance(report.get("worker"), str):
            raise UsageError("Successor report needs the configured predecessor worker name.", {})
        agent = select_agents(load_config(_config_path(args)), [report["worker"]])[0]
        return successors.record(path, report, at, agent), None
    if args.command == "capability-check":
        document = capabilities.load(path)
        state, usable = load_state_checked(path, warn=warn, persist_migration=False)
        if not usable:
            # An unreadable ledger is not an empty one: whether work exists, and
            # so whether a refresh is due, is unknown.
            raise StateError("The ledger at {} exists but cannot be read, so whether the capability "
                             "table is due is unknown. Repair or migrate it with `{}`, "
                             "then re-run `{}`.".format(path, runnable.command("state"), runnable.command("capability-check")),
                             {"path": str(path)})
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
                or not retrospective.same_location(saved.get("state_path"), retrospective.canonical_state(path))):
            raise UsageError("Retrospective check receipt belongs to another state or schema; rerun `{}` for this --state.".format(
                runnable.command("retro-check")), {})
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
    if not retrospective.same_history(saved["coverage"], result["coverage"]):
        raise UsageError("Retrospective worker, report, assignment or target changed since its check; refresh only the affected coverage and the foreman's synthesis before recording.", {})
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


def cmd_finding_churn(args, client=None, warn=None, trace=None):
    return churn.run_command(args)


def _dispatched_seat_briefs(plan, slice_paths, dispatches, task):
    """Each seat's brief text as THIS plan dispatched it, or a refusal naming the seat.

    The seat's latest dispatch for the task, by event time, must be applied,
    to the worker the plan assigns, under the plan's task context, from an
    intact frozen brief and common brief. The text returned is the bytes
    checked, so the scope check reads what the worker read.
    An older dispatch of the seat to another worker, or a newer one that never
    applied, is not this plan's review (#460).
    """
    context = plan.get("task_context")
    if not isinstance(context, dict) or context.get("task") != task:
        raise UsageError("The plan was not made for task {!r}, so no seat dispatch can be bound to it. Replan with "
                         "`{}` and dispatch from that plan.".format(task, runnable.command("plan --partition ... --task {}".format(shlex.quote(task)))), {"task": task})
    assignments = plan.get("assignments")
    if not isinstance(assignments, dict):
        raise UsageError("The plan carries no assignments; pass the JSON `{}` wrote.".format(runnable.command("plan --partition")), {})
    bodies = {}
    for seat in slice_paths:
        agent = assignments.get(seat)
        # By event time, not append order: imported evidence can land after
        # newer rows, and a tie is refused rather than guessed.
        latest = chronology.latest_assignment(dispatches, task=task, role=seat)
        row = latest[1] if latest else None
        if not isinstance(agent, str) or row is None:
            raise UsageError("Seat {} has no dispatch for task {}; a slice nobody was sent cannot pass.".format(
                seat, task), {"seat": seat})
        mismatched = [key for key, expected in (("agent", agent), ("fix_round", context.get("fix_round")),
                                                ("plan", context.get("plan")), ("work", context.get("work")))
                      if row.get(key) != expected]
        if mismatched:
            raise UsageError("Seat {}'s latest dispatch differs from this plan in {}; it is another plan's review. "
                             "Verify the plan that dispatch was sent from, or dispatch this one.".format(
                                 seat, ", ".join(mismatched)), {"seat": seat, "fields": mismatched})
        if row.get("status") != "applied":
            raise UsageError("Seat {}'s latest dispatch is {!r}, not applied, so its worker never took this plan's "
                             "brief. Reconcile it, or dispatch the seat again.".format(seat, row.get("status")),
                             {"seat": seat, "status": row.get("status")})
        # The worker reads both, so both must still hold what was sent.
        read_frozen(row.get("common") or "")
        content = read_frozen(row.get("brief") or "")
        try:
            bodies[seat] = content.decode("utf-8")
        except UnicodeDecodeError:
            raise UsageError("Seat {}'s dispatched brief {} is not UTF-8; dispatch the seat again from a composed "
                             "brief.".format(seat, row.get("brief")), {"seat": seat}) from None
    return bodies


def cmd_verify_partition(args, client=None, warn=None, trace=None):
    """The review gate for a partitioned round: the plan's boundary, as dispatched, covers the task's diff at the tip."""
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError("Cannot read the plan at {}: {}. Pass the JSON `{}` wrote.".format(args.plan, exc, runnable.command("plan --partition")),
                         {"path": str(args.plan)}) from None
    if not isinstance(plan, dict):
        raise UsageError("The plan at {} is not a JSON object; pass the JSON `{}` wrote.".format(
            args.plan, runnable.command("plan --partition")), {})
    state, usable = load_state_checked(_state_path(args), warn, persist_migration=False)
    if not usable:
        raise StateError("The dispatch state is unusable, so the task's base and dispatched briefs cannot be read; "
                         "restore it before verifying.", {})
    store = state["recovery"]
    task = store["tasks"].get(args.task)
    if task is None:
        raise UsageError("Task {!r} has no registered base; pass the task the plan was dispatched under.".format(args.task), {})
    slice_paths = partition.check_slice_paths(plan.get("slice_paths"), "The plan")
    bodies = _dispatched_seat_briefs(plan, slice_paths, store["dispatches"], args.task)
    # Each seat reviewed what its dispatched (frozen) brief bound it to.
    _require_bound_slices(plan, sorted(slice_paths), {seat: None for seat in bodies}, bodies)
    return partition.verify(plan, args.repo, args.head, task["base_revision"]), None


def cmd_validate_partition(args, client=None, warn=None, trace=None):
    return partition.run_command(args)


#: The first line only `templates/brief-judge-weighing.md` renders. A frozen
#: judge brief opening with it was a weighing; a dispute brief never opens with it (#632).
WEIGHING_BRIEF_MARKER = b"<!-- herdr-brief: judge-weighing -->"


def cmd_verify_ruling(args, client=None, warn=None, trace=None):
    """A team-round ruling file is the report the pinned judge's weighing was sent to write (#632).

    The ruling artifact's writer/reader contract, this reader's side included,
    is the header of `skills/release/dismiss-ruled-review.sh`, which calls this
    before it accepts `AUTHORITY: judge`. The dispatch is selected by the supervision
    enrollment whose report path is the ruling file, never by "latest judge
    adjudication". That dispatch must be the pinned judge's, applied, on the
    given task, and its frozen brief must open with `WEIGHING_BRIEF_MARKER`: the
    bytes the judge actually read were the weighing brief, not a dispute's. The
    ruling's current bytes must match a delivery the owners recorded for that
    dispatch (`report_gates.ledger_view`).
    """
    judge = load_judge(_config_path(args))
    if judge is None:
        raise UsageError("No pinned judge is configured, so no ruling can be the judge's; add the `judge` block to config.json.", {})
    ruling = Path(args.ruling)
    if not ruling.is_absolute():
        raise UsageError("--ruling must be the absolute path of the judge's delivered report.", {"ruling": args.ruling})
    state, usable = load_state_checked(_state_path(args), warn, persist_migration=False)
    if not usable:
        raise StateError("The dispatch state is unusable, so the judge dispatch cannot be read; restore it before verifying the ruling.", {})
    enrolled = [row for row in supervision.load(_state_path(args))["members"]
                if Path(supervision.expected_assignment(row)["report"]).resolve() == ruling.resolve()]
    if len(enrolled) != 1:
        raise UsageError("{} is {} supervision enrollment's report; pass the report the judge's weighing dispatch was enrolled "
                         "to write.".format(args.ruling, "no" if not enrolled else "more than one"), {"ruling": args.ruling})
    dispatch = next((row for row in state["recovery"]["dispatches"] if row.get("id") == enrolled[0]["id"]), None)
    if dispatch is None:
        raise UsageError("The enrollment for {} names dispatch {}, which the dispatch state does not hold; reconcile it "
                         "before verifying.".format(args.ruling, enrolled[0]["id"]), {})
    pinned = dispatch.get("agent") == judge.agent or dispatch.get("worker_kind") == judge.agent
    if (canonical_role(dispatch.get("role")) != "judge" or not pinned
            or dispatch.get("task") != args.task or dispatch.get("status") != "applied"
            or dispatch.get("judge_mode") != "adjudication"):
        raise UsageError("Dispatch {} is not an applied adjudication of the pinned judge {} on task {!r}; the ruling is "
                         "not this task's weighing.".format(dispatch["id"], judge.agent, args.task),
                         {"dispatch": dispatch["id"], "task": dispatch.get("task"), "agent": dispatch.get("agent")})
    brief = read_frozen(dispatch.get("brief") or "")
    # First line, exact: a value field rendered into a dispute brief can put the
    # marker text on a line of its own anywhere below it.
    if brief.split(b"\n", 1)[0].rstrip(b"\r") != WEIGHING_BRIEF_MARKER:
        raise UsageError("Dispatch {} sent the judge a brief that is not the weighing brief (templates/brief-judge-weighing.md); "
                         "a dispute ruling never clears a policy review.".format(dispatch["id"]), {"dispatch": dispatch["id"]})
    try:
        body = ruling.read_bytes()
    except OSError as exc:
        raise UsageError("Cannot read the ruling at {}: {}.".format(args.ruling, exc), {}) from None
    digest = hashlib.sha256(body).hexdigest()
    # The enrolled path names where the report was meant to land, not what is
    # there now: the bytes must be ones the owners recorded delivered for this
    # dispatch, read through the same receipts report gates resolve against.
    key = str(ruling.resolve())
    if not any(row["dispatch"] == dispatch["id"] and row["path"] == key and row["sha256"] == digest
               for row in report_gates.ledger_view(_state_path(args), dispatch["agent"])["deliveries"]):
        raise UsageError("The ruling's current bytes match no recorded delivery of the judge's report — re-deliver or "
                         "re-weigh.", {"dispatch": dispatch["id"], "sha256": digest})
    verified = {"task": args.task, "dispatch": dispatch["id"], "judge": dispatch["agent"],
                "report": str(ruling), "sha256": digest}
    if dispatch.get("worker_kind") is not None:
        verified["worker_kind"] = dispatch["worker_kind"]
    return verified, None


def cmd_verify_oracle(args, client=None, warn=None, trace=None):
    """The oracle gate for a mechanical round, against the oracle its dispatch bound (#585)."""
    state, usable = load_state_checked(_state_path(args), warn, persist_migration=False)
    if not usable:
        raise StateError("The dispatch state is unusable, so the oracle the round was sent with cannot be read; "
                         "restore it before verifying.", {})
    return oracle.run_command(args, state["recovery"]["dispatches"])


def cmd_probe_report(args, client=None, warn=None, trace=None):
    if not Path(args.report).is_absolute() or not renderable.renderable(args.report) or args.lines < 1:
        raise UsageError("Report probing needs an absolute one-row report path and positive --lines.", {})
    client = client if client is not None else _client(args, trace=trace)
    return report_delivery.probe(client, args.agent, args.pane, args.report, sys.stdin.read().rstrip("\n"), args.lines), None


def cmd_marker_fit(args, client=None, warn=None, trace=None):
    """Read-only width verdict; a marker that would wrap is `fits: false`, never an error."""
    # The shared rule (#578): controls, format characters and the U+2028/U+2029
    # separators never stay on one marker row.
    if not Path(args.report).is_absolute() or not renderable.renderable(args.report):
        raise UsageError("marker-fit needs an absolute one-row --report path; pass the exact path the worker "
                         "will print after `REPORT: `.", {"report": args.report})
    client = client if client is not None else _client(args, trace=trace)
    return report_delivery.marker_fit(client, args.agent, args.report), None


def cmd_probe_unavailable(args, client=None, warn=None, trace=None):
    if not Path(args.report).is_absolute() or not renderable.renderable(args.report) or args.lines < 1:
        raise UsageError("Native error probing needs an absolute one-row report path and positive --lines.", {})
    confirmation = os.environ.get("FOREMAN_REFUSAL_CONFIRM_SEC", str(model_unavailability.CONFIRM_SECONDS))
    if not confirmation.isascii() or not confirmation.isdigit():
        raise UsageError("FOREMAN_REFUSAL_CONFIRM_SEC must be a non-negative integer; unset it for the script-owned default.", {})
    client = client if client is not None else _client(args, trace=trace)
    return model_unavailability.probe(client, args.agent, args.pane, args.report,
                                     sys.stdin.read().rstrip("\n"), args.lines,
                                     confirm_seconds=int(confirmation)), None


def cmd_memory(args, client=None, warn=None, trace=None):
    return memory.run_command(args, _state_path(args), args.now or now_iso()), None


def cmd_report_gates(args, client=None, warn=None, trace=None):
    judge = None
    if args.command in {"report-gate-clear", "report-gate-reread"}:
        # The pinned judge is who may clear in adjudication; the owner reads the
        # role from the delivering dispatch, never from the caller.
        pinned = load_judge(_config_path(args))
        judge = pinned.agent if pinned else None
    return report_gates.run_command(args, _state_path(args), now_iso(), judge), None


def cmd_attention(args, client=None, warn=None, trace=None):
    return attention.run_command(args, _state_path(args), args.now or now_iso()), None


SUPERVISION_COMMANDS = frozenset("supervision-" + action for action in ("bind", "enroll", "ack", "resolve", "hold", "resume", "drain", "status", "watch"))


def cmd_supervision(args, client=None, warn=None, trace=None):
    return supervision_runtime.run_command(args, _state_path(args), args.now or now_iso(),
                                           client=client, clock=now_iso, sleeper=time.sleep), None


def cmd_supervision_gate(args, client=None, warn=None, trace=None):
    """Which pending supervision events need the foreman; see supervision_gate.py."""
    return supervision_gate.pending(supervision.load(_state_path(args))), None


def cmd_restoration(args, client=None, warn=None, trace=None):
    client = client if client is not None else _client(args, trace=trace)
    return restoration.run_command(args, client, sleep=time.sleep), None


def cmd_migrate_home(args, client=None, warn=None, trace=None):
    return home.migrate(), None


COMMANDS = {
    "migrate-home": cmd_migrate_home,
    "measure": cmd_measure,
    "resolve-probe": cmd_resolve_probe,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "state": cmd_state,
    "status": cmd_status,
    "foreman-queue": cmd_foreman_queue,
    "cost-report": cmd_cost_report,
    "foreman-reset": cmd_foreman_reset,
    "foreman-reset-deliver": cmd_foreman_reset_deliver,
    "foreman-reset-reconcile": cmd_foreman_reset_reconcile,
    "close-member": cmd_close_member,
    "check-member": cmd_check_member,
    "load-set": cmd_load_set,
    **{command: cmd_recovery for command in ("task", "checkpoint", "authorize-corrections", "authorize-approach", "recover-context", "recover-role-clear", "record-report", "record-refusal", "authorize-refused-dispatch", "diagnose", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report", "assess-specialist", "close-task")},
    "detect-triggers": cmd_detect_triggers,
    "finding-churn": cmd_finding_churn,
    "validate-partition": cmd_validate_partition,
    "verify-partition": cmd_verify_partition,
    "verify-oracle": cmd_verify_oracle,
    "verify-ruling": cmd_verify_ruling,
    "start-judge": cmd_start_judge,
    "relaunch-worker": cmd_relaunch_worker,
    "start-foreman": cmd_start_foreman,
    "verify-foreman": cmd_verify_foreman,
    "probe-report": cmd_probe_report,
    "probe-unavailable": cmd_probe_unavailable,
    "marker-fit": cmd_marker_fit,
    **{command: cmd_retrospective for command in ("retro-check", "retro-record", "retro-list", "retro-show")},
    **{command: cmd_capability for command in ("capability-check", "capability-record", "capability-show", "capability-successor", "capability-migrate")},
    "supervision-gate": cmd_supervision_gate,
    **{command: cmd_memory for command in memory.COMMANDS},
    **{command: cmd_attention for command in attention.COMMANDS},
    **{command: cmd_report_gates for command in report_gates.COMMANDS},
    **{command: cmd_supervision for command in SUPERVISION_COMMANDS},
    **{command: cmd_restoration for command in restoration.COMMANDS},
}


#: Commands that read neither the state nor the config home.
HOME_FREE_COMMANDS = frozenset({"marker-fit", "finding-churn", "probe-unavailable"})


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
        # A default home still at the legacy path is refused before anything is
        # read or created at the new one. `migrate-home` moves the default homes
        # alone, under the home guard held exclusively; every other command holds
        # that guard shared for its whole run, so neither starts under the other.
        if args.command == "migrate-home":
            given = [flag for flag, attr in (("--state", "state"), ("--config", "config")) if getattr(args, attr, None)]
            if given:
                raise UsageError("migrate-home moves the default homes under $XDG_STATE_HOME and $XDG_CONFIG_HOME and takes "
                                 "no {}. Run it without {}; an explicit state or config file is never moved.".format(
                                     " or ".join(given), " or ".join(given)), {"given": given})
            payload, failure = COMMANDS[args.command](args, client=client, warn=warn, trace=trace)
            json.dump(payload, stdout, indent=2)
            stdout.write("\n")
            return 0
        # Only a command reading a default home takes the guard: explicit
        # --state and --config paths are never moved, so a migration never
        # blocks them.
        # A command that reads no state or config uses no home, so it neither
        # takes the guard nor refuses on a home awaiting migration.
        defaults = set() if args.command in HOME_FREE_COMMANDS else {
            kind for kind, given in (("state", getattr(args, "state", None)),
                                     ("config", getattr(args, "config", None))) if not given}
        with home.guard(False) if defaults else nullcontext():
            home.require_current(defaults)
            # Commands that may migrate or write state share its canonical lock.
            # Dry runs, probes, and retrospective reads remain read-only.
            readonly = args.command in {"probe-report", "probe-unavailable", "marker-fit", "detect-triggers", "finding-churn", "validate-partition", "verify-oracle", "verify-ruling", "retro-check", "retro-list", "retro-show", "capability-check", "capability-show", "supervision-gate", "load-set", "foreman-queue", "cost-report", "check-member", "verify-foreman", "report-gate-status"} or getattr(args, "dry_run", False)
            # The deliverer starts while `foreman-reset` still holds the state lock;
            # it serializes on the reset record's own lock instead. close-member
            # writes only through the supervision owner's own lock.
            separate_owner = args.command in memory.COMMANDS | attention.COMMANDS | report_gates.COMMANDS | SUPERVISION_COMMANDS | restoration.COMMANDS | {"foreman-reset-deliver", "foreman-reset-reconcile", "close-member"}
            lock = nullcontext() if readonly or separate_owner else state_lock(retrospective.canonical_state(_state_path(args)))
            with lock:
                retro_lock = retrospective.lock(_state_path(args)) if not readonly and args.command in {"apply", "start-judge", "relaunch-worker", "retro-record"} else nullcontext()
                with retro_lock:
                    payload, failure = COMMANDS[args.command](args, client=client, warn=warn, trace=trace)
    except ForemanError as exc:
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
