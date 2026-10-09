"""Assignment-scoped worker panes for config schema 7.

Worker-kind names are planning inputs, never live Herdr identities.  A plan
creates one stable identity per seat; apply creates an unfocused workspace and
starts it in the returned root pane. Close-member removes that pane after the
assessed outcome is accepted by the owner records.
"""

import json
import os
import re
import secrets
import shlex
import sys
import time

from .config import assignment_worker
from .composer import ensure_ready, startup_pending_error
from .errors import ForemanError, HerdrError, StartShellNotReadyError, UsageError, owner_recovery
from .herdr import READY_STATES, error_code, format_argv, scrub_for_trace
from .launch import holds_initializing_shell, holds_only_shell, require_empty_shell, start_worker, verify_running
from . import probe_recovery, runnable
from .tiers import launch_flags, worker_launch_args

MAX_AGENT_NAME = 32
_SAFE = re.compile(r"[^a-z0-9-]+")

#: Freshly created workspace root panes may still hold shell-initialization subprocesses.
#: Poll only owner-created panes, without input; existing panes keep their
#: one-shot occupancy refusal. The same shell must survive every observation.
FRESH_SHELL_POLL_ATTEMPTS = 30
FRESH_SHELL_POLL_INTERVAL = 0.2
#: A sole-shell read can precede a later startup child. Confirm consecutive
#: reads before preflight. Late initialization stays inside the fresh owner.
FRESH_SHELL_READY_READS = 2

#: A failure read is limited to the original disposable pane, not scrollback
#: or assignment history. The shared redactor bounds its retained text later.
PROBE_FAILURE_READ_LINES = 16
PROBE_HISTORY_BYTES = 1200


def _fresh_shell_info(client, pane, expected_shell=None, *, allow_absent_foreground=False):
    """Read a fresh pane's process evidence, rejecting malformed/replaced shells.

    Diagnostics carry PID shapes alone, never argv, cmdline or environment.
    Herdr transport failures remain failures, not transient occupied reads.
    """
    try:
        info = client.pane_process_info(pane)
    except HerdrError as exc:
        raise HerdrError(
            "Cannot read fresh pane {}'s shell process information; inspect Herdr's process-info command "
            "and retry the spawn after restoring that read. Nothing was started.".format(pane),
            {"pane": pane, "expected_shell_pid": expected_shell},
        ) from exc
    shell = info.get("shell_pid") if isinstance(info, dict) else None
    foreground = info.get("foreground_processes") if isinstance(info, dict) else None
    # Herdr omits an empty process vector. Only owner-created fresh-pane
    # readiness polling tolerates that absence; it never proves readiness.
    absent_foreground = isinstance(info, dict) and "foreground_processes" not in info
    pids = [row.get("pid") if isinstance(row, dict) else None for row in foreground] if isinstance(foreground, list) else None
    valid_pid = lambda pid: isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
    evidence = {"pane": pane, "shell_pid": shell if valid_pid(shell) else None,
                "foreground_pids": [pid if valid_pid(pid) else None for pid in pids] if pids is not None else None}
    if (not valid_pid(shell) or (pids is None and not (allow_absent_foreground and absent_foreground))
            or any(not valid_pid(pid) for pid in (pids or []))
            or (isinstance(info, dict) and info.get("pane_id", pane) != pane)):
        raise HerdrError(
            "Fresh pane {} returned malformed shell/foreground process evidence; inspect `herdr pane "
            "process-info --pane {}` and update Herdr before retrying. Nothing was started.".format(pane, pane),
            evidence,
        )
    if expected_shell is not None and shell != expected_shell:
        raise HerdrError(
            "Fresh pane {}'s shell changed from PID {} to {}; inspect its startup and retry with a new pane. "
            "Nothing was started.".format(pane, expected_shell, shell),
            {**evidence, "expected_shell_pid": expected_shell},
        )
    return info, evidence


