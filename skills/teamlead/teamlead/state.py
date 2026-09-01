"""The teamlead state file: usage snapshots and the assignment ledger.

State is a hint, not authority. A snapshot records what an agent's budget
looked like when it was measured; it never substitutes for reading the agent's
live status before writing to it. `plan` may run off a stale snapshot on
purpose (planning has no side effects); `apply` always re-checks live status.

Schema (schema_version 1)::

    {
      "schema_version": 1,
      "snapshots":  [ <measure output>, ... ],   # newest last, capped at 20
      "assignments":[ {"schema_version": 1, "at": <ISO-8601>,
                       "role": <str>, "agent": <str>}, ... ]
    }

Every RECORD carries its own `schema_version`, not just the document: a ledger
row outlives the document it arrived in, and a version on the row is what makes
a later migration auditable row by row.

teamlead owns this file and is the only writer. Writes are atomic: a temp file
in the same directory followed by `os.replace`, so an interrupted run leaves
the previous state intact rather than a truncated file.

Reading follows one rule per direction:

* **Older** document or record: the owner migrates it through MIGRATIONS and
  rewrites the upgraded file. Nobody else migrates.
* **Newer** document or record: this build is the lagging reader, not the
  migrator. It takes the no-usable-prior-state path -- an empty document in
  memory, the file left exactly as found, a warning on stderr.
* **Corrupt** file: no usable prior state, same treatment. Discarding a
  snapshot ring costs one re-measure; overwriting an unread file costs the
  ledger.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from .errors import StateError

#: The version this build writes, for the document and for every record in it.
#: They move together: one owner, one file, one release train.
STATE_SCHEMA_VERSION = 1

#: The version a document or record carries when it has no `schema_version` at
#: all -- the pre-versioning shape. Reading an absent key as 0 is what lets the
#: migration chain start below the first stamped version.
UNVERSIONED = 0

MAX_SNAPSHOTS = 20


def default_state_path():
    """`$XDG_STATE_HOME/teamlead/state.json`, falling back to `~/.local/state`."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "teamlead" / "state.json"


def empty_state():
    """A fresh, valid state document."""
    return {"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": []}


class _NoUsableState(Exception):
    """Internal signal: this file cannot be read, and must not be written."""


def _warn(message):
    """Default warning sink. Diagnostics go to stderr; stdout stays JSON."""
    print("teamlead: {}".format(message), file=sys.stderr)


def _migrate_record_0_to_1(record):
    """Pre-versioning assignment row -> version 1: stamp it."""
    record["schema_version"] = 1
    return record


def _migrate_document_0_to_1(payload):
    """Pre-versioning document -> version 1.

    The pre-versioning shape is this one without the stamps, so the upgrade is
    the stamps: the document gains `schema_version`, and every assignment row
    gains its own. The entry earns its place as much for its shape as for its
    work -- adding a real 1->2 step later is one more row in MIGRATIONS, not a
    rewrite of the reader.
    """
    payload["schema_version"] = 1
    payload["assignments"] = [
        _migrate_record_0_to_1(record) if isinstance(record, dict) else record
        for record in payload.get("assignments", [])
    ]
    return payload


#: Document migrations, keyed by the version being upgraded FROM. Each value is
#: (version_produced, upgrade_callable). `_apply_migrations` walks the chain
#: until it reaches STATE_SCHEMA_VERSION, so a future 1->2 is one entry.
MIGRATIONS = {
    UNVERSIONED: (1, _migrate_document_0_to_1),
}

#: The same table for one assignment record, walked the same way.
RECORD_MIGRATIONS = {
    UNVERSIONED: (1, _migrate_record_0_to_1),
}


def _version_of(payload):
    """The declared version, with an absent key reading as UNVERSIONED."""
    version = payload.get("schema_version", UNVERSIONED)
    if isinstance(version, bool) or not isinstance(version, int):
        raise _NoUsableState("schema_version {!r} is not an integer".format(version))
    return version


def _apply_migrations(payload, table, label):
    """Walk `payload` up the migration chain. Returns (payload, migrated?)."""
    version = _version_of(payload)
    migrated = False
    seen = set()
    while version != STATE_SCHEMA_VERSION:
        if version > STATE_SCHEMA_VERSION:
            raise _NoUsableState(
                "{} is at schema_version {}; this build owns {}".format(
                    label, version, STATE_SCHEMA_VERSION
                )
            )
        if version in seen:
            raise _NoUsableState(
                "{} migration chain loops at version {}".format(label, version)
            )
        seen.add(version)
        step = table.get(version)
        if step is None:
            raise _NoUsableState(
                "{} is at schema_version {}, which no migration upgrades".format(
                    label, version
                )
            )
        produced, upgrade = step
        payload = upgrade(payload)
        version = produced
        migrated = True
    return payload, migrated


