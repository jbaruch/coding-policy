"""Hand a role and its brief to an agent.

The hard rule this module enforces: teamlead never types into an agent that is
`working` or `blocked`. Statuses are checked for *every* target before the
first keystroke is sent, so a run that is going to be refused sends nothing at
all rather than half the assignments.

`working` from herdr is confirmed against the pane first (see
teamlead/probe.py) -- herdr's title-derived state goes stale and would
otherwise refuse a genuinely idle agent forever. `blocked` is never probed.

The clear command (`/clear`, `/new`) goes out by whichever mechanism the
agent's config names -- see SLASH_DELIVERIES in teamlead/herdr.py, because the
TUIs disagree about what a pasted slash command means. The assignment itself
IS a message, so it always goes through `agent prompt`.

Both are gated on the composer being empty (see teamlead/composer.py). Live,
an unsent `/new` sat in Codex's composer and the assignment was pasted onto
the end of it, so Codex received `/newNew assignment from the team lead...`
and rejected it. The assignment is never sent to an agent whose composer still
holds text.

`--dry-run` builds the same argv lists the live path would execute (the
builders live on the transport) and prints them without running anything.
"""

import os
import time

from .composer import COMPOSER_SETTLE_SEC, ensure_ready, send_command
from .errors import AgentBusyError, UsageError
from .herdr import (
    BUSY_STATES,
    DEFAULT_SETTLE_TIMEOUT_MS,
    SLASH_DELIVERY_TYPE,
    format_argv,
)
from .composer import COMPOSER_READ_LINES, COMPOSER_READ_SOURCE, checkable
from .probe import PROBE_READ_LINES, PROBE_READ_SOURCE, resolve_status, stderr_warn

APPLY_SCHEMA_VERSION = 1

#: States teamlead will type into. Anything else is refused, always.
SETTLE_STATES = ("idle", "done")

#: Stand-in for a pane id in `--dry-run`, which resolves no pane because it
#: makes no herdr calls.
PANE_ID_PLACEHOLDER = "PANE-ID-RESOLVED-AT-RUN-TIME"

ASSIGNMENT_TEMPLATE = (
    "New assignment from the team lead. Your role for this task is {role}. "
    "Read {common} in full, then read {brief} in full, and execute that brief "
    "exactly. Finish with the REPORT line it specifies."
)


def assignment_text(role, common_path, brief_path):
    """The exact prompt sent to an agent. Pure, so tests pin it byte for byte."""
    return ASSIGNMENT_TEMPLATE.format(
        role=role.upper(), common=common_path, brief=brief_path
    )


def normalize_assignments(payload):
    """Accept either `plan` output or a bare `{role: agent}` mapping."""
    if isinstance(payload, dict) and isinstance(payload.get("assignments"), dict):
        payload = payload["assignments"]
    if not isinstance(payload, dict) or not payload:
        raise UsageError(
            "--assignments needs a JSON object mapping roles to agent names, or "
            "the output of `teamlead plan` (which nests one under "
            "\"assignments\"). Got: {}.".format(type(payload).__name__),
            {},
        )
    for role, agent in payload.items():
        if not isinstance(agent, str) or not agent:
            raise UsageError(
                "--assignments maps role {!r} to {!r}; each role must map to an "
                "agent name string.".format(role, agent),
                {"role": role},
            )
    return dict(payload)


def resolve_paths(assignments, briefs, common):
    """Turn the brief/common inputs into absolute paths, or explain what is missing."""
    missing_briefs = [role for role in assignments if role not in briefs]
    if missing_briefs:
        raise UsageError(
            "No --brief given for role {} - pass --brief {}=/path/to/brief.md "
            "for every role in --assignments.".format(
                ", ".join(sorted(missing_briefs)), sorted(missing_briefs)[0]
            ),
            {"roles": sorted(missing_briefs)},
        )

    resolved = {"common": os.path.abspath(common)}
    unreadable = [] if os.path.isfile(resolved["common"]) else [resolved["common"]]
    for role in assignments:
        path = os.path.abspath(briefs[role])
        resolved[role] = path
        if not os.path.isfile(path):
            unreadable.append(path)
    if unreadable:
        raise UsageError(
            "These brief files do not exist: {} - create them, or fix the "
            "--common / --brief paths. The agents are told to read these "
            "paths, so a missing file wastes a whole assignment round.".format(
                ", ".join(unreadable)
            ),
            {"missing": unreadable},
        )
    return resolved


