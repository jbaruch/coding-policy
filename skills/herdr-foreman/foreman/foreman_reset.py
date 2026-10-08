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
  active enrollment or a handoff hold whose id matches the stow. The scheduled
  live deliverer becomes the Stop proof after this preflight succeeds

The foreman's runtime mechanics (clear command, slash delivery, composer
glyphs) come from a configured worker of the same kind; Herdr names the kind
and the foreman's agent name from its own pane.

One claimed delivery attempt per pane and stow, never retried automatically.
The scheduler verifies the loaded child's identity and durable claim before
returning success. Lost preclaim children are reaped and replaced within the
startup allowance while the record lock prevents them from ever claiming;
the private reset log retains their identity and failure reason.
`<state>.foreman-reset.json` records each scheduled reset (`schedule`), and
the deliverer claims it before sending anything (`claim`). A retry of a live,
delivered or reconciled reset replays the record and spawns nothing. Any
other reset is finalized `failed` (nothing typed) or `interrupted` (typing began) with the
resume prompt the operator pastes, under the Working Memory recovery
carve-out; the next round resets from a new stow. Before every keystroke the
deliverer re-reads the stow and the pane, and refuses unless the stow is
still reset-ready and the same agent is still idle.

The pane's name and runtime kind do not identify the foreman: the operator can
replace the process in that pane with another session of the same name and
kind while the deliverer waits (#523). `foreman-reset` records the native
session bound at `supervision-bind` on the row, and every keystroke of the
clear command, extra Enters included, refuses unless the pane still holds that
session.
The clear itself starts a new native session by design. Claude/Grok report it
before the continuation; Codex can report it only after a prompt is submitted.
All routes use the same UserPromptSubmit gate before model execution. The
claimed child saves the foreground process pins it found before clearing;
the hook verifies the exact input, unchanged stow, live claim and new native
session, then atomically rebinds supervision and records input acceptance.
Every keystroke checks the same foreground identities. Codex alone permits
the old integration hint until its real continuation triggers the native hook.
No delivered outcome is recorded without that hook's durable acceptance.
"""

import copy
import fcntl
import json
import os
import select
import shlex
import subprocess
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

RESET_SCHEMA_VERSION = 3
#: Startup is acknowledged by the loaded child, not guessed from its launcher
#: PID. A macOS Python launcher execs the framework runtime in the same PID.
STARTUP_BUDGET_SEC = 10
STARTUP_ATTEMPTS = 2
STARTUP_MESSAGE_BYTES = 1024
STARTUP_REAP_SEC = 5
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
#: The detail keys a failure record keeps. Herdr and composer errors can carry
#: raw subprocess output or pane text; the record keeps identifiers only.
FAILURE_DETAIL_KEYS = frozenset({"pane_id", "stow", "record", "status", "pid", "lock", "kind", "reconciled",
                                 "reconciled_at", "schema_version", "reason"})
RESUME_OPENING = "Foreman resume after a planned round-boundary reset."
RESET_RECEIPT_PREFIX = "Herdr reset input receipt: "
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


def guarded_resume(stow, state, native_session, processes, *, options=None):
    """Native wire input; the pre-prompt hook consumes the handoff once.

    The envelope identifies existing owner records, never authorizes itself.
    It contains no pane text, provider output or credentials.
    """
    receipt = {"schema_version": 1, "state": str(supervision.canonical(state)), "stow": stow,
               "native_session": native_session, "foreground": processes}
    return resume_prompt(stow, str(supervision.canonical(state)), **(options or {})) + "\n" + RESET_RECEIPT_PREFIX + json.dumps(receipt, sort_keys=True)


def accepted_resume(state, pane_id, stow, before):
    """The hook's new owner binding, not a screen or stale integration hint."""
    binding = supervision.load(state)["binding"]
    document, _ = _load(record_path(state), migrate_legacy=False)
    row = _row(document, {"pane_id": pane_id, "stow": stow})
    who = binding["identity"] if binding is not None else {}
    current = {key: who.get(key) for key in ("kind", "value")}
    if (row is not None and row["status"] == "delivering" and row["accepted_session"] == current
            and who.get("pane_id") == pane_id and _valid_session(current) and current != before):
        return current
    return None


OPERATOR_RECOVERY = ("Do not run `{}` again for this stow. The operator recovers the foreman under "
                     "skills/herdr-foreman/references/team-operation.md Working Memory: clear the foreman's pane, then paste the "
                     "resume prompt saved in this reset's record. The next round resets from a new stow.").format(
                         command("foreman-reset"))


class ResetEnded(UsageError):
    """This stow's one reset attempt failed or was interrupted; only the operator recovers it."""

    code = "reset_ended"


class ResetRecordNewer(StateError):
    """The reset record was written by a newer build; read as no prior reset, never written."""

    code = "reset_record_newer"


class ResetRecordOlder(StateError):
    """A read-only evaluator cannot use or migrate a legacy reset record."""

    code = "reset_record_older"


class ResetRecordUnusable(StateError):
    """The reset record is unreadable or fails validation; preserved untouched."""

    code = "reset_record_unusable"


class SessionChanged(HerdrError):
    """The foreman's pane no longer holds the native session bound at supervision-bind."""

    code = "reset_session_changed"


class StartupFailed(StateError):
    """The detached child supplied no verified startup/claim acknowledgment."""

    code = "reset_startup_failed"


def startup_notify(fd, phase, process):
    """Send the child's post-import identity and, later, its durable claim proof.

    The inherited pipe is private to this launch. No argv, pane text or
    provider output crosses it. The child closes it after the second message.
    """
    if fd is None:
        return
    try:
        payload = json.dumps({"phase": phase, "process": process}).encode("ascii") + b"\n"
        if os.write(fd, payload) != len(payload):
            raise StartupFailed("The reset startup acknowledgment was incomplete; continue foreground supervision and inspect the reset log.", {})
    except OSError:
        raise StartupFailed("The reset startup pipe is unavailable; continue foreground supervision and inspect the reset log.", {}) from None
    finally:
        if phase != "ready":
            os.close(fd)


class DetachedReset:
    """One owned detached child, with readiness and post-lock claim handshakes.

    `ready` validates the child's final runtime identity against a live probe.
    `claimed` waits for the owner-record claim after the scheduler unlocks.
    `abort` is called only while that record is locked and still unclaimed;
    an unreaped Popen child cannot have its PID reused during termination.
    Failed pre-claim attempts are appended to the existing private reset log.
    """

    def __init__(self, argv, sink, cwd):
        self.sink = os.fdopen(os.dup(sink.fileno()), "ab")
        self.buffer = b""
        self.identity = None
        self.fd, writer = os.pipe()
        try:
            self.child = subprocess.Popen([*argv, "--startup-fd", str(writer)],
                                          stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                                          start_new_session=True, cwd=cwd, pass_fds=(writer,))
        except OSError:
            os.close(self.fd)
            self.sink.close()
            raise
        finally:
            os.close(writer)

    def _failed(self, reason):
        return StartupFailed("Reset child {} did not establish continuation ({}); continue foreground supervision and inspect the reset log.".format(
            self.child.pid, reason), {"pid": self.child.pid, "reason": reason})

    def _receive(self):
        if self.fd is None:
            raise self._failed("startup_pipe_closed")
        deadline = time.monotonic() + STARTUP_BUDGET_SEC
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._failed("startup_timeout")
            try:
                readable, _, _ = select.select([self.fd], [], [], remaining)
                if not readable:
                    raise self._failed("startup_timeout")
                chunk = os.read(self.fd, STARTUP_MESSAGE_BYTES)
            except OSError:
                raise self._failed("startup_pipe_unavailable") from None
            if not chunk:
                raise self._failed("startup_child_exited")
            self.buffer += chunk
            if len(self.buffer) > STARTUP_MESSAGE_BYTES:
                raise self._failed("startup_message_oversized")
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            return json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise self._failed("startup_message_invalid") from None

    def ready(self, probe):
        message = self._receive()
        identity = message.get("process") if isinstance(message, dict) else None
        if (not isinstance(message, dict) or set(message) != {"phase", "process"} or message["phase"] != "ready"
                or not isinstance(identity, dict) or set(identity) != {"pid", "identity"}
                or type(identity["pid"]) is not int or identity["pid"] != self.child.pid
                or not isinstance(identity["identity"], str) or len(identity["identity"]) != 64
                or any(ch not in "0123456789abcdef" for ch in identity["identity"])
                or probe(self.child.pid) != identity):
            raise self._failed("startup_identity_unverified")
        self.identity = identity
        return identity

    def claimed(self):
        if self._receive() != {"phase": "claimed", "process": self.identity}:
            raise self._failed("startup_claim_unverified")

    def abort(self):
        if self.child.poll() is None:
            try:
                self.child.terminate()
            except ProcessLookupError:
                pass  # The owned child exited between poll and terminate; wait reaps it.
            try:
                self.child.wait(timeout=STARTUP_REAP_SEC)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=STARTUP_REAP_SEC)
        self.close()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.sink.close()

    def record_loss(self, attempt, exc):
        try:
            self.sink.write(json.dumps({"startup_attempt": attempt, "phase": "preclaim", "process": self.identity,
                                       "pid": self.child.pid, "reason": exc.details.get("reason", exc.code)}).encode("ascii") + b"\n")
            self.sink.flush()
            os.fsync(self.sink.fileno())
        except OSError:
            raise StateError("Could not retain reset startup evidence; restore access to the reset log before another scheduling call. Continue foreground supervision.",
                             {"pid": self.child.pid, "reason": "startup_log_unavailable"}) from None

    def abandon(self, attempt, exc):
        """Retain preclaim loss evidence and reap even when logging fails."""
        try:
            self.record_loss(attempt, exc)
        finally:
            self.abort()


