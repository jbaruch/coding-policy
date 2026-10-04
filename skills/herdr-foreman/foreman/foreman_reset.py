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
workers (foreman/composer.py). It never types into a working or blocked pane.

Preconditions, all checked before anything is scheduled:

- the named stow is `reset_ready` (memory.py)
- its id is not `latest`, the memory-show selector the resume prompt cannot name exactly
- the caller runs in the bound foreman's own Herdr pane
- the foreman could stop now: no unhandled supervision event, and either no
  active enrollment or a hold covering the current ones (the Stop hook's rule)

The foreman's runtime mechanics (clear command, slash delivery, composer
glyphs) come from a configured worker of the same kind; Herdr names the kind
and the foreman's agent name from its own pane.

One `foreman-reset` per pane and stow, never retried. `<state>.foreman-reset.json`
records each scheduled reset (`schedule`), and the deliverer claims it before
sending anything (`claim`). A retry of a live, delivered or reconciled reset
replays the record and spawns nothing. A never-typed failure (`scheduled` or
`failed` before any keystroke) is recovered by the owner with one `deliver()`
under the same idle, native-session, empty-composer and process-identity
checks; that recovery is not a second `foreman-reset`. An `interrupted` reset,
a replaced pane, a busy or occupied composer, or a session that is already the
post-clear resume session stays operator look-first. The next round resets
from a new stow. Before every keystroke the deliverer re-reads the stow and
the pane, and refuses unless the stow is still reset-ready and the same agent
is still idle.

The pane's name and runtime kind do not identify the foreman: the operator can
replace the process in that pane with another session of the same name and
kind while the deliverer waits (#523). `foreman-reset` records the native
session bound at `supervision-bind` on the row, and every keystroke of the
clear command, extra Enters included, refuses unless the pane still holds that
session.
The clear itself starts a new native session by design. The deliverer waits
for Herdr to report that new session, pins it, and every keystroke of the
resume prompt refuses unless the pane still holds the pinned session. A new
session alone cannot tell the clear's from a replacement's, so the pin also
requires the pane's foreground processes to be the ones the first keystroke
found, by pid, start time and command line: the clear keeps its process, and a
replacement is a new one.
"""

import copy
import fcntl
import json
import os
import select
import shlex
import time
from contextlib import contextmanager
from pathlib import Path

from .assign import SETTLE_STATES
from .composer import COMPOSER_SETTLE_SEC, send_command, send_message
from .errors import HerdrError, StateError, ForemanError, UsageError
from .herdr import DEFAULT_SETTLE_TIMEOUT_MS
from . import supervision
from .chronology import timestamp
from .runnable import command, launcher
from .supervision_runtime import process_identity
from .state import save_state, state_lock

RESET_SCHEMA_VERSION = 2
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
#: How long the deliverer waits, after the clear, for Herdr to report the new
#: native session the clear started, and how often it looks.
CLEAR_SESSION_BUDGET_SEC = 30
CLEAR_SESSION_POLL_SEC = 1
#: How long the scheduler waits for the child's ready identity after spawn.
#: Script-owned and injectable (rules/ci-safety.md Always Watch CI).
READY_BUDGET_SEC = 15
READY_MAX_BYTES = 4096
READY_FD_ENV = "FOREMAN_RESET_READY_FD"
#: Owner-recovery markers stored in a failure `details` object. Missing means
#: the row is not in the never-typed recovery class (a claimed delivery already
#: ran, or a historical failure recorded before this contract).
RECOVERY_ELIGIBLE = "eligible"
RECOVERY_SCHEDULED = "scheduled"
RECOVERY_REFUSED = "refused"
RECOVERY_FAILED = "failed"
RECOVERY_TERMINAL = frozenset({RECOVERY_REFUSED, RECOVERY_FAILED})
#: The detail keys a failure record keeps. Herdr and composer errors can carry
#: raw subprocess output or pane text; the record keeps identifiers only.
FAILURE_DETAIL_KEYS = frozenset({"pane_id", "stow", "record", "status", "pid", "lock", "kind", "reconciled",
                                 "reconciled_at", "schema_version", "reason", "recovery", "recovery_pid",
                                 "recovery_identity"})
RESUME_OPENING = "Foreman resume after a planned round-boundary reset."
RESUME_TEMPLATE = (
    RESUME_OPENING + " Your earlier conversation is gone by design. Run the "
    "herdr-foreman skill; every command below is complete and runnable as written, "
    "and every other launcher command takes the same `{flags}`. Before "
    "anything else: run `{tl} memory-show {flags} --id {stow}` and read its "
    "required files in order; run `{tl} supervision-bind {flags}`, "
    "`{tl} supervision-resume {flags}`, `{tl} supervision-status {flags}` "
    "and `{tl} supervision-drain {flags}`; then "
    "`{tl} foreman-queue {flags}`. Then take SKILL.md Step 17's Resume Route: Step 1, Step 2, "
    "then the continuation step the stow's unresolved work names, in place of Step 5. "
    "Load each decision's records before making it, with "
    "`{tl} load-set {flags} --decision <plan|brief|gate|diagnose> --task <task>` or "
    "`{tl} load-set {flags} --decision wake --enrollment <enrollment-id>`."
)


def resume_prompt(stow, state, *, config=None, herdr_bin=None):
    """The prompt a fresh context resumes from, carrying every non-default owner setting."""
    flags = "--state " + shlex.quote(state)
    if config:
        flags += " --config " + shlex.quote(config)
    if herdr_bin:
        flags += " --herdr-bin " + shlex.quote(herdr_bin)
    return RESUME_TEMPLATE.format(tl="bash " + shlex.quote(launcher()), stow=shlex.quote(stow), flags=flags)


OWNER_RECOVERY = ("Do not run `{}` again for this stow. The owner recovers a never-typed failed reset "
                  "automatically under the same idle, native-session, empty-composer and process-identity "
                  "checks as a healthy deliverer; `{}` names the recovery state. The next round resets "
                  "from a new stow.").format(command("foreman-reset"), command("catch-up"))
OPERATOR_RECOVERY = ("Do not run `{}` again for this stow. Look at the pane first: if a foreman resumed "
                     "from this reset is running there, run the printed reconcile command and clear nothing. "
                     "Otherwise recover under skills/herdr-foreman/references/team-operation.md Working Memory: "
                     "clear the foreman's pane, then paste the resume prompt saved in this reset's record. "
                     "The next round resets from a new stow.").format(command("foreman-reset"))


class ResetEnded(UsageError):
    """This stow's one `foreman-reset` ended; owner recovery or operator look-first follows the row."""

    code = "reset_ended"


class ResetRecordNewer(StateError):
    """The reset record was written by a newer build; read as no prior reset, never written."""

    code = "reset_record_newer"


class ResetRecordUnusable(StateError):
    """The reset record is unreadable or fails validation; preserved untouched."""

    code = "reset_record_unusable"


class SessionChanged(HerdrError):
    """The foreman's pane no longer holds the native session bound at supervision-bind."""

    code = "reset_session_changed"


def record_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".foreman-reset.json")


