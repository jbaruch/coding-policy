"""Verified worker starts and fresh-round relaunches.

Herdr 0.8.2's bundled schema names agent.start's {agent, argv} result and
pane.process_info's foreground process records. The installed CLI returns
JSON for both. Older builds without that contract fail before dispatch.
https://herdr.dev/docs/socket-api/

Relaunch terminates only the identified, idle foreground agent after an empty
composer check, waits for its shell AND for Herdr to release the old agent
name, then starts the selected tier. It never terminates a working/blocked
agent or guesses a PID from a transcript.

Herdr keeps the name reserved briefly after the process exits, so a start
issued the moment the shell returns refuses with `agent_name_taken` while the
seat reads Idle -- a fresh judge dispatch lost an attempt to exactly that
(#379). The reservation is waited out, and a refusal that slips through is
retried a bounded number of times, each time re-proving the pane and the name.
"""

import time
from pathlib import PurePath

from . import runnable
from .composer import ensure_ready
from .errors import AgentBusyError, HerdrError
from .herdr import READY_STATES, error_code
from .restoration import NAME_RELEASED, NAME_TAKEN, name_state
from .tiers import launch_flags, verify_argv, verify_worker_permissions, worker_launch_args

SHELL_POLL_ATTEMPTS = 30
SHELL_POLL_INTERVAL = 0.2

#: How many times to re-read the released name before giving up, and how long
#: to wait between reads: 15 s in total, matching the restoration gate. A name
#: Herdr has not released by then needs a human's eyes, not a longer loop.
NAME_POLL_ATTEMPTS = 60
NAME_POLL_INTERVAL = 0.25

#: How many `agent_name_taken` refusals may each be followed by a fresh
#: release wait and another start. Every other start failure ends the relaunch:
#: Herdr may already have started the process, and a second start duplicates it.
NAME_TAKEN_RETRIES = 2


def foreground_agent(client, pane, kind):
    info = client.pane_process_info(pane)
    matches = []
    for process in info.get("foreground_processes", []):
        if not isinstance(process, dict):
            raise HerdrError("Malformed foreground-process record; inspect the pane.", {})
        argv = process.get("argv")
        name = process.get("name")
        if name == kind or (isinstance(argv, list) and argv and isinstance(argv[0], str) and PurePath(argv[0]).name == kind):
            if argv is None:
                argv = client.process_args(process.get("pid"))
            matches.append({**process, "argv": argv})
    if len(matches) != 1:
        raise HerdrError("Cannot identify one {} foreground process in {}; inspect the pane before relaunch.".format(kind, pane), {})
    return matches[0]


def holds_only_shell(info):
    """True when a `pane_process_info` record's sole foreground process is its shell."""
    shell = info.get("shell_pid") if isinstance(info, dict) else None
    foreground = info.get("foreground_processes") if isinstance(info, dict) else None
    return (isinstance(shell, int) and not isinstance(shell, bool) and shell > 0
            and isinstance(foreground, list) and len(foreground) == 1
            and isinstance(foreground[0], dict) and foreground[0].get("pid") == shell)


def require_empty_shell(client, pane, info=None):
    """Refuse a pane whose foreground holds anything but its shell.

    `info` is an earlier `pane_process_info` read to judge; without one the
    pane is read now.
    """
    if not holds_only_shell(client.pane_process_info(pane) if info is None else info):
        raise HerdrError(
            "Pane {} is occupied: its foreground holds something other than its shell. Name an empty Herdr shell "
            "pane; nothing was started.".format(pane), {"pane": pane})


def verify_running(client, agent, pane, tier):
    launch_args = worker_launch_args(agent.kind, agent.launch_args)
    process = foreground_agent(client, pane, agent.kind)
    proof = verify_argv(agent.kind, tier, process["argv"], launch_args)
    return {**proof, "source": "process_argv", "pid": process["pid"], "pane_id": pane}


