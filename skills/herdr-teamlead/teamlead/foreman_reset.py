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

One delivery attempt per pane and stow, never retried automatically.
`<state>.foreman-reset.json` records each scheduled reset (`schedule`), and
the deliverer claims it before sending anything (`claim`). A retry of a live
or delivered reset replays the record and spawns nothing. Any other reset is
finalized `failed` (nothing typed) or `interrupted` (typing began) with the
resume prompt the operator pastes, under the Working Memory recovery
carve-out; the next round resets from a new stow. Before every keystroke the
deliverer re-reads the stow and the pane, and refuses unless the stow is
still reset-ready and the same agent is still idle.
"""

import copy
import fcntl
import os
import shlex
import time
from contextlib import contextmanager
from pathlib import Path

from .assign import SETTLE_STATES
from .composer import COMPOSER_SETTLE_SEC, send_command, send_message
from .errors import HerdrError, StateError, TeamLeadError, UsageError
from .herdr import DEFAULT_SETTLE_TIMEOUT_MS
from . import supervision
from .chronology import timestamp
from .supervision_runtime import process_identity
from .state import save_state, state_lock

RESET_SCHEMA_VERSION = 1
#: How long the deliverer waits for the foreman's turn to end, and how often
#: it looks. Script-owned constants (rules/ci-safety.md Always Watch CI).
IDLE_BUDGET_SEC = 1800
IDLE_POLL_SEC = 5
#: How long a new deliverer waits to claim its row. `schedule` holds the
#: record lock from the row's first save until the deliverer's identity is
#: saved, and the deliverer starts inside that window.
CLAIM_LOCK_BUDGET_SEC = 60
CLAIM_LOCK_POLL_SEC = 0.2
#: Consecutive settled reads, `IDLE_POLL_SEC` apart, before the first keystroke.
#: Herdr can report `done` for a single read while a turn is still running
#: (references/herdr.md), so one settled read is not an ended turn.
RESET_STABLE_READS = 3
#: The detail keys a failure record keeps. Herdr and composer errors can carry
#: raw subprocess output or pane text; the record keeps identifiers only.
FAILURE_DETAIL_KEYS = frozenset({"pane_id", "stow", "record", "status", "pid", "lock", "kind", "reconciled",
                                 "reconciled_at", "schema_version"})
RESUME_OPENING = "Foreman resume after a planned round-boundary reset."
RESUME_TEMPLATE = (
    RESUME_OPENING + " Your earlier conversation is gone by design. Run the "
    "herdr-teamlead skill with `{flags}` on every teamlead command. Before "
    "anything else: run `teamlead memory-show {flags} --id {stow}` and read its "
    "required files in order; run `teamlead supervision-bind {flags}`, "
    "`teamlead supervision-resume {flags}`, `teamlead supervision-status {flags}` "
    "and `teamlead supervision-drain {flags}`; then "
    "`teamlead foreman-queue {flags}`. Then take SKILL.md Step 17's Resume Route: Step 1, Step 2, "
    "then the continuation step the stow's unresolved work names, in place of Step 5. "
    "Load each decision's records before making it, with "
    "`teamlead load-set {flags} --decision <plan|brief|gate|diagnose> --task <task>` or "
    "`teamlead load-set {flags} --decision wake --enrollment <enrollment-id>`."
)


def resume_prompt(stow, state, *, config=None, herdr_bin=None):
    """The prompt a fresh context resumes from, carrying every non-default owner setting."""
    flags = "--state " + shlex.quote(state)
    if config:
        flags += " --config " + shlex.quote(config)
    if herdr_bin:
        flags += " --herdr-bin " + shlex.quote(herdr_bin)
    return RESUME_TEMPLATE.format(stow=shlex.quote(stow), flags=flags)


OPERATOR_RECOVERY = ("Do not run foreman-reset again for this stow. The operator recovers the foreman under "
                     "rules/agent-team-operation.md Working Memory: clear the foreman's pane, then paste the "
                     "resume prompt saved in this reset's record. The next round resets from a new stow.")


class ResetEnded(UsageError):
    """This stow's one reset attempt failed or was interrupted; only the operator recovers it."""

    code = "reset_ended"


class ResetRecordNewer(StateError):
    """The reset record was written by a newer build; read as no prior reset, never written."""

    code = "reset_record_newer"