def announce_ready(*, probe=process_identity):
    """Write this process's runtime identity to the scheduler's ready pipe, if one was given.

    No-op when `FOREMAN_RESET_READY_FD` is unset, so in-process CLI tests and a
    recoverer started without a handshake still run. The child closes the write
    descriptor after one JSON line; a missing, unreadable or unannouncable
    identity fails visibly rather than hanging the parent.
    """
    raw = os.environ.pop(READY_FD_ENV, None)
    if raw is None:
        return None
    try:
        fd = int(raw)
    except ValueError:
        raise StateError("Reset ready descriptor {} is not an integer; the deliverer did not announce identity.".format(raw),
                         {"pid": os.getpid()}) from None
    try:
        identity = probe(os.getpid())
        if identity is None:
            raise StateError("This reset deliverer (pid {}) exited before it could announce a ready identity; nothing was sent.".format(
                os.getpid()), {"pid": os.getpid()})
        payload = json.dumps({"pid": os.getpid(), "identity": identity["identity"]}, separators=(",", ":")) + "\n"
        os.write(fd, payload.encode("ascii"))
    except OSError as exc:
        raise StateError("Cannot write reset ready identity for pid {}: {}.".format(os.getpid(), exc),
                         {"pid": os.getpid()}) from None
    finally:
        os.close(fd)
    return identity


def read_ready_identity(read_fd, expected_pid, *, probe=process_identity, budget_sec=READY_BUDGET_SEC,
                        clock=time.monotonic, child=None, wait=None):
    """Read the child's `{pid, identity}` line, then prove it matches `Popen.pid` and a live probe.

    `wait(fd, remaining)` replaces `select` in tests. `child.poll` is checked when
    given so a death before readiness fails instead of waiting out the budget.
    """
    deadline = clock() + budget_sec
    chunks = []
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise StateError("Reset deliverer (pid {}) did not announce a ready identity within {}s; nothing was sent.".format(
                expected_pid, budget_sec), {"pid": expected_pid})
        if child is not None:
            ended = child.poll()
            if ended is not None and not chunks:
                raise StateError("Reset deliverer (pid {}) exited {} before announcing a ready identity; nothing was sent.".format(
                    expected_pid, ended), {"pid": expected_pid})
        if wait is None:
            readable, _, _ = select.select([read_fd], [], [], max(remaining, 0))
            if not readable:
                raise StateError("Reset deliverer (pid {}) did not announce a ready identity within {}s; nothing was sent.".format(
                    expected_pid, budget_sec), {"pid": expected_pid})
            data = os.read(read_fd, READY_MAX_BYTES)
        else:
            data = wait(read_fd, remaining)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data or sum(len(part) for part in chunks) >= READY_MAX_BYTES:
            break
    raw = b"".join(chunks).strip()
    if not raw:
        raise StateError("Reset deliverer (pid {}) closed its ready pipe without an identity; nothing was sent.".format(
            expected_pid), {"pid": expected_pid})
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StateError("Reset deliverer (pid {}) announced a malformed ready identity; nothing was sent.".format(
            expected_pid), {"pid": expected_pid}) from None
    if (not isinstance(payload, dict) or set(payload) != {"pid", "identity"}
            or type(payload["pid"]) is not int or payload["pid"] != expected_pid
            or not isinstance(payload["identity"], str) or not payload["identity"]):
        raise StateError("Reset deliverer (pid {}) announced a mismatched ready identity; nothing was sent.".format(
            expected_pid), {"pid": expected_pid})
    announced = {"pid": expected_pid, "identity": payload["identity"]}
    live = probe(expected_pid)
    if live is None:
        raise StateError("Reset deliverer (pid {}) exited before its ready identity could be proved live; nothing was sent.".format(
            expected_pid), {"pid": expected_pid})
    if live != announced:
        raise StateError("Reset deliverer (pid {}) announced {} but the live process is {}; nothing was sent.".format(
            expected_pid, announced, live), {"pid": expected_pid})
    return announced