def _await_fresh_shell(client, pane, sleep):
    """Confirm the same sole shell across reads after owner workspace_create only."""
    shell = None
    ready_reads = 0
    evidence = {}
    for attempt in range(1, FRESH_SHELL_POLL_ATTEMPTS + 1):
        info, evidence = _fresh_shell_info(client, pane, shell, allow_absent_foreground=True)
        shell = info["shell_pid"]
        ready_reads = ready_reads + 1 if holds_only_shell(info) else 0
        if ready_reads >= FRESH_SHELL_READY_READS:
            return shell
        if attempt < FRESH_SHELL_POLL_ATTEMPTS:
            sleep(FRESH_SHELL_POLL_INTERVAL)
    raise HerdrError(
        "Fresh pane {} did not settle to its shell alone after {} process reads (shell PID {}, foreground "
        "PIDs {}); inspect the shell startup or extra process, then retry the spawn. Nothing was started.".format(
            pane, FRESH_SHELL_POLL_ATTEMPTS, shell, evidence.get("foreground_pids")),
        {**evidence, "attempts": FRESH_SHELL_POLL_ATTEMPTS,
         "ready_reads": ready_reads, "required_ready_reads": FRESH_SHELL_READY_READS},
    )


def _start_fresh_worker(client, worker, pane, tier, shell, before_start, sleep):
    """Bound late login-shell forks and Herdr's atomic pre-input busy refusal.

    HerdrClient requires its running-server dependency floor before trusting
    agent_pane_busy as a pre-registration, pre-input refusal. No other failure is
    retried: startup timeout, transport failure and unknown readiness may
    already have sent input. Every retry rechecks the same root and authority.
    """
    evidence = {"pane": pane, "shell_pid": shell}
    native_error = None
    for attempt in range(1, FRESH_SHELL_POLL_ATTEMPTS + 1):
        if attempt > 1:
            info, evidence = _fresh_shell_info(client, pane, shell, allow_absent_foreground=True)
            if not holds_only_shell(info):
                if "foreground_processes" in info and not holds_initializing_shell(info):
                    require_empty_shell(client, pane, info)
                if attempt < FRESH_SHELL_POLL_ATTEMPTS:
                    sleep(FRESH_SHELL_POLL_INTERVAL)
                continue
        try:
            if before_start is not None:
                before_start(pane)
        except StartShellNotReadyError as exc:
            if exc.details.get("pane_id") != pane or exc.details.get("shell_pid") != shell:
                raise
            evidence = {"pane": pane, "shell_pid": shell,
                        "foreground_pids": exc.details.get("foreground_pids")}
        else:
            info, evidence = _fresh_shell_info(client, pane, shell, allow_absent_foreground=True)
            if not holds_only_shell(info):
                if "foreground_processes" in info and not holds_initializing_shell(info):
                    require_empty_shell(client, pane, info)
            else:
                try:
                    start_worker(client, worker, pane, tier, owned_fresh=True)
                except HerdrError as exc:
                    if error_code(exc) == "agent_not_ready":
                        live = client.agent_get(worker.name)
                        if (live.get("pane_id") == pane and live.get("name") == worker.name
                                and live.get("agent") == worker.kind and live.get("agent_status") == "blocked"):
                            # A known first-start refusal can leave its native
                            # dialog alive. Prove that exact seat, never launch
                            # again or grant it readiness for assignment input.
                            verify_running(client, worker, pane, tier)
                            return
                    if error_code(exc) != "agent_pane_busy":
                        raise
                    native_error = error_code(exc)
                    client.require_start_retry_compatibility()
                else:
                    return
        if attempt < FRESH_SHELL_POLL_ATTEMPTS:
            sleep(FRESH_SHELL_POLL_INTERVAL)
    raise HerdrError(
        "Fresh pane {} did not settle through preflight and native start after {} attempts; inspect "
        "its shell startup or Herdr's available-shell check, then retry the owner spawn. No worker input "
        "was sent.".format(pane, FRESH_SHELL_POLL_ATTEMPTS),
        {**evidence, "attempts": FRESH_SHELL_POLL_ATTEMPTS, "error_code": native_error},
    )