class ResetRecordUnusable(StateError):
    """The reset record is unreadable or fails validation; preserved untouched."""

    code = "reset_record_unusable"


def record_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".foreman-reset.json")


#: `interrupted` is a delivery that failed after its first keystroke: the
#: pane may be cleared or half-prompted, so it is never retried automatically.
#: `reconciled` is a reset the operator closed through `reconcile` after its
#: deliverer was gone without recording an outcome, confirming the foreman
#: resumed.
STATUSES = frozenset({"scheduled", "delivering", "delivered", "failed", "interrupted", "reconciled"})
ROW_FIELDS = frozenset({"schema_version", "pane_id", "stow", "status", "scheduled_at", "options", "process", "result"})
OPTION_FIELDS = frozenset({"config", "herdr_bin"})


def _version(value):
    """A stored schema version is this exact integer; JSON `true` and `1.0` are not."""
    return type(value) is int and value == RESET_SCHEMA_VERSION


def _records(path):
    """The reset record to write through; a newer one refuses, untouched.

    A newer `schema_version` is data this build lags, not corruption
    (rules/stateful-artifacts.md Migration Policy). A write refuses it here;
    `_readable` takes it as no usable prior reset.
    """
    unusable = ("Reset record {} is {}. It is left untouched; the operator restores a valid file from its own backup "
                "before any reset.")
    if path.is_symlink():
        raise ResetRecordUnusable(unusable.format(path, "a link, not the owner's file"), {"record": str(path)})
    if not path.exists():
        return {"schema_version": RESET_SCHEMA_VERSION, "resets": []}
    try:
        document = supervision.read_json(path)
    except StateError as exc:
        raise ResetRecordUnusable(unusable.format(path, "unreadable ({})".format(exc.message)), {"record": str(path)}) from None
    if not isinstance(document, dict):
        raise ResetRecordUnusable("Reset record {} is unreadable. It is left untouched; the operator restores a valid file "
                                  "from its own backup before any reset.".format(path), {"record": str(path)})
    version = document.get("schema_version")
    if type(version) is int and version > RESET_SCHEMA_VERSION:
        raise ResetRecordNewer("Reset record {} is schema {}, newer than this build's {}. It is left untouched; update the "
                               "coding-policy plugin, then run foreman-reset.".format(path, version, RESET_SCHEMA_VERSION),
                               {"record": str(path), "schema_version": version})
    rows = document.get("resets")
    if (not _version(version) or not isinstance(rows, list) or not all(_valid_row(row) for row in rows)
            or len({(row["pane_id"], row["stow"]) for row in rows}) != len(rows)):
        raise ResetRecordUnusable("Reset record {} is malformed. It is left untouched; the operator restores a valid file "
                                  "from its own backup before any reset.".format(path), {"record": str(path)})
    return document


def _readable(path):
    """The reset record for a read, or None when a newer build wrote it."""
    try:
        return _records(path)
    except ResetRecordNewer:
        return None


def _valid_row(row):
    """Every documented field, typed, with the result shape its status requires."""
    if not isinstance(row, dict) or set(row) != ROW_FIELDS or not _version(row["schema_version"]):
        return False
    options = row["options"]
    if not (isinstance(options, dict) and set(options) <= OPTION_FIELDS
            and all(isinstance(value, str) and value for value in options.values())):
        return False
    status, process, result = row["status"], row["process"], row["result"]
    if not (isinstance(row["pane_id"], str) and isinstance(row["stow"], str) and isinstance(status, str) and status in STATUSES):
        return False
    try:
        timestamp(row["scheduled_at"], "Reset scheduled_at")
    except UsageError:
        return False
    if process is None:
        # Null only before the deliverer is identified: a row still scheduled,
        # or one whose deliverer never started or was gone before identification.
        if status not in ("scheduled", "failed"):
            return False
    elif not (isinstance(process, dict) and set(process) == {"pid", "identity"}
              and type(process["pid"]) is int and process["pid"] > 0 and isinstance(process["identity"], str)):
        return False
    if status in ("scheduled", "delivering"):
        return result is None
    if status == "reconciled":
        if not (isinstance(result, dict) and set(result) == {"outcome", "reconciled_at"} and result["outcome"] == "delivered"):
            return False
        try:
            timestamp(result["reconciled_at"], "Reset reconciled_at")
        except UsageError:
            return False
        return True
    if status == "delivered":
        return (isinstance(result, dict) and set(result) == {"schema_version", "pane_id", "stow", "agent", "cleared", "resume"}
                and _version(result["schema_version"])
                and result["pane_id"] == row["pane_id"] and result["stow"] == row["stow"]
                and result["cleared"] is True and isinstance(result["agent"], str)
                and isinstance(result["resume"], dict) and set(result["resume"]) == {"landed", "started"}
                and result["resume"]["landed"] is True and result["resume"]["started"] is True)
    return (isinstance(result, dict) and set(result) == {"error", "message", "details", "resume_prompt"}
            and all(isinstance(result[key], str) for key in ("error", "message", "resume_prompt"))
            and isinstance(result["details"], dict))