def record_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".foreman-reset.json")


#: `interrupted` is a delivery that failed after its first keystroke: the
#: pane may be cleared or half-prompted, so it is never retried automatically.
#: `reconciled` is a reset the operator closed through `reconcile` after its
#: deliverer was gone without recording an outcome, confirming the foreman
#: resumed.
STATUSES = frozenset({"scheduled", "delivering", "delivered", "failed", "interrupted", "reconciled"})
#: Schema 1 rows lack `native_session`; schema 2 adds it (#523).
ROW_FIELDS_V1 = frozenset({"schema_version", "pane_id", "stow", "status", "scheduled_at", "options", "process", "result"})
ROW_FIELDS_V2 = ROW_FIELDS_V1 | {"native_session"}
ROW_FIELDS = ROW_FIELDS_V2 | {"foreground", "accepted_session"}
OPTION_FIELDS = frozenset({"config", "herdr_bin"})
SESSION_KINDS = ("id", "path")
#: The agents whose native session supervision-bind accepts (supervision_runtime.bind_current).
SESSION_AGENTS = ("claude", "codex", "grok")


def _version(value, expected=RESET_SCHEMA_VERSION):
    """A stored schema version is this exact integer; JSON `true` and `1.0` are not."""
    return type(value) is int and value == expected


