"""Assignment-scoped worker panes for config schema 7.

Worker-kind names are planning inputs, never live Herdr identities.  A plan
creates one stable identity per seat; apply materializes it in a new pane and
close-member removes that pane after the assessed outcome is accepted by the
owner records.
"""

import os
import re
import secrets

from .config import assignment_worker
from .errors import ForemanError, HerdrError, UsageError
from .herdr import error_code, format_argv
from .launch import verify_running
from . import runnable
from .tiers import launch_flags, worker_launch_args

MAX_AGENT_NAME = 32
_SAFE = re.compile(r"[^a-z0-9-]+")


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
    """The exact split/start commands a live spawn executes."""
    pane = "PANE-ID-RETURNED-BY-SPLIT"
    split = client.argv_pane_split(current=True, cwd=cwd or os.getcwd(), focus=False)
    flags = worker_launch_args(worker.kind, worker.launch_args) + launch_flags(worker.kind, tier)
    return [split, client.argv_agent_start(worker.name, worker.kind, pane, flags)]


def spawn(client, worker, tier, *, cwd=None):
    """Split from the foreman, start one worker, and prove its selected tier."""
    flags = worker_launch_args(worker.kind, worker.launch_args) + launch_flags(worker.kind, tier)
    pane = client.pane_split(current=True, cwd=cwd or os.getcwd(), focus=False)
    try:
        client.agent_start(worker.name, worker.kind, pane, flags)
        verify_running(client, worker, pane, tier)
    except ForemanError:
        try:
            client.pane_close(pane)
        except HerdrError:
            pass
        raise
    return pane


def close(client, agent, pane):
    """Close one assignment pane, accepting a replay only when its agent is absent."""
    try:
        live = client.agent_get(agent)
    except HerdrError as exc:
        if error_code(exc) == "agent_not_found":
            return {"pane_id": pane, "agent": agent, "closed": True, "replayed": True}
        raise
    if live.get("pane_id") != pane:
        raise HerdrError(
            "Assignment {} moved from recorded pane {} to {}; refusing to close an unbound pane.".format(
                agent, pane, live.get("pane_id")),
            {"agent": agent, "recorded_pane": pane, "live_pane": live.get("pane_id")},
        )
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


def measure_worker_kinds(client, templates, measured_at, **options):
    """Measure one disposable probe per billing window, then close every probe."""
    from .billing import tier_billing
    from .measure import MEASURE_SCHEMA_VERSION, measure, snapshot_error

    groups = {}
    for template in templates:
        groups.setdefault(template.window_group or template.name, []).append(template)
    records, failures = {}, []
    for group, members in groups.items():
        template = members[0]
        probe = assignment_worker(template, identity("probe-" + template.name))
        tier = template.tiers.get("coordination")
        pane = None
        record = None
        try:
            if not isinstance(tier, dict):
                raise UsageError("Worker kind {!r} has no coordination tier for its disposable usage probe.".format(template.name), {})
            pane = spawn(client, probe, tier)
            snapshot = measure(client, [probe], measured_at, **options)
            record = dict(snapshot["agents"][probe.name])
            record.pop("tier_billing", None)
        except (HerdrError, UsageError) as exc:
            record = {
                "kind": template.kind, "state": None, "herdr_state": None,
                "state_source": None, "windows": None, "credits": None,
                "plan": None, "headroom_pct": None, "window_group": group,
                "skipped": False, "error": snapshot_error(exc),
            }
        finally:
            if pane is not None:
                try:
                    close(client, probe.name, pane)
                except HerdrError as exc:
                    record = {
                        "kind": template.kind, "state": None, "herdr_state": None,
                        "state_source": None, "windows": None, "credits": None,
                        "plan": None, "headroom_pct": None, "window_group": group,
                        "skipped": False, "error": snapshot_error(exc),
                    }
        for member in members:
            copied = {**record, "kind": member.kind, "window_group": member.window_group,
                      "pane_id": None, "tier_billing": tier_billing(member.tiers)}
            records[member.name] = copied
            if "error" in copied:
                failures.append(member.name)
    return {"schema_version": MEASURE_SCHEMA_VERSION, "measured_at": measured_at,
            "agents": records, "failed_agents": failures}