def _row(document, plan):
    return next((row for row in reversed(document["resets"])
                 if row["pane_id"] == plan["pane_id"] and row["stow"] == plan["stow"]), None)


def _alive(process, probe=None):
    """Whether the recorded deliverer is still that exact process, not a reused pid."""
    return process is not None and (probe or process_identity)(process["pid"]) == process


def _settle(document, row, state_path, alive):
    """Replay a live or delivered reset; finalize any other, then refuse it for the operator.

    A dead `scheduled` row typed nothing and becomes `failed`; a dead
    `delivering` row may have typed and becomes `interrupted`. Either way the
    row carries the resume prompt before the operator is sent to recover.
    Returns (replay-or-None, changed).
    """
    if row["status"] in ("delivered", "reconciled") or (row["status"] in ("scheduled", "delivering") and alive(row["process"])):
        return {**row, "replayed": True}, False
    state = str(Path(state_path).expanduser().resolve())
    changed = row["status"] in ("scheduled", "delivering")
    if changed:
        lost = StateError("The reset deliverer for stow {} exited without finishing.".format(row["stow"]), {"process": row["process"]})
        row.update(status="failed" if row["status"] == "scheduled" else "interrupted",
                   result=failure(lost, row["stow"], state, **row["options"]))
    return None, changed


def _refuse(row, state_path, cause=None):
    raise ResetEnded("The reset from stow {} ended {}{}; the pane may already be cleared. {}".format(
        row["stow"], row["status"], " ({})".format(cause) if cause else "", OPERATOR_RECOVERY),
        {"record": str(record_path(state_path)), "resume_prompt": row["result"]["resume_prompt"]})


def replay(state_path, plan, *, alive=_alive):
    """The existing reset for (pane, stow), or None when this stow never reset.

    Read before every precondition the reset itself changes: once a reset
    ran, its stow's reads and the supervision state legitimately change, and a
    retry still replays. Reading the stow and supervision, and checking the
    caller's pane, still come first (`cli.cmd_foreman_reset`).
    A reset that is neither live nor delivered is finalized and refused.
    """
    path = record_path(state_path)
    with state_lock(path):
        document = _readable(path)
        if document is None:
            return None
        row = _row(document, plan)
        if row is None:
            return None
        live, changed = _settle(document, row, state_path, alive)
        if changed:
            save_state(path, document)
    if live is None:
        _refuse(row, state_path)
    return live


def schedule(state_path, plan, at, start, *, alive=_alive, probe=None, options=None):
    """Record one reset for (pane, stow) and start its deliverer exactly once.

    `start()` launches the deliverer and returns its pid. The record lock is
    held until the deliverer's process identity is saved, and a deliverer
    claims only the row carrying its own identity. A launch failure, or a
    deliverer that is already gone when probed, finishes the row `failed`
    with the resume prompt before re-raising.
    """
    try:
        timestamp(at, "Reset scheduled_at")
    except UsageError:
        raise UsageError("The reset time {!r} is not an ISO-8601 timestamp with a timezone; nothing was scheduled.".format(at),
                         {"at": at}) from None
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        prior = _row(document, plan)
        if prior is not None:
            live, changed = _settle(document, prior, state_path, alive)
            if changed:
                save_state(path, document)
            if live is not None:
                return live
            _refuse(prior, state_path)
        row = {"schema_version": RESET_SCHEMA_VERSION, **plan, "status": "scheduled", "scheduled_at": at,
               "options": dict(options or {}), "process": None, "result": None}
        if not _valid_row(row):
            raise UsageError("The reset for stow {} would not validate as a reset row; nothing was scheduled.".format(
                plan.get("stow")), {"row": row})
        document["resets"].append(row)
        save_state(path, document)
        try:
            pid = start()
            # The deliverer waits on this lock to claim, so it is alive to be identified.
            identity = (probe or process_identity)(pid)
            if identity is None:
                raise StateError("The reset deliverer (pid {}) exited before it could be identified; nothing was sent.".format(pid), {"pid": pid})
            row["process"] = identity
        except TeamLeadError as exc:
            row.update(status="failed", result=failure(exc, plan["stow"], str(Path(state_path).expanduser().resolve()), **(options or {})))
            save_state(path, document)
            _refuse(row, state_path, exc.message)
        save_state(path, document)
        return {**row, "replayed": False}


