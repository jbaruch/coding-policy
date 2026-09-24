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

One reset per pane and stow: `<state>.foreman-reset.json` records each
scheduled reset (`schedule`), and the deliverer claims it before sending
anything (`claim`). A retry of a scheduled or delivered reset replays the
record and spawns nothing; only a failed one may be scheduled again. Before
every keystroke the deliverer re-reads the pane and refuses unless the same
agent is still idle.
"""

import copy
import os
import time
from pathlib import Path

from .assign import SETTLE_STATES
from .composer import COMPOSER_SETTLE_SEC, send_command, send_message
from .errors import HerdrError, StateError, UsageError
from .herdr import DEFAULT_SETTLE_TIMEOUT_MS
from . import supervision
from .state import save_state, state_lock

RESET_SCHEMA_VERSION = 1
#: How long the deliverer waits for the foreman's turn to end, and how often
#: it looks. Script-owned constants (rules/ci-safety.md Always Watch CI).
IDLE_BUDGET_SEC = 1800
IDLE_POLL_SEC = 5
RESUME_OPENING = "Foreman resume after a planned round-boundary reset."
RESUME_TEMPLATE = (
    RESUME_OPENING + " Your earlier conversation is gone by design. Run the "
    "herdr-teamlead skill. Before anything else: run `teamlead memory-show --id {stow}` "
    "and read its required files in order; run `teamlead supervision-bind`, "
    "`supervision-resume`, `supervision-status` and `supervision-drain`; then "
    "`teamlead foreman-queue`. Load each decision's records with `teamlead "
    "load-set` before making it."
)
#: Reset record statuses. `delivered` always replays; `scheduled` and
#: `delivering` replay while their deliverer process is alive, and are marked
#: failed when it is gone; `failed` may be scheduled again.
REPLAYED = frozenset({"scheduled", "delivering", "delivered"})


def resume_prompt(stow):
    return RESUME_TEMPLATE.format(stow=stow)


def record_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".foreman-reset.json")


def _records(path):
    if not path.exists():
        return {"schema_version": RESET_SCHEMA_VERSION, "resets": []}
    document = supervision.read_json(path)
    if (not isinstance(document, dict) or document.get("schema_version") != RESET_SCHEMA_VERSION
            or not isinstance(document.get("resets"), list)):
        raise StateError("Reset record {} is unreadable or newer; preserve it and restore a valid file before resetting.".format(path), {})
    return document


def _alive(pid):
    """Whether a deliverer process still exists; a gone one cannot hold its reset."""
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def schedule(state_path, plan, at, start, *, alive=_alive):
    """Record one reset for (pane, stow) and start its deliverer exactly once.

    `start()` launches the deliverer and returns its pid; it runs only when no
    live reset for this pane and stow exists.
    """
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        prior = next((row for row in reversed(document["resets"])
                      if row["pane_id"] == plan["pane_id"] and row["stow"] == plan["stow"]), None)
        if prior is not None and prior["status"] == "delivered":
            return {**prior, "replayed": True}
        if prior is not None and prior["status"] in REPLAYED and alive(prior["pid"]):
            return {**prior, "replayed": True}
        if prior is not None and prior["status"] in REPLAYED:
            # Its deliverer died mid-flight; record that before scheduling again.
            prior.update(status="failed", result={"error": "deliverer_lost", "pid": prior["pid"]})
        row = {"schema_version": RESET_SCHEMA_VERSION, **plan, "status": "scheduled", "scheduled_at": at,
               "pid": None, "result": None}
        document["resets"].append(row)
        save_state(path, document)
        row["pid"] = start()
        save_state(path, document)
        return {**row, "replayed": False}


def claim(state_path, plan):
    """Move a scheduled reset to `delivering`; False when another run owns it."""
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        row = next((item for item in reversed(document["resets"])
                    if item["pane_id"] == plan["pane_id"] and item["stow"] == plan["stow"]), None)
        if row is None or row["status"] != "scheduled":
            return False
        row["status"] = "delivering"
        save_state(path, document)
        return True


def finish(state_path, plan, status, result):
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        row = next(item for item in reversed(document["resets"])
                   if item["pane_id"] == plan["pane_id"] and item["stow"] == plan["stow"])
        row.update(status=status, result=result)
        save_state(path, document)


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


def deliver(client, agents, pane_id, stow, *, still_ready=lambda: True, sleep=time.sleep, clock=time.monotonic, warn=None,
            budget_sec=IDLE_BUDGET_SEC, poll_sec=IDLE_POLL_SEC, settle_sec=COMPOSER_SETTLE_SEC):
    """Wait for the foreman's pane to go idle, then clear it and send the resume prompt.

    `still_ready()` re-checks the stow right before the clear; a stow that
    changed while the deliverer waited stops the reset with nothing sent.
    """
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
    if not still_ready():
        raise UsageError("Stow {} is no longer reset-ready; nothing was sent. Record a new stow and reset again.".format(stow), {"stow": stow})

    def guard():
        # Dispatch Safety: never type into a pane that started another turn.
        live = _foreman_record(client, pane_id)
        if live.get("name") != agent.name or live.get("agent_status") not in SETTLE_STATES:
            raise HerdrError("The foreman's pane {} is {} again; the reset stopped before typing into it.".format(
                pane_id, live.get("agent_status")), {"pane_id": pane_id})

    outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec,
                           before_input=guard)
    if not outcome["screen_changed"]:
        raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared. Nothing further was sent.".format(
            agent.clear_prompt), {"pane_id": pane_id})
    client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
    sleep(settle_sec)
    landing = send_message(client, agent, resume_prompt(stow), RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                           settle_sec=settle_sec, before_input=guard)
    if not landing["landed"]:
        raise HerdrError("The foreman was cleared but the resume prompt did not land in pane {}. Paste it by hand:\n{}".format(
            pane_id, resume_prompt(stow)), {"pane_id": pane_id})
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "stow": stow, "agent": agent.name,
            "cleared": True, "resume": landing}