def configured_running_tier(client, agent, pane):
    """Return the configured tier proved by this pane's live process argv.

    A maintenance relaunch has no plan from which to select a tier. Preserve
    the exact configured model/effort already running instead of silently
    choosing a row or accepting an operator override.
    """
    launch_args = worker_launch_args(agent.kind, agent.launch_args)
    process = foreground_agent(client, pane, agent.kind)
    matches = []
    for tier in agent.tiers.values():
        try:
            proof = verify_argv(agent.kind, tier, process["argv"], launch_args)
        except HerdrError:
            continue
        key = (proof["model"], proof["effort"])
        if all((row[0]["model"], row[0].get("effort")) != key for row in matches):
            matches.append((dict(tier), proof))
    if len(matches) != 1:
        raise HerdrError(
            "Worker {}'s live argv does not prove one configured tier; restore a configured model/effort before relaunching it.".format(agent.name),
            {"agent": agent.name, "pane": pane, "configured_matches": len(matches)},
        )
    tier, proof = matches[0]
    return tier, {**proof, "source": "process_argv", "pid": process["pid"], "pane_id": pane}


def verify_running_permissions(client, agent, pane):
    worker_launch_args(agent.kind, agent.launch_args)
    process = foreground_agent(client, pane, agent.kind)
    verify_worker_permissions(agent.kind, process["argv"])


def _start_seat(client, name, kind, pane, tier, launch_args):
    """Start one named seat on `tier` and prove its launch argv."""
    result = client.agent_start(name, kind, pane, list(launch_args) + launch_flags(kind, tier))
    info = result.get("agent") if isinstance(result, dict) else None
    if not isinstance(info, dict) or (
        info.get("pane_id") != pane or info.get("name") != name
        or info.get("agent") != kind or info.get("agent_status") not in READY_STATES
    ):
        raise HerdrError("Started worker identity or readiness differs from the requested pane and kind; no brief was sent.", {})
    proof = verify_argv(kind, tier, result.get("argv"), launch_args)
    return {**proof, "pane_id": pane}


def start_worker(client, agent, pane, tier, before_start=None, sleep=time.sleep):
    launch_args = worker_launch_args(agent.kind, agent.launch_args)
    # An unsupported kind refuses here, before the retrospective hook runs.
    launch_flags(agent.kind, tier)
    if before_start is not None:
        before_start()
    return _start_seat(client, agent.name, agent.kind, pane, tier, launch_args)


def start_foreman(client, seat, pane, tier):
    """Start the foreman seat in a shell pane on its selected tier.

    `seat` is a `config.Foreman` and `tier` the row `tiers.select_tier`
    resolved for its coordination round. Its launch options are the
    operator's (`tiers.parse_launch_args`); the worker YOLO requirement does
    not apply.

    A fresh start needs a pane holding only its shell, read before the name
    check and again immediately before the start; an occupied pane refuses.
    A retry is safe: when Herdr already holds the seat's name on `pane` with
    the seat's kind, nothing is started, the live foreground argv must carry
    `tier`, and the proof returns with `replayed: true`. A name bound to
    another pane or kind, or a live tier other than `tier`, refuses.
    """
    occupant = client.pane_process_info(pane)
    try:
        record = client.agent_get(seat.agent)
    except HerdrError as exc:
        if error_code(exc) != NAME_RELEASED:
            raise
        record = None
    if record is None:
        require_empty_shell(client, pane, occupant)
        # Re-read immediately before the start: the pane may have been taken
        # since the first read.
        require_empty_shell(client, pane)
        return {**_start_seat(client, seat.agent, seat.kind, pane, tier, list(seat.launch_args)), "replayed": False}
    if record.get("pane_id") != pane or record.get("agent") != seat.kind:
        raise HerdrError(
            "The foreman name {!r} is already held by a {} agent in pane {!r}, not a {} agent in {!r}; nothing was "
            "started. Stop that agent or name the pane it runs in, then retry.".format(
                seat.agent, record.get("agent"), record.get("pane_id"), seat.kind, pane),
            {"agent": seat.agent, "pane": pane, "bound_pane": record.get("pane_id"), "bound_kind": record.get("agent")})
    return {**verify_foreman(client, seat, pane, tier), "replayed": True}