#: Error codes whose messages this owner writes itself. Any other error, a
#: Herdr or composer failure above all, can carry raw subprocess output or pane
#: text in its message; the record keeps a generic line and the log keeps it.
OWN_MESSAGE_CODES = frozenset({"usage_error", "state_error", "reset_ended", "reset_record_newer", "reset_record_unusable"})


def failure(exc, stow, state, **options):
    """The durable result of a failed or interrupted reset: the error and the prompt the operator pastes.

    Details are filtered to identifier keys with scalar values, and a message
    this owner did not write is replaced by a generic one; the full error
    stays in the deliverer's log.
    """
    details = {key: value for key, value in exc.details.items()
               if key in FAILURE_DETAIL_KEYS and (value is None or isinstance(value, (str, int, float, bool)))}
    message = exc.message if exc.code in OWN_MESSAGE_CODES else (
        "A Herdr call failed ({}); the reset's log holds its output.".format(exc.code))
    return {"error": exc.code, "message": message, "details": details, "resume_prompt": resume_prompt(stow, state, **options)}


def launcher():
    """The installed launcher that runs this package's commands."""
    return str(Path(__file__).resolve().parents[1] / "teamlead.sh")


def reconcile_command(state_path, pane_id, stow, outcome):
    """The complete, runnable repair command for one reset."""
    return "bash {} foreman-reset-reconcile --state {} --pane {} --stow {} --outcome {}".format(
        shlex.quote(launcher()), shlex.quote(str(Path(state_path).expanduser().resolve())),
        shlex.quote(pane_id), shlex.quote(stow), outcome)


TERMINAL_FAILURES = frozenset({"failed", "interrupted"})


def outstanding(state_path, *, alive=_alive):
    """The foreman resets that still need the operator, read from the record alone.

    The record is the durable blocker: `foreman-reset` writes the row before
    anything else can fail, and every later failure lands on it. For each pane,
    the latest reset needs the operator when it ended `failed` or
    `interrupted`, or when it never reached an outcome and its deliverer is
    gone. A later `delivered` reset for the pane supersedes an older failure.
    An unreadable record is itself outstanding. Read-only: nothing is written.
    """
    path = record_path(state_path)
    try:
        document = _readable(path)
    except ResetRecordUnusable as exc:
        return [{"pane_id": None, "stow": None, "status": "record_unusable", "record": str(path),
                 "needed": exc.message, "resume_prompt": None}]
    if document is None:
        return []
    latest = {}
    for row in document["resets"]:
        latest[row["pane_id"]] = row
    items = []
    for row in latest.values():
        if row["status"] in TERMINAL_FAILURES:
            prompt = row["result"]["resume_prompt"]
            needed = OPERATOR_RECOVERY
            if row["status"] == "interrupted":
                # Typing began, so the pane may already hold a resumed foreman:
                # the record alone cannot say, and clearing it would erase that context.
                needed = ("Look at pane {} first: if a foreman resumed from this reset is running there, run `{}` "
                          "and clear nothing. Otherwise: {}".format(
                              row["pane_id"], reconcile_command(state_path, row["pane_id"], row["stow"], "delivered"),
                              OPERATOR_RECOVERY))
        elif row["status"] in ("scheduled", "delivering") and not alive(row["process"]):
            prompt = None
            failed = reconcile_command(state_path, row["pane_id"], row["stow"], "failed")
            delivered = reconcile_command(state_path, row["pane_id"], row["stow"], "delivered")
            if row["status"] == "scheduled":
                needed = ("The deliverer stopped before claiming the reset, so nothing was typed and the foreman in pane {} "
                          "still holds its old context. Run `{}`; catch-up then shows the saved resume "
                          "prompt for recovery.".format(row["pane_id"], failed))
            else:
                needed = ("The deliverer stopped mid-delivery and its outcome is unknown. Look at pane {}: if a resumed "
                          "foreman is running there, run `{}`; otherwise run `{}` "
                          "and recover from the saved resume prompt.".format(row["pane_id"], delivered, failed))
        else:
            continue
        items.append({"pane_id": row["pane_id"], "stow": row["stow"], "status": row["status"], "record": str(path),
                      "needed": needed, "resume_prompt": prompt})
    return items