def validate_agents(assignments, agents_by_name):
    """Refuse an assignment naming an agent the config does not know."""
    for role, name in assignments.items():
        if name not in agents_by_name:
            raise UsageError(
                "Assignment for role {!r} names agent {!r}, which is not in the "
                "config - configured agents are {}.".format(
                    role, name, ", ".join(sorted(agents_by_name))
                ),
                {"role": role, "agent": name},
            )


def build_steps(client, assignments, agents_by_name, paths, panes=None, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS):
    """Build the per-role command plan. Pure with respect to herdr: nothing runs.

    This is what `--dry-run` prints, and what the live path walks. `panes` maps
    agent name to pane id; `--dry-run` has resolved no panes, so its rendering
    carries PANE_ID_PLACEHOLDER where the live path substitutes the real id.
    """
    validate_agents(assignments, agents_by_name)
    panes = panes or {}
    steps = []
    for role, name in assignments.items():
        agent = agents_by_name[name]
        pane_id = panes.get(name) or PANE_ID_PLACEHOLDER
        text = assignment_text(role, paths["common"], paths[role])
        composer_reads = (
            [client.argv_agent_read(name, source=COMPOSER_READ_SOURCE, lines=COMPOSER_READ_LINES)]
            if checkable(agent)
            else []
        )
        commands = [client.argv_agent_get(name)]
        # Runs only when the `agent get` above reports `working`; see
        # teamlead/probe.py.
        conditional = [
            (
                client.argv_agent_read(name, source=PROBE_READ_SOURCE, lines=PROBE_READ_LINES),
                "herdr reports the agent as working; confirms it against the "
                "pane footer before refusing",
            )
        ]
        if not no_clear:
            commands.extend(composer_reads)
            commands.extend(
                client.argv_deliver_slash_command(
                    agent.slash_delivery, name, pane_id, agent.clear_prompt
                )
            )
            commands.extend(composer_reads)
            commands.append(
                client.argv_agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            )
            if agent.recover_keys:
                conditional.append(
                    (
                        client.argv_agent_send_keys(name, agent.recover_keys),
                        "the composer already holds text before dispatch; sent "
                        "exactly once, never twice",
                    )
                )
            conditional.append(
                (
                    client.argv_pane_send_keys(pane_id, ["enter"]),
                    "the clear command is still in the composer after the "
                    "first Enter (Codex's autocomplete popup eats it)",
                )
            )
        commands.extend(composer_reads)
        # The assignment is real message text, so pasting it is correct.
        commands.append(client.argv_agent_prompt(name, text))
        steps.append(
            {
                "role": role,
                "agent": name,
                "kind": agent.kind,
                "pane_id": pane_id,
                "brief": paths[role],
                "common": paths["common"],
                "prompt": text,
                "commands": [{"argv": argv, "shell": format_argv(argv)} for argv in commands],
                "conditional_commands": [
                    {"argv": argv, "shell": format_argv(argv), "when": when}
                    for argv, when in conditional
                ],
            }
        )
    return steps


def check_all_ready(client, assignments, agents_by_name, warn=None):
    """Read every target's live status before anything is sent.

    Returns `{agent: {"state": ..., "herdr_state": ..., "state_source": ...}}`.
    Raises AgentBusyError -- having sent nothing -- when any target is
    `working` or `blocked`. There is no override: rules/agent-team-operation.md
    Dispatch Safety is unconditional, and a keystroke into a working agent
    lands in the middle of somebody's turn.
    """
    validate_agents(assignments, agents_by_name)
    statuses = {}
    for name in assignments.values():
        info = client.agent_get(name)
        herdr_status = info.get("agent_status")
        status, source = resolve_status(client, agents_by_name[name], herdr_status, warn=warn)
        statuses[name] = {
            "state": status,
            "herdr_state": herdr_status,
            "state_source": source,
            "pane_id": info.get("pane_id"),
        }
    busy = {
        name: record["state"]
        for name, record in statuses.items()
        if record["state"] in BUSY_STATES
    }
    if busy:
        raise AgentBusyError(
            "Refusing to interrupt {} - wait for them to reach idle or done, "
            "then run this again.".format(
                ", ".join("{} ({})".format(name, status) for name, status in sorted(busy.items()))
            ),
            {"busy": busy},
        )
    return statuses


