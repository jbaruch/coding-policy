"""Clear the foreman's own context at a round boundary (#483).

The foreman's conversation grew for the whole session until it failed with
`Prompt is too long`. The fix is a reset at every round boundary: the foreman
saves a handoff (a reset-ready stow), ends its turn, and a fresh context
resumes from durable records.

A foreman cannot type into its own composer mid-turn, so the reset is two
commands. `foreman-reset` runs inside the turn: it checks the preconditions
and starts a detached `foreman-reset-deliver`. The deliverer waits until the
foreman's pane is idle -- the turn has ended -- then sends the runtime's clear
command and the resume prompt through the same composer checks used for
workers (teamlead/composer.py). It never types into a working or blocked pane.

Preconditions, all checked before anything is scheduled:

- the named stow is `reset_ready` (memory.py)
- the caller runs in the bound foreman's own Herdr pane
- the foreman could stop now: no unhandled supervision event, and either no
  active enrollment or a hold covering the current ones (the Stop hook's rule)

The foreman's runtime mechanics (clear command, slash delivery, composer
glyphs) come from a configured worker of the same kind; Herdr names the kind
and the foreman's agent name from its own pane.
"""

import copy
import time

from .assign import SETTLE_STATES
from .composer import COMPOSER_SETTLE_SEC, send_command, send_message
from .errors import HerdrError, StateError, UsageError
from .herdr import DEFAULT_SETTLE_TIMEOUT_MS
from . import supervision

RESET_SCHEMA_VERSION = 1
#: How long the deliverer waits for the foreman's turn to end, and how often
#: it looks. Script-owned constants (rules/ci-safety.md Always Watch CI).
IDLE_BUDGET_SEC = 1800
IDLE_POLL_SEC = 5
RESUME_OPENING = "Foreman resume after a planned round-boundary reset."
RESUME_PROMPT = (
    RESUME_OPENING + " Your earlier conversation is gone by design. Run the "
    "herdr-teamlead skill. Before anything else: run `teamlead memory-show` and read "
    "its required files in order; run `teamlead supervision-bind`, "
    "`supervision-resume`, `supervision-status` and `supervision-drain`; then "
    "`teamlead foreman-queue`. Load each decision's records with `teamlead "
    "load-set` before making it."
)


def preflight(stow, supervision_data, caller_pane):
    """Refuse a reset that would lose work; return what the deliverer needs."""
    if not stow["reset_ready"]:
        raise UsageError("Stow {} is not reset-ready: a required read changed or a gap names no task. Record a new stow before resetting.".format(
            stow["id"]), {"stow": stow["id"]})
    binding = supervision_data.get("binding")
    if binding is None:
        raise UsageError("No foreman is bound to this state; run supervision-bind from the foreman's pane before resetting.", {})
    pane = binding["identity"]["pane_id"]
    if caller_pane != pane:
        raise UsageError("foreman-reset runs from the bound foreman's own pane ({}); this call came from {}.".format(
            pane, caller_pane or "outside Herdr"), {"pane_id": pane})
    events = supervision.pending(supervision_data)
    active = [row["id"] for row in supervision_data["members"] if row["active"]]
    if events or (active and not supervision.held(supervision_data)):
        raise UsageError("The foreman cannot stop yet: {} unhandled event(s), {} active assignment(s) without a covering hold. Handle the events and save supervision-hold kind handoff before resetting.".format(
            len(events), len(active)), {"events": len(events), "active": active})
    return {"pane_id": pane, "stow": stow["id"]}


def _foreman_record(client, pane_id):
    record = next((row for row in client.agent_list() if row.get("pane_id") == pane_id), None)
    if record is None:
        raise HerdrError("No Herdr agent runs in the foreman's pane {}; the reset was not delivered.".format(pane_id), {"pane_id": pane_id})
    return record


def mechanics(agents, kind, name):
    """A worker config of the foreman's runtime kind, renamed to the foreman."""
    template = next((agent for agent in agents if agent.kind == kind), None)
    if template is None:
        raise StateError("No configured worker has kind {!r}, so the foreman's clear command is unknown. Add one to config.json.".format(kind), {"kind": kind})
    foreman = copy.copy(template)
    foreman.name = name
    return foreman


def deliver(client, agents, pane_id, *, sleep=time.sleep, clock=time.monotonic, warn=None,
            budget_sec=IDLE_BUDGET_SEC, poll_sec=IDLE_POLL_SEC, settle_sec=COMPOSER_SETTLE_SEC):
    """Wait for the foreman's pane to go idle, then clear it and send the resume prompt."""
    deadline = clock() + budget_sec
    while True:
        record = _foreman_record(client, pane_id)
        if record.get("agent_status") in SETTLE_STATES:
            break
        if clock() >= deadline:
            raise HerdrError("The foreman's pane {} stayed {} for {}s; nothing was sent. Clear it by hand and paste the resume prompt.".format(
                pane_id, record.get("agent_status"), budget_sec), {"pane_id": pane_id})
        sleep(poll_sec)
    agent = mechanics(agents, record.get("agent"), record["name"])
    outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec)
    if not outcome["screen_changed"]:
        raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared. Nothing further was sent.".format(
            agent.clear_prompt), {"pane_id": pane_id})
    client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
    sleep(settle_sec)
    landing = send_message(client, agent, RESUME_PROMPT, RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                           settle_sec=settle_sec)
    if not landing["landed"]:
        raise HerdrError("The foreman was cleared but the resume prompt did not land in pane {}. Paste it by hand:\n{}".format(
            pane_id, RESUME_PROMPT), {"pane_id": pane_id})
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "agent": agent.name,
            "cleared": True, "resume": landing}