def _failure_copy(row):
    """The recovery instruction an ended row carries: owner while never-typed recovery is open, operator otherwise."""
    if row["status"] == "failed" and _recovery_state(row) in {RECOVERY_ELIGIBLE, RECOVERY_SCHEDULED}:
        return OWNER_RECOVERY
    return OPERATOR_RECOVERY


def _recovery_state(row):
    result = row.get("result") or {}
    details = result.get("details") if isinstance(result, dict) else None
    if not isinstance(details, dict):
        return None
    value = details.get("recovery")
    return value if isinstance(value, str) else None


def _recovery_open(row):
    """A never-typed `failed` row that has not yet spent its one owner-recovery attempt."""
    return row["status"] == "failed" and _recovery_state(row) == RECOVERY_ELIGIBLE


def _mark_eligible(result):
    result["details"]["recovery"] = RECOVERY_ELIGIBLE
    return result


#: `interrupted` is a delivery that failed after its first keystroke: the
#: pane may be cleared or half-prompted, so it is never retried automatically.
#: `reconciled` is a reset the operator closed through `reconcile` after its
#: deliverer was gone without recording an outcome, confirming the foreman
#: resumed.
STATUSES = frozenset({"scheduled", "delivering", "delivered", "failed", "interrupted", "reconciled"})
#: Schema 1 rows lack `native_session`; schema 2 adds it (#523).
ROW_FIELDS_V1 = frozenset({"schema_version", "pane_id", "stow", "status", "scheduled_at", "options", "process", "result"})
ROW_FIELDS = ROW_FIELDS_V1 | {"native_session"}
OPTION_FIELDS = frozenset({"config", "herdr_bin"})
SESSION_KINDS = ("id", "path")
#: The agents whose native session supervision-bind accepts (supervision_runtime.bind_current).
SESSION_AGENTS = ("claude", "codex")


def _version(value, expected=RESET_SCHEMA_VERSION):
    """A stored schema version is this exact integer; JSON `true` and `1.0` are not."""
    return type(value) is int and value == expected


def _valid_session(value):
    """A recorded native session: `{"kind", "value"}` as supervision-bind stored it."""
    return (isinstance(value, dict) and set(value) == {"kind", "value"} and value["kind"] in SESSION_KINDS
            and isinstance(value["value"], str) and bool(value["value"]))


def _migrate(document):
    """Upgrade a schema-1 record in memory; `_open` persists it.

    A schema-1 row never recorded the bound native session, so it migrates
    with `native_session: null`, and a deliverer that claims such a row
    refuses before any keystroke. Every schema-1 row is validated against
    its own shape first; one that fails leaves the record unusable.
    """
    rows = document.get("resets")
    if not isinstance(rows, list) or not all(_valid_row(row, 1) for row in rows):
        return False
    for row in rows:
        row.update(schema_version=RESET_SCHEMA_VERSION, native_session=None)
        if row["status"] == "delivered":
            row["result"]["schema_version"] = RESET_SCHEMA_VERSION
    document["schema_version"] = RESET_SCHEMA_VERSION
    return True


def _alive(process, probe=None):
    """Whether the recorded deliverer is still that exact process, not a reused pid."""
    return process is not None and (probe or process_identity)(process["pid"]) == process


def _load(path):
    """The reset record in this build's shape, and whether it was migrated from schema 1.

    A newer `schema_version` is data this build lags, not corruption
    (rules/stateful-artifacts.md Migration Policy). A write refuses it;
    `_readable` takes it as no usable prior reset.
    """
    unusable = ("Reset record {} is {}. It is left untouched; the operator restores a valid file from its own backup "
                "before any reset.")
    if path.is_symlink():
        raise ResetRecordUnusable(unusable.format(path, "a link, not the owner's file"), {"record": str(path)})
    if not path.exists():
        return {"schema_version": RESET_SCHEMA_VERSION, "resets": []}, False
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
                               "coding-policy plugin, then run `{}`.".format(path, version, RESET_SCHEMA_VERSION,
                                                                          command("foreman-reset")),
                               {"record": str(path), "schema_version": version})
    migrated = _version(version, 1)
    if migrated and not _migrate(document):
        raise ResetRecordUnusable("Reset record {} is malformed. It is left untouched; the operator restores a valid file "
                                  "from its own backup before any reset.".format(path), {"record": str(path)})
    version = document.get("schema_version")
    rows = document.get("resets")
    if (not _version(version) or not isinstance(rows, list) or not all(_valid_row(row) for row in rows)
            or len({(row["pane_id"], row["stow"]) for row in rows}) != len(rows)):
        raise ResetRecordUnusable("Reset record {} is malformed. It is left untouched; the operator restores a valid file "
                                  "from its own backup before any reset.".format(path), {"record": str(path)})
    return document, migrated