def apply(client, assignments, agents_by_name, paths, at, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, on_assigned=None, warn=None, sleep=time.sleep, settle_sec=COMPOSER_SETTLE_SEC):
    """Clear each agent and hand it its brief. Writes to the agents.

    `on_assigned(role, agent, at)` is called after each successful hand-off so
    the caller records it in the state ledger as it goes -- an interrupted run
    still leaves a truthful record of what was actually sent.
    """
    validate_agents(assignments, agents_by_name)
    # Status first: it is the refusal gate, and it is also where the pane ids
    # the clear command needs come from.
    statuses = check_all_ready(client, assignments, agents_by_name, warn=warn)
    steps = build_steps(
        client,
        assignments,
        agents_by_name,
        paths,
        panes={name: record.get("pane_id") for name, record in statuses.items()},
        no_clear=no_clear,
        settle_timeout_ms=settle_timeout_ms,
    )

    applied = []
    for step in steps:
        name = step["agent"]
        agent = agents_by_name[name]
        cleared = False
        if not no_clear:
            pane_id = step["pane_id"]
            if agent.slash_delivery == SLASH_DELIVERY_TYPE and pane_id == PANE_ID_PLACEHOLDER:
                raise UsageError(
                    "herdr reported no pane for agent {!r}, so its {} command "
                    "cannot be typed - confirm the agent is live with "
                    "`herdr agent list`, or pass --no-clear.".format(
                        name, agent.clear_prompt
                    ),
                    {"agent": name},
                )
            outcome = send_command(
                client,
                agent,
                pane_id,
                agent.clear_prompt,
                sleep=sleep,
                warn=warn,
                settle_sec=settle_sec,
            )
            client.agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            # `cleared` means the command was consumed AND the screen changed:
            # a fresh Codex session draws its banner, Claude empties the
            # transcript, Grok redraws session_start. Consumed but unchanged is
            # reported honestly rather than assumed.
            cleared = outcome["screen_changed"]
            if not cleared:
                (warn or stderr_warn)(
                    "teamlead: {} consumed {} but its screen did not change, so "
                    "the context may not have been cleared. Reported as "
                    "cleared: false.".format(name, agent.clear_prompt)
                )
        # Never paste an assignment onto text already sitting in the composer.
        ensure_ready(client, agent, sleep=sleep, warn=warn, settle_sec=settle_sec)
        client.agent_prompt(name, step["prompt"])
        checked = statuses.get(name, {})
        record = {
            "role": step["role"],
            "agent": name,
            "state_before": checked.get("state"),
            "herdr_state_before": checked.get("herdr_state"),
            "state_source": checked.get("state_source"),
            "pane_id": checked.get("pane_id"),
            "cleared": cleared,
            "brief": step["brief"],
            "common": step["common"],
            "at": at,
        }
        applied.append(record)
        if on_assigned is not None:
            on_assigned(step["role"], name, at)

    return {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": False,
        "applied_at": at,
        "applied": applied,
    }


def dry_run(client, assignments, agents_by_name, paths, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS):
    """Print the plan without contacting herdr at all.

    Deliberately makes zero herdr calls, including the status check: a dry run
    against busy agents must show the plan rather than refuse it. The live
    `apply` re-checks status for real before sending anything.
    """
    return {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": True,
        "sent": False,
        "steps": build_steps(
            client,
            assignments,
            agents_by_name,
            paths,
            no_clear=no_clear,
            settle_timeout_ms=settle_timeout_ms,
        ),
    }