def _valid_session(value):
    """A recorded native session: `{"kind", "value"}` as supervision-bind stored it."""
    return (isinstance(value, dict) and set(value) == {"kind", "value"} and value["kind"] in SESSION_KINDS
            and isinstance(value["value"], str) and bool(value["value"]))


def _valid_foreground(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, dict) and set(item) == {"pid", "identity"}
                    and type(item["pid"]) is int and item["pid"] > 0
                    and isinstance(item["identity"], str) and bool(item["identity"]) for item in value)
            and len({item["pid"] for item in value}) == len(value))


def arm_resume(state, pane, stow, before, processes):
    """Only the claimed child saves the pre-clear process pins for its input hook."""
    path = record_path(state)
    with state_lock(path):
        document = _records(path)
        row = _row(document, {"pane_id": pane, "stow": stow})
        me = process_identity(os.getpid())
        if (row is None or row["status"] != "delivering" or row["process"] != me
                or row["native_session"] != before or not _valid_foreground(processes)
                or row["foreground"] is not None):
            raise UsageError("The reset input cannot be armed by this process. Preserve its owner record and continue foreground supervision.", {})
        row["foreground"] = copy.deepcopy(processes)
        save_state(path, document)


def _migrate(document):
    """Upgrade schema 1/2 in memory; `_open` persists schema 3.

    A schema-1 row never recorded the bound native session, so it migrates
    with `native_session: null`, and a deliverer that claims such a row
    refuses before any keystroke. Every legacy row is validated against
    its own shape first; one that fails leaves the record unusable.
    """
    rows = document.get("resets")
    version = document.get("schema_version")
    if version not in (1, 2) or not isinstance(rows, list) or not all(_valid_row(row, version) for row in rows):
        return False
    for row in rows:
        row.update(schema_version=RESET_SCHEMA_VERSION, foreground=None, accepted_session=None)
        if version == 1:
            row["native_session"] = None
        if row["status"] == "delivered":
            row["result"]["schema_version"] = RESET_SCHEMA_VERSION
    document["schema_version"] = RESET_SCHEMA_VERSION
    return True


def _alive(process, probe=None):
    """Whether the recorded deliverer is still that exact process, not a reused pid."""
    return process is not None and (probe or process_identity)(process["pid"]) == process