def _open(path):
    """The reset record, a schema-1 one upgraded and rewritten; the caller holds the record lock.

    The owner rewrites a migrated record at once (rules/stateful-artifacts.md
    Migration Policy). A deliverer of the schema-1 build still running then
    reads a newer record and cannot record its outcome; its row stays
    `scheduled` or `delivering`, and once that process is gone `outstanding`
    names the `foreman-reset-reconcile` command, the recovery for any outcome
    the record could not take.
    """
    document, migrated = _load(path)
    if migrated:
        save_state(path, document)
    return document


def _records(path):
    """The reset record to write through; a newer one refuses, untouched."""
    return _open(path)


def _readable(path):
    """The reset record for a read, or None when a newer build wrote it."""
    try:
        return _open(path)
    except ResetRecordNewer:
        return None


def _valid_row(row, version=RESET_SCHEMA_VERSION):
    """Every documented field of that schema version, typed, with the result shape its status requires."""
    fields = ROW_FIELDS_V1 if version == 1 else ROW_FIELDS
    if not isinstance(row, dict) or set(row) != fields or not _version(row["schema_version"], version):
        return False
    # Null only on a row migrated from schema 1, which never recorded it.
    if version != 1 and row["native_session"] is not None and not _valid_session(row["native_session"]):
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
                and _version(result["schema_version"], version)
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


def _settle(document, row, state_path, alive):
    """Replay a live, delivered or reconciled reset; finalize any other, then refuse it for the operator.

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
        result = failure(lost, row["stow"], state, **row["options"])
        if row["status"] == "scheduled":
            _mark_eligible(result)
            row.update(status="failed", result=result)
        else:
            row.update(status="interrupted", result=result)
    return None, changed


def _refuse(row, state_path, cause=None):
    raise ResetEnded("The reset from stow {} ended {}{}; the pane may already be cleared. {}".format(
        row["stow"], row["status"], " ({})".format(cause) if cause else "", _failure_copy(row)),
        {"record": str(record_path(state_path)), "resume_prompt": row["result"]["resume_prompt"]})


def replay(state_path, plan, *, alive=_alive):
    """The existing reset for (pane, stow), or None when this stow never reset.

    Read before every precondition the reset itself changes: once a reset
    ran, its stow's reads and the supervision state legitimately change, and a
    retry still replays. Reading the stow and supervision, and checking the
    caller's pane, still come first (`cli.cmd_foreman_reset`).
    A reset that is neither live, delivered nor reconciled is finalized and refused.
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


def schedule(state_path, plan, at, start, *, native_session, alive=_alive, probe=None, options=None,
             start_recovery=None):
    """Record one reset for (pane, stow) and start its deliverer exactly once.

    `native_session` is the foreman's session bound at supervision-bind
    (`bound_session`); the deliverer types only while the pane still holds it.
    `start()` launches the deliverer and returns its pid after the child-ready
    handshake. The record lock is held until the deliverer's process identity
    is saved, and a deliverer claims only the row carrying its own identity. A
    launch or handshake failure, or a deliverer that is already gone when
    probed, finishes the row `failed` with the resume prompt, marks it eligible
    for one owner recovery, and optionally starts that recoverer (`start_recovery`)
    before re-raising.
    """
    try:
        timestamp(at, "Reset scheduled_at")
    except UsageError:
        raise UsageError("The reset time {!r} is not an ISO-8601 timestamp with a timezone; nothing was scheduled.".format(at),
                         {"at": at}) from None
    if not _valid_session(native_session):
        raise UsageError("The reset for stow {} carries no bound native session; run `{}` from the foreman's pane, then `{}`. "
                         "Nothing was scheduled.".format(plan.get("stow"), command("supervision-bind"), command("foreman-reset")),
                         {"stow": plan.get("stow")})
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
               "options": dict(options or {}), "process": None, "result": None, "native_session": dict(native_session)}
        if not _valid_row(row):
            raise UsageError("The reset for stow {} would not validate as a reset row; nothing was scheduled.".format(
                plan.get("stow")), {"row": row})
        document["resets"].append(row)
        save_state(path, document)
        try:
            pid = start()
            # The deliverer waits on this lock to claim, so it is alive to be identified.
            # `start()` has already waited for the child's ready identity; this probe
            # is the fresh live proof saved on the row.
            identity = (probe or process_identity)(pid)
            if identity is None:
                raise StateError("The reset deliverer (pid {}) exited before it could be identified; nothing was sent.".format(pid), {"pid": pid})
            row["process"] = identity
        except ForemanError as exc:
            result = failure(exc, plan["stow"], str(Path(state_path).expanduser().resolve()), **(options or {}))
            _mark_eligible(result)
            row.update(status="failed", result=result)
            save_state(path, document)
            _begin_recovery(row, start_recovery, probe or process_identity)
            save_state(path, document)
            _refuse(row, state_path, exc.message)
        save_state(path, document)
        return {**row, "replayed": False}