def verify_foreman(client, seat, pane, tier):
    """Prove the live foreman in `pane` runs the selected tier, from its foreground argv.

    The pane must be the one Herdr binds to the seat's agent name, with the
    seat's kind: another agent's pane running the same tier proves nothing
    about the foreman.
    """
    try:
        record = client.agent_get(seat.agent)
    except HerdrError as exc:
        if error_code(exc) != NAME_RELEASED:
            raise
        raise HerdrError(
            "Herdr holds no agent named {!r}, so pane {!r} is not the foreman's seat. Start the foreman from "
            "another shell with `{}`, then re-run the preflight.".format(
                seat.agent, pane, runnable.command("start-foreman --pane <empty-shell-pane>")),
            {"agent": seat.agent, "pane": pane}) from exc
    if not isinstance(record, dict) or record.get("pane_id") != pane or record.get("agent") != seat.kind:
        bound_pane = record.get("pane_id") if isinstance(record, dict) else None
        bound_kind = record.get("agent") if isinstance(record, dict) else None
        raise HerdrError(
            "The foreman name {!r} is bound to a {} agent in pane {!r}, not a {} agent in {!r}; this pane is not "
            "the foreman's seat. Run the foreman from its own seat, or stop the agent holding that name and "
            "start the foreman with `{}`.".format(
                seat.agent, bound_kind, bound_pane, seat.kind, pane,
                runnable.command("start-foreman --pane <empty-shell-pane>")),
            {"agent": seat.agent, "pane": pane, "bound_pane": bound_pane, "bound_kind": bound_kind})
    process = foreground_agent(client, pane, seat.kind)
    proof = verify_argv(seat.kind, tier, process["argv"], list(seat.launch_args))
    return {**proof, "source": "process_argv", "pid": process["pid"], "pane_id": pane}


def await_name_release(client, name, pane, sleep=time.sleep):
    """Wait, bounded, for Herdr to release the stopped worker's name.

    A name bound to ANOTHER pane is a refusal, never something to wait out.
    """
    state = None
    for attempt in range(1, NAME_POLL_ATTEMPTS + 1):
        state = name_state(client, name, pane)
        if state == "released":
            return attempt
        if attempt < NAME_POLL_ATTEMPTS:
            sleep(NAME_POLL_INTERVAL)
    raise HerdrError(
        "Herdr still reserves the agent name {!r} after {} reads; the pane returned to its shell but the seat cannot be re-started. Inspect `herdr agent list` and the pane by hand. No start or brief was sent.".format(name, NAME_POLL_ATTEMPTS),
        {"agent": name, "pane": pane, "attempts": NAME_POLL_ATTEMPTS},
    )


def start_after_release(client, agent, pane, tier, sleep=time.sleep, before_start=None):
    """Start the seat once the name reads released, retrying a reservation.

    A refusal that slips through the wait is the reservation lapsing late, and
    it started no process. Every other failure ends the relaunch untried.
    """
    retries = 0
    while True:
        await_name_release(client, agent.name, pane, sleep=sleep)
        try:
            return start_worker(client, agent, pane, tier, before_start=before_start, sleep=sleep)
        except HerdrError as exc:
            if error_code(exc) != NAME_TAKEN or retries >= NAME_TAKEN_RETRIES:
                raise
            retries += 1