def _validate(payload, path):
    """Return (state, migrated?) or raise _NoUsableState.

    Every rejection here is a no-usable-prior-state signal, never an
    instruction to the operator to delete their ledger.
    """
    if not isinstance(payload, dict):
        raise _NoUsableState("the document is not a JSON object")

    payload, migrated = _apply_migrations(payload, MIGRATIONS, "state file")

    for key in ("snapshots", "assignments"):
        if not isinstance(payload.get(key), list):
            raise _NoUsableState("{!r} is not an array".format(key))

    rows = []
    for record in payload["assignments"]:
        if not isinstance(record, dict):
            raise _NoUsableState("an assignment row is not a JSON object")
        record, row_migrated = _apply_migrations(record, RECORD_MIGRATIONS, "an assignment row")
        migrated = migrated or row_migrated
        rows.append(record)
    payload["assignments"] = rows

    # A snapshot is a whole `measure` document and arrives already stamped. One
    # that is not an object, or is stamped ahead of this build, is the same
    # lagging-reader case as the document itself.
    for snapshot in payload["snapshots"]:
        if not isinstance(snapshot, dict):
            raise _NoUsableState("a snapshot entry is not a JSON object")
        found = _version_of(snapshot)
        if found > STATE_SCHEMA_VERSION:
            raise _NoUsableState(
                "a snapshot is at schema_version {}; this build owns {}".format(
                    found, STATE_SCHEMA_VERSION
                )
            )
    return payload, migrated


def load_state(path, warn=None):
    """Read the state file, migrating an older one and rewriting it.

    A missing file and a never-written file are the same thing: no prior state.
    So is a corrupt one, and so is one written by a newer build -- in both of
    those the file is left exactly as found, and the caller gets an empty
    document plus a warning on stderr. A tool failure (unreadable permissions,
    a directory in the way) still raises: that is not "no state", it is "this
    environment is wrong", and it carries a fix.
    """
    warn = warn or _warn
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()
    except PermissionError as exc:
        raise StateError(
            "State file {} is not readable: {} - fix its permissions with "
            "`chmod u+rw {}`.".format(path, exc.strerror, path),
            {"path": str(path)},
        ) from None
    except IsADirectoryError:
        raise StateError(
            "State path {} is a directory - point --state at the state.json "
            "file itself.".format(path),
            {"path": str(path)},
        ) from None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        warn(
            "state file {} is not valid JSON ({} at line {} column {}) - starting "
            "from an empty ledger; the file is left untouched.".format(
                path, exc.msg, exc.lineno, exc.colno
            )
        )
        return empty_state()

    try:
        state, migrated = _validate(payload, path)
    except _NoUsableState as exc:
        warn(
            "state file {}: {} - starting from an empty ledger; the file is left "
            "untouched.".format(path, exc)
        )
        return empty_state()

    if migrated:
        try:
            save_state(path, state)
        except StateError as exc:
            # The upgrade holds in memory even when the rewrite cannot land.
            warn(
                "state file {} migrated to schema_version {} in memory, but the "
                "rewrite failed ({}) - the next run migrates it again.".format(
                    path, STATE_SCHEMA_VERSION, exc
                )
            )
    return state


def save_state(path, state):
    """Write `state` atomically, creating the parent directory when needed."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, FileExistsError, NotADirectoryError) as exc:
        raise StateError(
            "Cannot create the state directory {}: {} - pass --state to point "
            "at a writable location.".format(path.parent, exc),
            {"path": str(path)},
        ) from None

    handle = None
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(state, handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp_name, str(path))
        temp_name = None
    except OSError as exc:
        raise StateError(
            "Cannot write the state file {}: {} - pass --state to point at a "
            "writable location.".format(path, exc),
            {"path": str(path)},
        ) from None
    finally:
        if handle is not None:
            handle.close()
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


def add_snapshot(state, snapshot):
    """Append `snapshot` and keep only the newest MAX_SNAPSHOTS entries."""
    snapshots = list(state.get("snapshots", []))
    snapshots.append(snapshot)
    state["snapshots"] = snapshots[-MAX_SNAPSHOTS:]
    return state


def add_assignment(state, at, role, agent):
    """Append one role-to-agent assignment to the ledger.

    The row carries its own `schema_version`, so a later migration can walk the
    ledger row by row rather than inferring a row's shape from the document.
    """
    state.setdefault("assignments", []).append(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "at": at,
            "role": role,
            "agent": agent,
        }
    )
    return state


def latest_snapshot(state):
    """The most recent snapshot, or None when nothing has been measured yet."""
    snapshots = state.get("snapshots", [])
    return snapshots[-1] if snapshots else None


def role_counts(state):
    """How many times each agent has previously held each role.

    Returns ``{role: {agent: count}}``. The planner uses it to break headroom
    ties toward the agent that has held the role least often, which spreads
    roles around instead of pinning one agent to `developer` forever.
    """
    counts = {}
    for record in state.get("assignments", []):
        role = record.get("role")
        agent = record.get("agent")
        if role is None or agent is None:
            continue
        per_role = counts.setdefault(role, {})
        per_role[agent] = per_role.get(agent, 0) + 1
    return counts