RECONCILE_OUTCOMES = ("delivered", "failed")


def reconcile(state_path, plan, outcome, at, *, alive=_alive):
    """Close a reset whose deliverer is gone without an outcome; the operator says which one happened.

    The owner's repair for a record that could not say how a delivery ended.
    Only a `scheduled` or `delivering` row whose deliverer is no longer that
    process qualifies: a live one is still working, and a finished one already
    has its outcome. `failed` records the failure with the resume prompt built
    from the row's own settings; `delivered` records that the operator saw the
    foreman resume.
    """
    if outcome not in RECONCILE_OUTCOMES:
        raise UsageError("Reconcile outcome is delivered or failed.", {"outcome": outcome})
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        row = _row(document, plan)
        if row is None:
            raise UsageError("Reset record {} holds no reset for stow {} in pane {}; nothing to reconcile.".format(
                path, plan["stow"], plan["pane_id"]), {"record": str(path)})
        previous = _reconciled_outcome(row)
        if previous == outcome:
            # An identical retry, e.g. after the first response was lost.
            return {**row, "replayed": True}
        # An interrupted delivery may have resumed the foreman after all; the
        # operator who sees it running reconciles it as delivered.
        # A `scheduled` row was never claimed, so nothing was typed and it can
        # only have failed; a delivery that began may have resumed the foreman.
        reopenable = ((row["status"] == "scheduled" and outcome == "failed")
                      or row["status"] == "delivering"
                      or (row["status"] == "interrupted" and outcome == "delivered"))
        if not reopenable:
            raise UsageError("The reset from stow {} already ended {}{}; there is nothing to reconcile.".format(
                row["stow"], row["status"], " (reconciled as {})".format(previous) if previous else ""),
                {"record": str(path), "status": row["status"]})
        if row["status"] != "interrupted" and alive(row["process"]):
            raise UsageError("The reset from stow {} still has its deliverer running; let it finish instead of "
                             "reconciling.".format(row["stow"]), {"record": str(path), "process": row["process"]})
        if outcome == "delivered":
            row.update(status="reconciled", result={"outcome": "delivered", "reconciled_at": at})
        else:
            lost = StateError("The operator reconciled this reset as failed: its deliverer stopped without an outcome.",
                              {"reconciled": "failed", "reconciled_at": at})
            row.update(status="failed", result=failure(lost, row["stow"], str(Path(state_path).expanduser().resolve()),
                                                       **row["options"]))
        if not _valid_row(row):
            raise UsageError("The reconciled row does not validate; nothing was written.", {"record": str(path)})
        save_state(path, document)
        return {**row, "replayed": False}


def _reconciled_outcome(row):
    """The outcome `reconcile` recorded on this row, or None when it never ran."""
    if row["status"] == "reconciled":
        return "delivered"
    if row["status"] == "failed" and row["result"]["details"].get("reconciled") == "failed":
        return "failed"
    return None


def delivery_failed(state_path, stow, result):
    """The error a deliverer exits with once its failure is recorded: where the record and the prompt are."""
    record = record_path(state_path)
    return ResetEnded("The reset from stow {} did not complete ({}); {} holds the cause and the resume prompt. {}".format(
        stow, result["error"], record, OPERATOR_RECOVERY),
        {"record": str(record), "resume_prompt": result["resume_prompt"], "cause": {"error": result["error"], "message": result["message"]}})