def _begin_recovery(row, start_recovery, probe):
    """Spawn the one owner recoverer for an eligible never-typed failure, while the caller holds the record lock.

    The recoverer announces ready without taking this lock, then waits to claim.
    A spawn or handshake failure records `recovery: failed` and does not raise:
    the original failure stays the durable outcome, and catch-up names it.
    """
    if start_recovery is None or not _recovery_open(row):
        return
    try:
        pid = start_recovery(row)
        identity = probe(pid)
        details = row["result"]["details"]
        details["recovery"] = RECOVERY_SCHEDULED
        details["recovery_pid"] = pid
        if identity is not None:
            details["recovery_identity"] = identity["identity"]
            row["process"] = identity
        else:
            details["recovery"] = RECOVERY_FAILED
    except ForemanError:
        row["result"]["details"]["recovery"] = RECOVERY_FAILED


#: Error codes whose messages this owner writes itself. Any other error, a
#: Herdr or composer failure above all, can carry raw subprocess output or pane
#: text in its message; the record keeps a generic line and the log keeps it.
OWN_MESSAGE_CODES = frozenset({"usage_error", "state_error", "reset_ended", "reset_record_newer", "reset_record_unusable",
                               "reset_session_changed"})


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


def reconcile_command(state_path, pane_id, stow, outcome):
    """The complete, runnable repair command for one reset."""
    return command("foreman-reset-reconcile --state {} --pane {} --stow {} --outcome {}".format(
        shlex.quote(str(Path(state_path).expanduser().resolve())), shlex.quote(pane_id), shlex.quote(stow), outcome))


TERMINAL_FAILURES = frozenset({"failed", "interrupted"})


def _recovery_needed(state_path, row):
    """Catch-up copy for a never-typed `failed` row, including in-flight owner recovery."""
    state = _recovery_state(row)
    if state == RECOVERY_SCHEDULED:
        return ("Owner recovery is in progress for pane {}: a recoverer is delivering the saved resume prompt. "
                "Do not paste it and do not run `{}` again for this stow.".format(
                    row["pane_id"], command("foreman-reset")))
    if state == RECOVERY_REFUSED:
        reason = (row["result"]["details"] or {}).get("reason")
        return ("Owner recovery refused to type into pane {} ({}). The old context is untouched. "
                "Look at the pane first: {}".format(row["pane_id"], reason or "busy, replaced, occupied or interrupted",
                                                    OPERATOR_RECOVERY))
    if state == RECOVERY_FAILED:
        return ("Owner recovery of the never-typed reset in pane {} failed. Look at the pane first: {}".format(
            row["pane_id"], OPERATOR_RECOVERY))
    if state == RECOVERY_ELIGIBLE:
        return ("The deliverer stopped before typing in pane {}, so the old context is still there. "
                "The owner recovers it automatically; do not run `{}` again for this stow. {}".format(
                    row["pane_id"], command("foreman-reset"), OWNER_RECOVERY))
    return OPERATOR_RECOVERY


def _outstanding_would_rewrite(row, alive, start_recovery):
    """Whether catch-up must take the record lock to finalize or spawn recovery for this latest row."""
    if row["status"] == "scheduled" and not alive(row["process"]):
        return True
    if start_recovery is not None and _recovery_open(row):
        return True
    return (row["status"] == "failed" and _recovery_state(row) == RECOVERY_SCHEDULED
            and not alive(row["process"]))


def _finalize_outstanding_row(row, state_path, alive, start_recovery, probe):
    """Rewrite a latest row that catch-up can close or recover; True when the record changed."""
    if row["status"] == "scheduled" and not alive(row["process"]):
        lost = StateError("The reset deliverer for stow {} exited without finishing.".format(row["stow"]),
                          {"process": row["process"]})
        result = failure(lost, row["stow"], str(Path(state_path).expanduser().resolve()), **row["options"])
        _mark_eligible(result)
        row.update(status="failed", result=result)
        _begin_recovery(row, start_recovery, probe)
        return True
    if _recovery_open(row):
        _begin_recovery(row, start_recovery, probe)
        return _recovery_state(row) != RECOVERY_ELIGIBLE
    if (row["status"] == "failed" and _recovery_state(row) == RECOVERY_SCHEDULED
            and not alive(row["process"])):
        # The one recoverer died before claiming; that attempt is spent.
        row["result"]["details"]["recovery"] = RECOVERY_FAILED
        return True
    return False