def identity(role, token=None):
    """Return a readable, Herdr-safe, plan-bound worker identity."""
    stem = _SAFE.sub("-", role.lower()).strip("-") or "worker"
    suffix = token or secrets.token_hex(5)
    return "{}-{}".format(stem[: MAX_AGENT_NAME - len(suffix) - 1], suffix)


def identities(roles):
    """Allocate collision-free identities for all seats in one plan."""
    result, used = {}, set()
    for role in roles:
        name = identity(role)
        while name in used:
            name = identity(role)
        result[role] = name
        used.add(name)
    return result


def materialize(assignments, worker_kinds, templates):
    """Resolve plan identities to cloned config templates."""
    if not isinstance(worker_kinds, dict) or set(worker_kinds) != set(assignments):
        raise UsageError("Assignment-scoped plans need one worker_kinds entry for every assigned seat; re-run `{}`.".format(
            runnable.command("plan")), {})
    by_kind = {worker.name: worker for worker in templates}
    live = {}
    for role, name in assignments.items():
        kind = worker_kinds.get(role)
        if not isinstance(kind, str) or kind not in by_kind:
            raise UsageError("Plan seat {!r} names unknown worker kind {!r}; replan from the current schema-7 config.".format(role, kind), {})
        if not by_kind[kind].assignment_scoped:
            raise UsageError("Plan mixes assignment-scoped identities with a standing-worker config; replan after choosing one config schema.", {})
        live[name] = assignment_worker(by_kind[kind], name)
    return live


def spawn_commands(client, worker, tier, *, cwd=None):
    """The exact workspace-create/start commands a live spawn executes."""
    pane = "ROOT-PANE-ID-RETURNED-BY-WORKSPACE-CREATE"
    create = client.argv_workspace_create(cwd=cwd or os.getcwd(), label=worker.name, focus=False)
    flags = worker_launch_args(worker.kind, worker.launch_args) + launch_flags(worker.kind, tier)
    return [create, client.argv_agent_start(worker.name, worker.kind, pane, flags)]


def spawn(client, worker, tier, *, cwd=None, history=None, before_start=None, sleep=time.sleep):
    """Create an unfocused workspace, prove a first-launch shell, start one worker, and prove its tier."""
    if not isinstance(history, (list, tuple)):
        raise UsageError(
            "Assignment spawn needs the owner's assignment history to prove this fresh identity has never held an earlier assignment.",
            {"agent": worker.name},
        )
    if any(isinstance(row, dict) and row.get("agent") == worker.name for row in history):
        raise UsageError(
            "Assignment identity {!r} already appears in owner history; allocate a fresh identity before spawning it.".format(
                worker.name),
            {"agent": worker.name},
        )
    pane = client.workspace_create(cwd=cwd or os.getcwd(), label=worker.name, focus=False)
    completed = False
    try:
        # This is the lifecycle's first-launch carve-out: live process evidence
        # proves the newly-created pane holds only its shell, while the owner
        # history proof above establishes that no prior assignment can carry
        # context under this identity.
        shell = _await_fresh_shell(client, pane, sleep)
        _start_fresh_worker(client, worker, pane, tier, shell, before_start, sleep)
        verify_running(client, worker, pane, tier)
        completed = True
    finally:
        if not completed:
            primary = sys.exc_info()[1]
            try:
                client.pane_close(pane)
            except ForemanError as cleanup:
                action = "Cleanup also failed: {}. Close the pane with `{}` before retrying.".format(
                    cleanup, format_argv(client.argv_pane_close(pane)))
                if isinstance(primary, ForemanError):
                    raise HerdrError(
                        "Worker spawn failed for {} in pane {}: {} {}".format(
                            worker.name, pane, primary, action),
                        {"agent": worker.name, "pane_id": pane,
                         "primary_error": primary.to_dict(), "cleanup_error": cleanup.to_dict()},
                    ) from primary
                # An interrupt or unexpected exception must remain the active
                # failure. Attach the cleanup repair without replacing it.
                if primary is not None and hasattr(primary, "add_note"):
                    primary.add_note(action)
    return pane


