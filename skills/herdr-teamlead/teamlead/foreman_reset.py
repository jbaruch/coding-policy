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

def resume_prompt(stow):
    return RESUME_TEMPLATE.format(stow=stow)


def record_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".foreman-reset.json")


STATUSES = frozenset({"scheduled", "delivering", "delivered", "failed"})
ROW_FIELDS = frozenset({"schema_version", "pane_id", "stow", "status", "scheduled_at", "pid", "result"})


def _records(path):
    if not path.exists():
        return {"schema_version": RESET_SCHEMA_VERSION, "resets": []}
    document = supervision.read_json(path)
    if not isinstance(document, dict):
        raise StateError("Reset record {} is unreadable; preserve it and restore a valid file before resetting.".format(path), {})
    rows = document.get("resets")
    valid = (document.get("schema_version") == RESET_SCHEMA_VERSION and isinstance(rows, list)
             and all(isinstance(row, dict) and set(row) == ROW_FIELDS and row["schema_version"] == RESET_SCHEMA_VERSION
                     and isinstance(row["pane_id"], str) and isinstance(row["stow"], str) and row["status"] in STATUSES
                     and (row["pid"] is None or type(row["pid"]) is int) for row in rows))
    if not valid:
        raise StateError("Reset record {} is unreadable, malformed or newer; preserve it and restore a valid file before resetting.".format(path), {})
    return document


def _row(document, plan):
    return next((row for row in reversed(document["resets"])
                 if row["pane_id"] == plan["pane_id"] and row["stow"] == plan["stow"]), None)


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


def replay(state_path, plan, *, alive=_alive):
    """The existing reset for (pane, stow) a retry must return, or None to schedule anew.

    Read before any new-reset precondition: once a reset ran, its stow's reads
    and the supervision state legitimately change, and a retry still replays.
    A `delivering` row whose process is gone may have cleared the pane already,
    so it is never retried automatically.
    """
    prior = _row(_records(record_path(state_path)), plan)
    if prior is None or prior["status"] == "failed":
        return None
    if prior["status"] == "delivered" or alive(prior["pid"]):
        return {**prior, "replayed": True}
    if prior["status"] == "delivering":
        raise UsageError("The reset from stow {} lost its deliverer mid-delivery, so the pane may already be cleared. Check the foreman pane: if it resumed, nothing is owed; otherwise clear it by hand and paste the resume prompt. Reset again only from a new stow.".format(
            plan["stow"]), {"record": str(record_path(state_path))})
    return None


def schedule(state_path, plan, at, start, *, alive=_alive):
    """Record one reset for (pane, stow) and start its deliverer exactly once.

    `start()` launches the deliverer and returns its pid. The record lock is
    held until that pid is saved, and a deliverer claims only a row carrying
    its own pid, so no deliverer can act on a row it was not started for.
    """
    path = record_path(state_path)
    with state_lock(path):
        existing = replay(state_path, plan, alive=alive)
        if existing is not None:
            return existing
        document = _records(path)
        prior = _row(document, plan)
        if prior is not None and prior["status"] == "scheduled":
            # Its deliverer never claimed the row and is gone; nothing was typed.
            prior.update(status="failed", result={"error": "deliverer_lost", "pid": prior["pid"]})
        row = {"schema_version": RESET_SCHEMA_VERSION, **plan, "status": "scheduled", "scheduled_at": at,
               "pid": None, "result": None}
        document["resets"].append(row)
        save_state(path, document)
        row["pid"] = start()
        save_state(path, document)
        return {**row, "replayed": False}


def claim(state_path, plan, pid):
    """Move this deliverer's scheduled reset to `delivering`; False when it is not the owner."""
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "scheduled" or row["pid"] != pid:
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
        raise HerdrError("No Herdr agent runs in the foreman's pane {}, so nothing was sent. Restart or rebind the foreman in that pane, then run foreman-reset again from a new stow.".format(pane_id), {"pane_id": pane_id})
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
        if (live.get("name") != agent.name or live.get("agent") != agent.kind
                or live.get("agent_status") not in SETTLE_STATES):
            raise HerdrError("The foreman's pane {} changed ({} {}, {}) before typing, so the reset stopped. Let that turn finish, confirm the stow is still reset-ready with memory-show, and run foreman-reset again.".format(
                pane_id, live.get("agent"), live.get("name"), live.get("agent_status")), {"pane_id": pane_id})

    outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec,
                           before_input=guard)
    if not outcome["screen_changed"]:
        raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared and nothing further was sent. Check the clear command configured for kind {}, or clear pane {} by hand and paste:\n{}".format(
            agent.clear_prompt, agent.kind, pane_id, resume_prompt(stow)), {"pane_id": pane_id})
    client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
    sleep(settle_sec)
    landing = send_message(client, agent, resume_prompt(stow), RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                           settle_sec=settle_sec, before_input=guard)
    if not landing["landed"]:
        raise HerdrError("The foreman was cleared but the resume prompt did not land in pane {}. Paste it by hand:\n{}".format(
            pane_id, resume_prompt(stow)), {"pane_id": pane_id})
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "stow": stow, "agent": agent.name,
            "cleared": True, "resume": landing}