def _outstanding_item(state_path, path, row, alive):
    """One catch-up row, or None when this latest reset does not need attention."""
    if row["status"] in TERMINAL_FAILURES:
        prompt = row["result"]["resume_prompt"]
        if row["status"] == "interrupted":
            needed = ("Look at pane {} first: if a foreman resumed from this reset is running there, run `{}` "
                      "and clear nothing. Otherwise: {}".format(
                          row["pane_id"], reconcile_command(state_path, row["pane_id"], row["stow"], "delivered"),
                          OPERATOR_RECOVERY))
        else:
            needed = _recovery_needed(state_path, row)
    elif row["status"] == "delivering" and not alive(row["process"]):
        prompt = None
        failed = reconcile_command(state_path, row["pane_id"], row["stow"], "failed")
        delivered = reconcile_command(state_path, row["pane_id"], row["stow"], "delivered")
        needed = ("The deliverer stopped mid-delivery and its outcome is unknown. Look at pane {}: if a resumed "
                  "foreman is running there, run `{}`; otherwise run `{}` "
                  "and recover from the saved resume prompt.".format(row["pane_id"], delivered, failed))
    else:
        return None
    return {"pane_id": row["pane_id"], "stow": row["stow"], "status": row["status"], "record": str(path),
            "needed": needed, "resume_prompt": prompt, "recovery": _recovery_state(row)}


def _unusable_outstanding(path, exc):
    return [{"pane_id": None, "stow": None, "status": "record_unusable", "record": str(path),
             "needed": exc.message, "resume_prompt": None, "recovery": None}]


def outstanding(state_path, *, alive=_alive, start_recovery=None, probe=None):
    """The foreman resets that still need attention, from the record.

    The record is the durable blocker: `foreman-reset` writes the row before
    anything else can fail, and every later failure lands on it. For each pane,
    the latest reset is listed when it ended `failed` or `interrupted`, or when
    it never reached an outcome and its deliverer is gone. A later `delivered`
    or `reconciled` reset for the pane supersedes an older failure. An unreadable
    record is itself outstanding.

    A dead `scheduled` row typed nothing: this finalizes it `failed` and marks
    it eligible for one owner recovery. `start_recovery`, when given, spawns
    that recoverer while the record lock is held (the child announces ready
    without taking the lock, then waits to claim). A dead `delivering` row may
    have typed and stays operator look-first, named with the reconcile command.

    The record lock is taken only when a row must be rewritten (dead scheduled,
    owner-recovery spawn, dead recoverer) or a schema-1 record must be upgraded.
    A missing record is a no-op read: no lock file, no directory created.
    """
    path = record_path(state_path)
    probe = probe or process_identity
    try:
        document, migrated = _load(path)
    except ResetRecordNewer:
        return []
    except ResetRecordUnusable as exc:
        return _unusable_outstanding(path, exc)
    latest = {}
    for row in document["resets"]:
        latest[row["pane_id"]] = row
    if migrated or any(_outstanding_would_rewrite(row, alive, start_recovery) for row in latest.values()):
        try:
            with state_lock(path):
                document = _readable(path)
                if document is None:
                    return []
                latest = {}
                for row in document["resets"]:
                    latest[row["pane_id"]] = row
                changed = False
                items = []
                for row in latest.values():
                    if _finalize_outstanding_row(row, state_path, alive, start_recovery, probe):
                        changed = True
                    item = _outstanding_item(state_path, path, row, alive)
                    if item is not None:
                        items.append(item)
                if changed:
                    save_state(path, document)
                return items
        except ResetRecordUnusable as exc:
            return _unusable_outstanding(path, exc)
    items = []
    for row in latest.values():
        item = _outstanding_item(state_path, path, row, alive)
        if item is not None:
            items.append(item)
    return items


RECONCILE_OUTCOMES = ("delivered", "failed")