def close(client, agent, pane, before_close=None):
    """Close one assignment pane, accepting a replay only when its agent is absent."""
    try:
        live = client.agent_get(agent)
    except HerdrError as exc:
        if error_code(exc) == "agent_not_found":
            if before_close is not None:
                before_close()
            try:
                client.pane_close(pane)
            except HerdrError as pane_exc:
                if error_code(pane_exc) == "pane_not_found":
                    return {"pane_id": pane, "agent": agent, "closed": True, "replayed": True}
                raise
            return {"pane_id": pane, "agent": agent, "closed": True, "replayed": False}
        raise
    if live.get("pane_id") != pane:
        raise HerdrError(
            "Assignment {} moved from recorded pane {} to {}; refusing to close an unbound pane.".format(
                agent, pane, live.get("pane_id")),
            {"agent": agent, "recorded_pane": pane, "live_pane": live.get("pane_id")},
        )
    if before_close is not None:
        before_close()
    client.pane_close(pane)
    try:
        client.agent_get(agent)
    except HerdrError as exc:
        if error_code(exc) == "agent_not_found":
            return {"pane_id": pane, "agent": agent, "closed": True, "replayed": False}
        raise
    raise HerdrError(
        "Pane {} was closed but assignment {} still appears in Herdr; wait for removal, then retry `{}`.".format(
            pane, agent, runnable.command("close-member")),
        {"agent": agent, "pane_id": pane},
    )


def rendered_commands(client, worker, tier, *, cwd=None):
    return [{"argv": argv, "shell": format_argv(argv)} for argv in spawn_commands(client, worker, tier, cwd=cwd)]


def _prepare_fresh_probe(client, worker, pane, tier, *, sleep=time.sleep, warn=None):
    """Prove the owned first-start composer before any usage-command input."""
    from .probe import resolve_status
    original = None
    def observe():
        nonlocal original
        live = client.agent_get(worker.name)
        proof = verify_running(client, worker, pane, tier)
        identity = (live.get("pane_id"), live.get("agent_session"), proof)
        if (live.get("pane_id") != pane or live.get("name") != worker.name or live.get("agent") != worker.kind
                or type(proof.get("pid")) is not int or proof["pid"] <= 0
                or (original is not None and original != identity)):
            raise HerdrError("Fresh probe identity changed; no usage command was sent.", {"agent": worker.name, "pane_id": pane})
        original = identity
        status, _source = resolve_status(client, worker, live.get("agent_status"), warn=warn)
        if status == "blocked":
            raise startup_pending_error(worker, pane)
        if status not in READY_STATES:
            raise HerdrError("Fresh probe is not idle/done; no usage command was sent.", {"agent": worker.name, "pane_id": pane})
        return identity
    try:
        ensure_ready(client, worker, pane, startup_observe=observe, sleep=sleep, warn=warn)
    except HerdrError as exc:
        setattr(exc, "_probe_startup_observation", original)
        if exc.details.get("failure_kind") == "startup_dialog_pending":
            exc.details["startup_observation"] = original
        raise
    return original


def _probe_visible_read(client, worker, pane, tier, original, observed_text=None, *, lines=PROBE_FAILURE_READ_LINES):
    """Read only a still-bound disposable probe; authorize no input/cleanup."""
    if (not isinstance(original, tuple) or len(original) != 3 or original[0] != pane
            or not isinstance(original[2], dict) or type(original[2].get("pid")) is not int
            or original[2]["pid"] <= 0):
        raise HerdrError("original startup binding is missing", {})

    def same_identity():
        live = client.agent_get(worker.name)
        return (live.get("name") == worker.name and live.get("agent") == worker.kind
                and (live.get("pane_id"), live.get("agent_session"),
                     verify_running(client, worker, pane, tier)) == original)
    if not same_identity():
        raise HerdrError("original probe identity changed", {})
    text = (observed_text if observed_text is not None and worker.usage_read_source == "visible"
            else client.pane_read(pane, lines=lines))
    if not same_identity():
        raise HerdrError("original probe identity changed during the read", {})
    return text