@contextmanager
def _waiting_lock(path, *, budget_sec=CLAIM_LOCK_BUDGET_SEC, poll_sec=CLAIM_LOCK_POLL_SEC, sleep=time.sleep, clock=time.monotonic):
    """The record's owner lock (the file `state_lock` uses), waited for up to a budget."""
    lock_path = Path(str(path) + ".lock")
    try:
        handle = lock_path.open("a", encoding="utf-8")
    except OSError as exc:
        raise StateError("Cannot open reset lock {}: {}. Restore directory access; the reset was not claimed.".format(lock_path, exc), {}) from None
    with handle:
        deadline = clock() + budget_sec
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise StateError("Reset lock {} was still held after {}s; this deliverer did not claim and sent nothing.".format(
                        lock_path, budget_sec), {"lock": str(lock_path)}) from None
                sleep(min(poll_sec, remaining))
            except OSError as exc:
                raise StateError("Cannot lock {}: {}. Use a filesystem supporting process locks; the reset was not claimed.".format(
                    lock_path, exc), {}) from None
        yield


def claim(state_path, plan, process, *, sleep=time.sleep, clock=time.monotonic):
    """Move this deliverer's scheduled reset to `delivering`; False when it is not the owner.

    `process` is the caller's own identity; a reused pid carries another one.
    The deliverer starts while `schedule` still holds the record lock, so it
    waits for that lock rather than failing on it.
    """
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "scheduled" or row["process"] != process:
            return False
        row["status"] = "delivering"
        save_state(path, document)
        return True


def fail_unclaimed(state_path, plan, result, *, sleep=time.sleep, clock=time.monotonic):
    """Finalize a still-`scheduled` row `failed`; return the row's status afterwards.

    The operator's recovery requires the record to show `failed` or
    `interrupted`, so the deliverer records its own pre-claim failure before
    exiting. Nothing was typed. A row that is no longer `scheduled` belongs to
    whatever moved it, and is left alone; its status says whether recovery is
    authorized.
    """
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None:
            raise ResetRecordUnusable("Reset record {} holds no reset for stow {} in pane {}; the failure was not recorded. "
                                      "The operator reconciles the record before any recovery.".format(path, plan["stow"], plan["pane_id"]),
                                      {"record": str(path)})
        if row["status"] == "scheduled":
            row.update(status="failed", result=result)
            save_state(path, document)
        return row["status"]


def finish(state_path, plan, status, result):
    """Record the claimed delivery's outcome; only a `delivering` row finishes."""
    path = record_path(state_path)
    with state_lock(path):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "delivering":
            raise ResetRecordUnusable("Reset record {} holds no delivering reset for stow {} in pane {}; this outcome ({}) was "
                                      "not recorded. The operator reconciles the record before any reset.".format(
                                          path, plan["stow"], plan["pane_id"], status), {"record": str(path)})
        row.update(status=status, result=result)
        if not _valid_row(row):
            raise ResetRecordUnusable("The {} outcome for stow {} does not match the reset record's shape; it was not recorded.".format(
                status, plan["stow"]), {"record": str(path)})
        save_state(path, document)


def _handoff_held(data):
    """A current, unresumed `handoff` hold covers the active work; a user pause does not."""
    return supervision.held(data) and any(
        row["resumed_at"] is None and row["kind"] == "handoff" and row["through"] == len(data["events"])
        and row["members"] == supervision.active_digest(data) for row in data["holds"])


def preflight(stow, supervision_data, caller_pane):
    """Refuse a reset that would lose work; return what the deliverer needs."""
    if stow.get("kind") != "stow":
        raise UsageError("Memory record {} is not a stow; name the stow to resume from.".format(stow.get("id")), {"record": stow.get("id")})
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
    # The resume sequence resumes every open hold, so a user pause must not ride along.
    waiting = [row.get("id") for row in supervision_data["holds"] if row["resumed_at"] is None and row["kind"] != "handoff"]
    if waiting:
        raise UsageError("A user pause is still open ({}); the reset's resume sequence would resume it without the user. "
                         "Record the user's answer and resume that hold before resetting.".format(", ".join(map(str, waiting))),
                         {"holds": waiting})
    if events or (active and not _handoff_held(supervision_data)):
        raise UsageError("The foreman cannot stop yet: {} unhandled event(s), {} active assignment(s) without a covering hold. Handle the events and save supervision-hold kind handoff (a user pause does not qualify) before resetting.".format(
            len(events), len(active)), {"events": len(events), "active": active})
    return {"pane_id": pane, "stow": stow["id"]}