def reconcile(state_path, plan, outcome, at, *, alive=_alive):
    """Close a reset whose deliverer is gone without an outcome; the operator says which one happened.

    The owner's repair for a record that could not say how a delivery ended.
    A `scheduled` row (`failed` only) or a `delivering` row whose deliverer is
    no longer that process qualifies, and so does an `interrupted` row the
    operator saw resume (`delivered` only). A live deliverer is still working,
    and any other finished row already has its outcome. `failed` records the failure with the resume prompt built
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
    copy = OWNER_RECOVERY if (result.get("details") or {}).get("recovery") == RECOVERY_ELIGIBLE else OPERATOR_RECOVERY
    if (result.get("details") or {}).get("recovery") in RECOVERY_TERMINAL:
        copy = OPERATOR_RECOVERY
    return ResetEnded("The reset from stow {} did not complete ({}); {} holds the cause and the resume prompt. {}".format(
        stow, result["error"], record, copy),
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
    """Move this deliverer's scheduled reset to `delivering` and return the claimed row; None when it is not the owner.

    `process` is the caller's own identity; a reused pid carries another one.
    The deliverer starts while `schedule` still holds the record lock, so it
    waits for that lock rather than failing on it.
    """
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "scheduled" or row["process"] != process:
            return None
        row["status"] = "delivering"
        save_state(path, document)
        return copy.deepcopy(row)


def unclaimed_reason(state_path, plan, process, *, sleep=time.sleep, clock=time.monotonic):
    """Why `claim` returned None: same-pid identity mismatch vs any other non-owner.

    A duplicate deliverer with another pid must not fail the scheduled owner's
    row. The scheduled child whose argv changed after spawn is that owner: its
    pid matches and its identity does not.
    """
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "scheduled" or row["process"] is None:
            return "not_owner"
        if row["process"]["pid"] == process["pid"] and row["process"] != process:
            return "identity_mismatch"
        return "not_owner"


def claim_recovery(state_path, plan, process, *, sleep=time.sleep, clock=time.monotonic):
    """Move an eligible never-typed `failed` reset to `delivering` under this recoverer's identity.

    Overwrites `process` with the recoverer's runtime identity so a launcher→runtime
    argv change can still own the one recovery attempt. A spawned recoverer whose
    identity was saved as `recovery: scheduled` claims only as that process. A row
    that is no longer recoverable, or already delivering, is left alone.
    """
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None or row["status"] != "failed":
            return None
        state = _recovery_state(row)
        if state == RECOVERY_ELIGIBLE:
            pass
        elif state == RECOVERY_SCHEDULED and row.get("process") == process:
            pass
        else:
            return None
        row.update(status="delivering", process=dict(process), result=None)
        if not _valid_row(row):
            raise ResetRecordUnusable("The recovery claim for stow {} does not match the reset record's shape; it was not recorded.".format(
                plan["stow"]), {"record": str(path)})
        save_state(path, document)
        return copy.deepcopy(row)


def refuse_recovery(state_path, plan, result, *, sleep=time.sleep, clock=time.monotonic):
    """Record that owner recovery did not type; the row stays `failed` with `recovery` terminal."""
    path = record_path(state_path)
    with _waiting_lock(path, sleep=sleep, clock=clock):
        document = _records(path)
        row = _row(document, plan)
        if row is None:
            raise ResetRecordUnusable("Reset record {} holds no reset for stow {} in pane {}; the recovery refusal was not recorded.".format(
                path, plan["stow"], plan["pane_id"]), {"record": str(path)})
        if row["status"] == "delivering":
            row.update(status="failed", result=result)
            if not _valid_row(row):
                raise ResetRecordUnusable("The recovery refusal for stow {} does not match the reset record's shape; it was not recorded.".format(
                    plan["stow"]), {"record": str(path)})
            save_state(path, document)
        return row["status"]


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
            stored = copy.deepcopy(result)
            _mark_eligible(stored)
            row.update(status="failed", result=stored)
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
    if stow["id"] == "latest":
        # `memory-show --id latest` selects the newest stow, so the resume prompt could not name this one.
        raise UsageError("Stow id 'latest' is a selector for `{}`, so the resume prompt cannot name it exactly. "
                         "Record the handoff under another stow id before resetting.".format(
                             command("memory-show --id latest")), {"stow": "latest"})
    if not stow["reset_ready"]:
        raise UsageError("Stow {} is not reset-ready: a required read changed or a gap names no task. Record a new stow before resetting.".format(
            stow["id"]), {"stow": stow["id"]})
    binding = supervision_data.get("binding")
    if binding is None:
        raise UsageError("No foreman is bound to this state; run `{}` from the foreman's pane before resetting.".format(
            command("supervision-bind")), {})
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
        raise UsageError("The foreman cannot stop yet: {} unhandled event(s), {} active assignment(s) without a covering hold. Handle the events and save a handoff hold with `{}` (a user pause does not qualify) before resetting.".format(
            len(events), len(active), command("supervision-hold")), {"events": len(events), "active": active})
    return {"pane_id": pane, "stow": stow["id"]}


def bound_session(supervision_data):
    """The foreman's native session as supervision-bind stored it: `{"kind", "value"}`."""
    identity = (supervision_data.get("binding") or {}).get("identity") or {}
    session = {key: identity.get(key) for key in ("kind", "value")}
    if not _valid_session(session):
        raise UsageError("The supervision binding names no native session for the foreman; run `{}` from the foreman's "
                         "pane before resetting.".format(command("supervision-bind")), {})
    return session


def pane_session(client, pane_id):
    """The native session Herdr reports for the pane now, in the binding's form, or None when it names none.

    The same source `supervision-bind` reads, held to the same proof
    (supervision_runtime.bind_current): a supported agent reported by its own
    Herdr integration. A transcript path is canonicalized the way the binding
    stored it.
    """
    pane = client.pane_get(pane_id)
    ref = pane.get("agent_session") if isinstance(pane, dict) else None
    if (not isinstance(pane, dict) or pane.get("pane_id") != pane_id or not isinstance(ref, dict)
            or ref.get("agent") not in SESSION_AGENTS or ref.get("source") != "herdr:" + ref["agent"]
            or ref.get("kind") not in SESSION_KINDS or not isinstance(ref.get("value"), str) or not ref["value"]):
        return None
    value = ref["value"]
    if ref["kind"] == "path":
        if not Path(value).is_absolute():
            return None
        try:
            value = str(supervision.canonical(value))
        except (OSError, RuntimeError, ValueError):
            # A path that cannot be resolved (a link loop, an unreadable
            # ancestor, an embedded NUL) names no session this reset can match.
            return None
    return {"kind": ref["kind"], "value": value}


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


def foreground_processes(client, pane_id):
    """The pane's foreground processes as identities, or None when Herdr reports none usable.

    Each is `supervision_runtime.process_identity`: the pid with a digest of
    its start time and command line, so a reused pid or a process that
    exec'd in place reads as another process.
    """
    info = client.pane_process_info(pane_id)
    processes = info.get("foreground_processes") if isinstance(info, dict) else None
    if not isinstance(processes, list) or not processes:
        return None
    identities = []
    for process in processes:
        pid = process.get("pid") if isinstance(process, dict) else None
        if type(pid) is not int or pid <= 0:
            return None
        identity = process_identity(pid)
        if identity is None:
            return None
        identities.append(identity)
    return sorted(identities, key=lambda identity: (identity["pid"], identity["identity"]))