def _load(path, *, migrate_legacy=True):
    """The reset record in this build's shape, and whether it needs owner migration.

    A newer `schema_version` is data this build lags, not corruption
    (rules/stateful-artifacts.md Migration Policy). A write refuses it;
    `_readable` takes it as no usable prior reset. Read-only evaluators
    disable legacy migration and refuse the old shape before transforming it.
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
    migrated = _version(version, 1) or _version(version, 2)
    if migrated and not migrate_legacy:
        assert type(version) is int  # migration detection above narrows the legacy version
        rows = document.get("resets")
        if (not isinstance(rows, list) or not all(_valid_row(row, version) for row in rows)
                or len({(row["pane_id"], row["stow"]) for row in rows}) != len(rows)):
            raise ResetRecordUnusable("Reset record {} is malformed. It is left untouched; the operator restores a valid file "
                                      "from its own backup before any reset.".format(path), {"record": str(path)})
        raise ResetRecordOlder("Reset record {} is an older schema and supplies no usable Stop proof. It is left untouched; "
                               "run `{}` for the same owner state to migrate and rewrite it before resetting.".format(
                                   path, command("catch-up")),
                               {"record": str(path), "schema_version": version})
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
    """The reset record, a legacy one upgraded and rewritten; the caller holds the record lock.

    The owner rewrites a migrated record at once (rules/stateful-artifacts.md
    Migration Policy). A deliverer of a legacy build still running then
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
    fields = ROW_FIELDS_V1 if version == 1 else ROW_FIELDS_V2 if version == 2 else ROW_FIELDS
    if not isinstance(row, dict) or set(row) != fields or not _version(row["schema_version"], version):
        return False
    # Null only on a row migrated from schema 1, which never recorded it.
    if version != 1 and row["native_session"] is not None and not _valid_session(row["native_session"]):
        return False
    if version == 3 and row["foreground"] is not None and not _valid_foreground(row["foreground"]):
        return False
    if version == 3 and row["accepted_session"] is not None and not _valid_session(row["accepted_session"]):
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