def restart_worker(client, agent, pane, tier, sleep=time.sleep, before_transition=None, before_start=None,
                   expected_process=None, recovery_command=None):
    worker_launch_args(agent.kind, agent.launch_args)
    rerun = recovery_command or runnable.command("apply")
    if not isinstance(pane, str) or not pane or not agent.composer_glyph:
        raise HerdrError("Tier relaunch needs a live pane and configured composer glyph; fix the agent config.", {})
    info = client.agent_get(agent.name)
    if (info.get("agent_status") not in READY_STATES or info.get("pane_id") != pane
            or info.get("name") != agent.name or info.get("agent") != agent.kind
            or not isinstance(info.get("terminal_id"), str) or not info["terminal_id"]):
        raise AgentBusyError("Worker is no longer idle in the planned pane; wait before relaunch.", {})
    ensure_ready(client, agent, pane_id=pane, sleep=sleep)
    process = foreground_agent(client, pane, agent.kind)
    if expected_process is not None and (
        process.get("pid") != expected_process.get("pid")
        or process.get("argv") != expected_process.get("argv")
    ):
        raise HerdrError(
            "Worker process changed after its configured tier was proved; no process was terminated. "
            "Inspect pane {}, wait for {} to stay idle, then rerun `{}` so its tier is proved "
            "again.".format(pane, agent.name, rerun),
            {"agent": agent.name, "pane": pane},
        )
    # Check native occupant and readiness before any transition callback.
    fresh = client.agent_get(agent.name)
    if (fresh.get("agent_status") not in READY_STATES or fresh.get("pane_id") != pane
            or fresh.get("terminal_id") != info.get("terminal_id")
            or fresh.get("agent_session") != info.get("agent_session")):
        raise AgentBusyError(
            "Worker changed during relaunch checks; no process was terminated. Inspect pane {}, wait "
            "for {} to stay idle, then rerun `{}`.".format(pane, agent.name, rerun),
            {"agent": agent.name, "pane": pane},
        )
    if before_transition is not None:
        before_transition()
    # Retrospective I/O can take long enough for the worker to change. Bind the
    # destructive operation to the same native occupant, PID, and argv after
    # that callback, immediately before termination.
    final = client.agent_get(agent.name)
    if (final.get("agent_status") not in READY_STATES or final.get("pane_id") != pane
            or final.get("terminal_id") != info.get("terminal_id")
            or final.get("agent_session") != info.get("agent_session")):
        raise AgentBusyError(
            "Worker changed during the retrospective relaunch check; no process was terminated. Wait "
            "for {} to become idle, refresh retrospective coverage if requested, then rerun "
            "`{}`.".format(agent.name, rerun),
            {"agent": agent.name, "pane": pane},
        )
    ensure_ready(client, agent, pane_id=pane, sleep=sleep)
    final = client.agent_get(agent.name)
    if (final.get("agent_status") not in READY_STATES or final.get("pane_id") != pane
            or final.get("terminal_id") != info.get("terminal_id")
            or final.get("agent_session") != info.get("agent_session")):
        raise AgentBusyError(
            "Worker changed during the final composer check; no process was terminated. Inspect pane "
            "{}, clear or wait out the composer or session change, then rerun `{}` only once {} is "
            "idle.".format(pane, rerun, agent.name),
            {"agent": agent.name, "pane": pane},
        )
    final_process = foreground_agent(client, pane, agent.kind)
    if (final_process.get("pid") != process.get("pid")
            or final_process.get("argv") != process.get("argv")):
        raise HerdrError(
            "Foreground process changed during the retrospective relaunch check; no process was "
            "terminated. Inspect pane {}, confirm the intended process is stable, then rerun "
            "`{}`.".format(pane, rerun),
            {"agent": agent.name, "pane": pane},
        )
    client.terminate_process(final_process.get("pid"))
    for attempt in range(SHELL_POLL_ATTEMPTS):
        if holds_only_shell(client.pane_process_info(pane)):
            return start_after_release(client, agent, pane, tier, sleep=sleep, before_start=before_start)
        if attempt + 1 < SHELL_POLL_ATTEMPTS:
            sleep(SHELL_POLL_INTERVAL)
    raise HerdrError("Worker termination did not return the pane to its shell; inspect it before retrying. No start or brief was sent.", {})