def _cleared_session(client, pane_id, before, processes, *, sleep, clock,
                     budget_sec=CLEAR_SESSION_BUDGET_SEC, poll_sec=CLEAR_SESSION_POLL_SEC):
    """The new native session the clear started, once Herdr reports it for the pane.

    A pane still reporting the bound session, or none, is polled until the
    budget is spent; the clear then proved no new session, and nothing
    further is sent. A new session is the clear's only while the pane's
    foreground processes are still `processes`, the ones found before the
    first keystroke; any other is a replacement, refused like a changed session.
    """
    deadline = clock() + budget_sec
    while True:
        current = pane_session(client, pane_id)
        if current is not None and current != before:
            if foreground_processes(client, pane_id) != processes:
                raise SessionChanged("The foreman's pane {} holds a new native session under another process after the "
                                     "clear, so it is not the session the clear started and the resume prompt was not "
                                     "sent. {}".format(pane_id, OPERATOR_RECOVERY),
                                     {"pane_id": pane_id, "reason": "native_session_changed"})
            return current
        if clock() >= deadline:
            raise HerdrError("The foreman's pane {} reported no new native session within {}s of the clear, so the resume "
                             "prompt was not sent. {}".format(pane_id, budget_sec, OPERATOR_RECOVERY),
                             {"pane_id": pane_id, "reason": "clear_session_unchanged"})
        sleep(poll_sec)


class DeliveryInterrupted(HerdrError):
    """A delivery that failed after typing into the pane; never retried automatically."""


class SessionInterrupted(DeliveryInterrupted):
    """The pane's native session changed after typing began; the record keeps `reset_session_changed`."""

    code = SessionChanged.code


def deliver(client, agents, pane_id, stow, state, *, native_session, still_ready=lambda: True, sleep=time.sleep,
            clock=time.monotonic, warn=None, budget_sec=IDLE_BUDGET_SEC, poll_sec=IDLE_POLL_SEC,
            settle_sec=COMPOSER_SETTLE_SEC, options=None):
    """Wait for the foreman's pane to go idle, then clear it and send the resume prompt.

    `still_ready()` re-checks the stow right before the clear; a stow that
    changed while the deliverer waited stops the reset with nothing sent.
    `native_session` is the row's bound session: every keystroke of the clear
    command, extra Enters included, refuses unless the pane still holds it. A
    null one, from a row migrated off schema 1, refuses before any keystroke.
    Once the clear is consumed, the new session Herdr reports for the pane is
    pinned, and every keystroke of the resume prompt refuses unless the pane
    still holds that one.
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
    # The session every keystroke must find in the pane: the bound one until
    # send_command confirms the clear consumed (no single Enter proves it
    # submitted; Codex's first only accepts autocomplete), then the new one
    # the clear started, once Herdr reports it.
    expected = [native_session]
    # The foreground processes the first keystroke found; the clear keeps them.
    processes = []

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
        # A same-name, same-kind replacement is another session (#523).
        if expected[0] is None or pane_session(client, pane_id) != expected[0]:
            raise SessionChanged("The foreman's pane {} no longer holds the native session {}{}, so the reset stopped. {}".format(
                pane_id, "its supervision binding recorded" if expected[0] is native_session else "the clear started",
                "" if expected[0] is not None else " (this reset recorded none)", OPERATOR_RECOVERY),
                {"pane_id": pane_id, "reason": "native_session_changed"})
        if not processes:
            found = foreground_processes(client, pane_id)
            if found is None:
                raise HerdrError("Herdr reports no foreground process for the foreman's pane {}, so the clear could not be "
                                 "tied to it; nothing was sent. {}".format(pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
            processes.append(found)
        typed.append(True)

    try:
        outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec,
                               before_input=guard)
        if not outcome["screen_changed"]:
            raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared and nothing further was sent. Check the clear command configured for kind {} in pane {}. {}".format(
                agent.clear_prompt, agent.kind, pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
        client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
        sleep(settle_sec)
        expected[0] = _cleared_session(client, pane_id, native_session, processes[0], sleep=sleep, clock=clock)
        landing = send_message(client, agent, resume_prompt(stow, state, **(options or {})), RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                               settle_sec=settle_sec, before_input=guard)
        if not (landing["landed"] and landing["started"]):
            raise HerdrError("The foreman was cleared but the resume prompt did not {} in pane {}. {}".format(
                "land" if not landing["landed"] else "start a turn", pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
    except ForemanError as exc:
        if typed and not isinstance(exc, DeliveryInterrupted):
            wrapper = SessionInterrupted if isinstance(exc, SessionChanged) else DeliveryInterrupted
            raise wrapper("{} The pane was already typed into, so this reset is not retried. {}".format(
                exc.message, OPERATOR_RECOVERY), exc.details) from None
        raise
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "stow": stow, "agent": agent.name,
            "cleared": True, "resume": landing}