def _probe_failure_view(client, worker, pane, tier, original, observed_text=None):
    """Keep failed read evidence explicit without replacing the usage cause."""
    try:
        return _probe_visible_read(client, worker, pane, tier, original, observed_text)
    except (HerdrError, UsageError) as exc:
        return "Visible evidence unavailable: bound probe observation failed: {}".format(exc.message)


def _probe_usage_recovery(record, cleanup_error, closure, client, pane, operation):
    """Replace retired-target instructions with one currently executable route."""
    from .measure import snapshot_error
    historical = record["error"]
    closed = isinstance(closure, dict) and closure.get("closed") is True and cleanup_error is None
    if closed:
        action = runnable.command(operation)
        condition = ("The disposable probe is retired; do not inspect, clear or submit input to its old identity. "
                     "Repair the observed native/configuration cause, then use normal measure to create a fresh owned probe.")
    else:
        action = format_argv(client.argv_pane_process_info(pane))
        condition = ("Probe cleanup is unproved. Preserve the surface and finish identity-bound owner cleanup "
                     "before another probe; this read-only inspection authorizes no input or forced close.")
    error = snapshot_error(cleanup_error) if cleanup_error is not None else dict(historical)
    if historical.get("details", {}).get("pending_cli_update") is True:
        error["details"] = {**error.get("details", {}), "pending_cli_update": True}
    error["message"] = scrub_for_trace(
        "Disposable usage measurement failed; capacity remains unknown. Next owner operation: `{}`. {}{} "
        "Historical diagnostic (not current instructions): {}".format(
            action, condition,
            " Cleanup diagnostic: " + json.dumps(scrub_for_trace(cleanup_error.message, cap=256), ensure_ascii=False)
                if cleanup_error is not None else "",
            json.dumps(scrub_for_trace(historical["message"], cap=PROBE_HISTORY_BYTES), ensure_ascii=False)))
    record["error"] = error