def schedule(state_path, plan, at, start, *, native_session, alive=_alive, probe=None, options=None):
    """Record one reset for (pane, stow), with bounded proved-preclaim recovery.

    `native_session` is the foreman's session bound at supervision-bind
    (`bound_session`); the deliverer types only while the pane still holds it.
    Production `start()` returns a DetachedReset. Its loaded runtime sends
    its identity before waiting for this lock, then acknowledges the durable
    claim after unlock. Preclaim loss revokes the identity under the lock,
    reaps only that owned child, and retries within STARTUP_ATTEMPTS. The log
    retains each lost attempt. Once claimed, no automatic retry is possible.
    Injected PID-only launchers retain the existing test/embedder contract.
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
    managed = None
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        with state_lock(path):
            document = _records(path)
            row = _row(document, plan)
            if attempt == 1:
                if row is not None:
                    live, changed = _settle(document, row, state_path, alive)
                    if changed:
                        save_state(path, document)
                    if live is not None:
                        return live
                    _refuse(row, state_path)
                row = {"schema_version": RESET_SCHEMA_VERSION, **plan, "status": "scheduled", "scheduled_at": at,
                       "options": dict(options or {}), "process": None, "result": None, "native_session": dict(native_session),
                       "foreground": None, "accepted_session": None}
                if not _valid_row(row):
                    raise UsageError("The reset for stow {} would not validate as a reset row; nothing was scheduled.".format(
                        plan.get("stow")), {"row": row})
                document["resets"].append(row)
                save_state(path, document)
            if row is None or row["status"] != "scheduled" or row["process"] is not None:
                raise StateError("Reset startup ownership changed; continue foreground supervision and inspect {} before any retry.".format(path),
                                 {"record": str(path)})
            managed = None
            try:
                started = start()
                managed = started if isinstance(started, DetachedReset) else None
                if managed is not None:
                    identity = managed.ready(probe or process_identity)
                else:
                    identity = (probe or process_identity)(started)
                    if identity is None:
                        raise StateError("The reset deliverer (pid {}) exited before it could be identified; nothing was sent.".format(started), {"pid": started})
                row["process"] = identity
            except ForemanError as exc:
                if managed is not None:
                    managed.abandon(attempt, exc)
                if isinstance(exc, StartupFailed) and managed is not None and attempt < STARTUP_ATTEMPTS:
                    continue
                row.update(status="failed", result=failure(exc, plan["stow"], str(Path(state_path).expanduser().resolve()), **(options or {})))
                save_state(path, document)
                _refuse(row, state_path, exc.message)
            save_state(path, document)
            scheduled = copy.deepcopy(row)
        if managed is None:
            return {**scheduled, "replayed": False}
        claim_error = None
        try:
            managed.claimed()
        except StartupFailed as exc:
            claim_error = exc
        with state_lock(path):
            document = _records(path)
            row = _row(document, plan)
            if row is None or row["process"] != scheduled["process"]:
                managed.close()
                raise StateError("Reset startup ownership changed; continue foreground supervision and inspect {} before any retry.".format(path),
                                 {"record": str(path)})
            if row["status"] == "delivered" or (row["status"] == "delivering" and alive(row["process"])):
                managed.close()
                return {**copy.deepcopy(row), "replayed": False}
            if row["status"] != "scheduled":
                managed.close()
                _live, changed = _settle(document, row, state_path, alive)
                if changed:
                    save_state(path, document)
                _refuse(row, state_path)
            # No claim occurred. Holding the same lock bars the old child
            # from ever claiming while it is reaped and its identity revoked.
            exc = claim_error or managed._failed("startup_claim_not_recorded")
            managed.abandon(attempt, exc)
            row["process"] = None
            if attempt == STARTUP_ATTEMPTS:
                row.update(status="failed", result=failure(exc, plan["stow"], str(Path(state_path).expanduser().resolve()), **(options or {})))
            save_state(path, document)
            if row["status"] == "failed":
                _refuse(row, state_path, exc.message)
    raise StateError("Reset startup exhausted its owner recovery; continue foreground supervision and inspect {}.".format(path),
                     {"record": str(path)})


#: Error codes whose messages this owner writes itself. Any other error, a
#: Herdr or composer failure above all, can carry raw subprocess output or pane
#: text in its message; the record keeps a generic line and the log keeps it.
OWN_MESSAGE_CODES = frozenset({"usage_error", "state_error", "reset_ended", "reset_record_newer", "reset_record_unusable",
                               "reset_session_changed", "reset_startup_failed"})


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


def outstanding(state_path, *, alive=_alive):
    """The foreman resets that still need the operator, read from the record alone.

    The record is the durable blocker: `foreman-reset` writes the row before
    anything else can fail, and every later failure lands on it. For each pane,
    the latest reset needs the operator when it ended `failed` or
    `interrupted`, or when it never reached an outcome and its deliverer is
    gone. A later `delivered` or `reconciled` reset for the pane supersedes an older failure.
    An unreadable record is itself outstanding. Nothing is written except the
    owner's rewrite of a legacy record (`_open`).
    """
    path = record_path(state_path)
    try:
        try:
            document, migrated = _load(path)
        except ResetRecordNewer:
            document, migrated = None, False
        if migrated:
            # Only a legacy record takes the owner lock, to be rewritten; any
            # other read writes nothing, not even a lock file.
            with state_lock(path):
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
                          "still holds its old context. Run `{}`; `{}` then shows the saved resume "
                          "prompt for recovery.".format(row["pane_id"], failed, command("catch-up")))
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


def _handoff_held(data, stow=None):
    """Exactly one current handoff covers the active work and matches the stow."""
    holds = supervision.current_holds(data, "handoff")
    return len(holds) == 1 and (stow is None or holds[0]["id"] == stow)


def stop_coverage(state_path, supervision_data, *, probe=process_identity):
    """Read-only proof that a current handoff has a live matching reset deliverer.

    A handoff hold prepares reset preflight. It authorizes Stop only after the
    reset record binds the same hold/stow id, pane, native session, and exact
    live deliverer process. Legacy records supply no usable prior state;
    this Stop-path reader never migrates or rewrites the record.
    """
    holds = supervision.current_holds(supervision_data, "handoff")
    if not holds:
        return {"eligible": False, "state": "handoff_missing"}
    if len(holds) != 1:
        return {"eligible": False, "state": "handoff_ambiguous"}
    binding = supervision_data.get("binding") or {}
    identity = binding.get("identity") or {}
    pane = identity.get("pane_id")
    native_session = {key: identity.get(key) for key in ("kind", "value")}
    path = record_path(state_path)
    document, _migrated = _load(path, migrate_legacy=False)
    stow = holds[0]["id"]
    matching_stow = [row for row in document["resets"] if row["stow"] == stow]
    if not matching_stow:
        return {"eligible": False, "state": "reset_missing", "record": str(path)}
    matching_pane = [row for row in matching_stow if row["pane_id"] == pane]
    if not matching_pane:
        return {"eligible": False, "state": "reset_pane_mismatch", "record": str(path)}
    row = matching_pane[-1]
    if row["native_session"] != native_session:
        return {"eligible": False, "state": "reset_native_session_mismatch", "record": str(path),
                "stow": row["stow"]}
    if row["status"] not in ("scheduled", "delivering"):
        return {"eligible": False, "state": "reset_" + row["status"], "record": str(path),
                "stow": row["stow"]}
    if not _alive(row["process"], probe):
        return {"eligible": False, "state": "reset_deliverer_not_live", "record": str(path),
                "stow": row["stow"]}
    return {"eligible": True, "state": "scheduled_continuation", "record": str(path),
            "stow": row["stow"], "process": row["process"]}


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
    matching_handoff = _handoff_held(supervision_data, stow["id"])
    if events or (active and not matching_handoff):
        raise UsageError("The foreman cannot prepare this reset yet: {} unhandled event(s), {} active assignment(s) without a matching handoff hold. Handle the events and save a handoff hold whose id is the stow id with `{}` (a user pause does not qualify) before resetting.".format(
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
    Once the clear is consumed, eager native sessions are pinned before input.
    Codex permits its old integration hint under unchanged process pins until
    the real continuation triggers its pre-prompt gate. All routes require the
    new binding and acceptance recorded by that gate before delivery succeeds.
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
    resuming = [False]
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
        current = pane_session(client, pane_id)
        deferred = resuming[0] and agent.kind == "codex" and expected[0] == native_session
        if deferred and current is not None and current != native_session:
            expected[0] = current
        if expected[0] is None or current != expected[0]:
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
        elif foreground_processes(client, pane_id) != processes[0]:
            raise SessionChanged("The foreman's pane {} is under another process before typing; nothing more was sent. {}".format(
                pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id, "reason": "native_session_changed"})
        typed.append(True)

    try:
        outcome = send_command(client, agent, pane_id, agent.clear_prompt, sleep=sleep, warn=warn, settle_sec=settle_sec,
                               before_input=guard)
        if not outcome["screen_changed"]:
            raise HerdrError("The foreman consumed {} but its screen did not change, so its context was not cleared and nothing further was sent. Check the clear command configured for kind {} in pane {}. {}".format(
                agent.clear_prompt, agent.kind, pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
        client.agent_wait(agent.name, until=SETTLE_STATES, timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS)
        sleep(settle_sec)
        resuming[0] = True
        if agent.kind != "codex":
            expected[0] = _cleared_session(client, pane_id, native_session, processes[0], sleep=sleep, clock=clock)
        arm_resume(state, pane_id, stow, native_session, processes[0])
        prompt = guarded_resume(stow, state, native_session, processes[0], options=options)
        landing = send_message(client, agent, prompt, RESUME_OPENING, pane_id=pane_id, sleep=sleep, warn=warn,
                               settle_sec=settle_sec, before_input=guard)
        if not (landing["landed"] and landing["started"]):
            raise HerdrError("The foreman was cleared but the resume prompt did not {} in pane {}. {}".format(
                "land" if not landing["landed"] else "start a turn", pane_id, OPERATOR_RECOVERY), {"pane_id": pane_id})
        # The real prompt triggers native hook/session events. No dummy
        # prompt, journal scrape or fabricated Herdr receipt substitutes.
        accepted = accepted_resume(state, pane_id, stow, native_session)
        if (accepted is None or foreground_processes(client, pane_id) != processes[0]
                or expected[0] != native_session and accepted != expected[0]):
            raise SessionChanged("The reset input hook did not verify the new foreman session; do not repeat this input. Restore the native hook and inspect the saved reset. {}".format(
                OPERATOR_RECOVERY), {"pane_id": pane_id, "reason": "reset_input_unverified"})
    except ForemanError as exc:
        if typed and not isinstance(exc, DeliveryInterrupted):
            wrapper = SessionInterrupted if isinstance(exc, SessionChanged) else DeliveryInterrupted
            raise wrapper("{} The pane was already typed into, so this reset is not retried. {}".format(
                exc.message, OPERATOR_RECOVERY), exc.details) from None
        raise
    return {"schema_version": RESET_SCHEMA_VERSION, "pane_id": pane_id, "stow": stow, "agent": agent.name,
            "cleared": True, "resume": landing}