def _foreman_record(client, pane_id):
    record = next((row for row in client.agent_list() if row.get("pane_id") == pane_id), None)
    if record is None:
        raise HerdrError("No Herdr agent runs in the foreman's pane {}, so nothing further was sent. {}".format(
            pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
    return record


def mechanics(agents, kind, name):
    """A worker config of the foreman's runtime kind, renamed to the foreman."""
    template = next((agent for agent in agents if agent.kind == kind), None)
    if template is None:
        raise StateError("No configured worker has kind {!r}, so the foreman's clear command is unknown. Add one to config.json.".format(kind), {"kind": kind})
    foreman = copy.copy(template)
    foreman.name = name
    return foreman


class DeliveryInterrupted(HerdrError):
    """A delivery that failed after typing into the pane; never retried automatically."""


def deliver(client, agents, pane_id, stow, state, *, still_ready=lambda: True, sleep=time.sleep, clock=time.monotonic, warn=None,
            budget_sec=IDLE_BUDGET_SEC, poll_sec=IDLE_POLL_SEC, settle_sec=COMPOSER_SETTLE_SEC, options=None):
    """Wait for the foreman's pane to go idle, then clear it and send the resume prompt.

    `still_ready()` re-checks the stow right before the clear; a stow that
    changed while the deliverer waited stops the reset with nothing sent.
    """
    deadline = clock() + budget_sec
    settled = 0
    while True:
        record = _foreman_record(client, pane_id)
        settled = settled + 1 if record.get("agent_status") in SETTLE_STATES else 0
        if settled >= RESET_STABLE_READS:
            break
        if clock() >= deadline:
            raise HerdrError("The foreman's pane {} stayed {} for {}s; nothing was sent. {}".format(
                pane_id, record.get("agent_status"), budget_sec, OPERATOR_RECOVERY), {"pane_id": pane_id})
        sleep(poll_sec)
    if not isinstance(record.get("name"), str) or not record["name"]:
        raise HerdrError("Herdr lists the foreman's pane {} with no agent name, so its runtime cannot be matched; nothing was "
                         "sent. {}".format(pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
    agent = mechanics(agents, record.get("agent"), record["name"])
    if not still_ready():
        raise UsageError("Stow {} is no longer reset-ready; nothing was sent. {}".format(stow, OPERATOR_RECOVERY), {"stow": stow})

    typed = []

    def guard():
        # The stow and the pane are both re-read right before every keystroke.
        if not still_ready():
            raise UsageError("Stow {} stopped being reset-ready before typing; nothing more was sent. {}".format(stow, OPERATOR_RECOVERY), {"stow": stow})
        # Dispatch Safety: never type into a pane that started another turn.
        live = _foreman_record(client, pane_id)
        if (live.get("name") != agent.name or live.get("agent") != agent.kind
                or live.get("agent_status") not in SETTLE_STATES):
            raise HerdrError("The foreman's pane {} changed ({} {}, {}) before typing, so the reset stopped. {}".format(
                pane_id, live.get("agent"), live.get("name"), live.get("agent_status"), OPERATOR_RECOVERY), {"pane_id": pane_id})
        typed.append(True)

    try:
        outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec,
                               before_input=guard)
        if not outcome["screen_changed"]:
            raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared and nothing further was sent. Check the clear command configured for kind {} in pane {}. {}".format(
                agent.clear_prompt, agent.kind, pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
        client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
        sleep(settle_sec)
        landing = send_message(client, agent, resume_prompt(stow, state, **(options or {})), RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                               settle_sec=settle_sec, before_input=guard)
        if not (landing["landed"] and landing["started"]):
            raise HerdrError("The foreman was cleared but the resume prompt did not {} in pane {}. {}".format(
                "land" if not landing["landed"] else "start a turn", pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
    except TeamLeadError as exc:
        if typed and not isinstance(exc, DeliveryInterrupted):
            raise DeliveryInterrupted("{} The pane was already typed into, so this reset is not retried. {}".format(
                exc.message, OPERATOR_RECOVERY), exc.details) from None
        raise
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "stow": stow, "agent": agent.name,
            "cleared": True, "resume": landing}