def measure_worker_kinds(client, templates, measured_at, *, state_path=None, config_path=None, **options):
    """Measure one probe per billing window; retain pre-input startup dialogs."""
    from .billing import tier_billing
    from .measure import DEFAULT_READ_LINES, MEASURE_SCHEMA_VERSION, _capture_failure, measure, snapshot_error

    groups = {}
    for template in templates:
        key = (("shared", template.window_group) if template.window_group
               else ("worker", template.name))
        groups.setdefault(key, []).append(template)
    records, failures = {}, []
    for _group_key, members in groups.items():
        template = members[0]
        group = template.window_group
        probe = assignment_worker(template, identity("probe-" + template.name))
        tier = template.tiers.get("coordination")
        pane = None
        record = None
        pending = False
        prior = None
        startup = True
        startup_error = None
        closure = None
        cleanup_error = None
        usage_failure = None
        operation = "measure --agent " + shlex.quote(template.name)
        if state_path is not None:
            operation += " --state " + shlex.quote(str(state_path))
        if config_path is not None:
            operation += " --config " + shlex.quote(str(config_path))
        binary = getattr(client, "binary", None)
        if isinstance(binary, str) and binary:
            operation += " --herdr-bin " + shlex.quote(binary)
        try:
            prior = probe_recovery.pending(state_path, template) if state_path is not None else None
            if prior is not None:
                raise probe_recovery.diagnostic(state_path, prior, herdr_bin=binary)
            if not isinstance(tier, dict):
                raise UsageError("Worker kind {!r} has no coordination tier for its disposable usage probe.".format(template.name), {})
            pane = spawn(client, probe, tier, history=(), sleep=options.get("sleep", time.sleep))
            original = _prepare_fresh_probe(client, probe, pane, tier, sleep=options.get("sleep", time.sleep), warn=options.get("warn"))
            startup = False
            snapshot = measure(client, [probe], measured_at,
                failure_capture=lambda text: _probe_failure_view(client, probe, pane, tier, original, text),
                owned_visible_read=lambda: _probe_visible_read(client, probe, pane, tier, original,
                    lines=options.get("read_lines", DEFAULT_READ_LINES)), **options)
            record = dict(snapshot["agents"][probe.name])
            record.pop("tier_billing", None)
            if "error" in record:
                usage_failure = dict(record)
        except (HerdrError, UsageError) as exc:
            if startup and prior is None:
                startup_error = exc
            needs_retention = (pane is not None and exc.details.get("failure_kind") == "startup_dialog_pending"
                       and exc.details.get("agent") == probe.name and exc.details.get("pane_id") == pane)
            if needs_retention:
                row = probe_recovery.retain(state_path, template, probe, pane, tier,
                    exc.details.get("startup_observation"), measured_at, config_path=config_path)
                pending = True
                exc = probe_recovery.diagnostic(state_path, row, herdr_bin=binary)
            elif startup and pane is not None:
                _capture_failure(exc, lambda text: _probe_failure_view(client, probe, pane, tier,
                    getattr(exc, "_probe_startup_observation", None), text))
            record = {
                "kind": template.kind, "state": None, "herdr_state": None,
                "state_source": None, "windows": None, "credits": None,
                "plan": None, "headroom_pct": None, "window_group": group,
                "skipped": False, "error": snapshot_error(exc),
            }
            if not startup:
                usage_failure = dict(record)
        finally:
            if pane is not None and not pending:
                primary = sys.exc_info()[1]
                try:
                    closure = close(client, probe.name, pane)
                except HerdrError as exc:
                    cleanup_error = exc
                    if primary is not None:
                        action = "Probe cleanup also failed for pane {}: {}. Close it with `{}` before measuring again.".format(
                            pane, exc, format_argv(client.argv_pane_close(pane)))
                        if hasattr(primary, "add_note"):
                            primary.add_note(action)
                    else:
                        record = {
                            "kind": template.kind, "state": None, "herdr_state": None,
                            "state_source": None, "windows": None, "credits": None,
                            "plan": None, "headroom_pct": None, "window_group": group,
                            "skipped": False, "error": snapshot_error(exc),
                        }
        if usage_failure is not None:
            _probe_usage_recovery(usage_failure, cleanup_error, closure, client, pane, operation)
            record = usage_failure
        if startup_error is not None and not pending:
            closed = isinstance(closure, dict) and closure.get("closed") is True
            condition = ("The owner closed its unused probe; no usage command was sent. Restore the worker-kind configuration/native startup evidence, then repeat normal measure."
                if closed else "No usage command was sent. Preserve and inspect the actual startup/cleanup evidence; restore the worker-kind configuration and finish owned cleanup before repeating normal measure.")
            historical = startup_error.details.get("failure_message", startup_error.message)
            failure = cleanup_error or startup_error
            recovered = owner_recovery(failure,
                "probe_cleanup_unproved" if cleanup_error else "probe_startup_unproved",
                runnable.command(operation), condition, outcome="retryable" if closed else "blocked")
            recovered.message = "Next owner operation: `{}`. {}{} Historical diagnostic: {}".format(
                runnable.command(operation), condition,
                " Cleanup diagnostic: " + json.dumps(scrub_for_trace(cleanup_error.message, cap=256), ensure_ascii=False)
                    if cleanup_error is not None else "",
                json.dumps(scrub_for_trace(historical, cap=PROBE_HISTORY_BYTES), ensure_ascii=False))
            recovered.args = (recovered.message,)
            record["error"] = snapshot_error(recovered)
        for member in members:
            copied = {**record, "kind": member.kind, "window_group": member.window_group,
                      "pane_id": None, "tier_billing": tier_billing(member.tiers)}
            records[member.name] = copied
            if "error" in copied:
                failures.append(member.name)
    return {"schema_version": MEASURE_SCHEMA_VERSION, "measured_at": measured_at,
            "agents": records, "failed_agents": failures}
