"""The teamlead state file: usage snapshots and the assignment ledger.

State is a hint, not authority. A snapshot records what an agent's budget
looked like when it was measured; it never substitutes for reading the agent's
live status before writing to it. `plan` may run off a stale snapshot on
purpose (planning has no side effects); `apply` always re-checks live status.

Schema (schema_version 1)::

    {
      "schema_version": 1,
      "snapshots":  [ <measure output>, ... ],   # newest last, capped at 20
      "assignments":[ {"at": <ISO-8601>, "role": <str>, "agent": <str>}, ... ]
    }

teamlead owns this file and is the only writer. Writes are atomic: a temp file
in the same directory followed by `os.replace`, so an interrupted run leaves
the previous state intact rather than a truncated file.
"""

import json
import os
import tempfile
from pathlib import Path

from .errors import StateError

STATE_SCHEMA_VERSION = 1
MAX_SNAPSHOTS = 20


def default_state_path():
    """`$XDG_STATE_HOME/teamlead/state.json`, falling back to `~/.local/state`."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "teamlead" / "state.json"


def empty_state():
    """A fresh, valid state document."""
    return {"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": []}


def _validate(payload, path):
    if not isinstance(payload, dict):
        raise StateError(
            "State file {} does not contain a JSON object - move it aside with "
            "`mv {} {}.bak` and rerun to start a fresh ledger.".format(path, path, path),
            {"path": str(path)},
        )
    version = payload.get("schema_version")
    if version != STATE_SCHEMA_VERSION:
        raise StateError(
            "State file {} has schema_version {!r}; this build owns version {}. "
            "Move it aside with `mv {} {}.bak` and rerun to start a fresh "
            "ledger.".format(path, version, STATE_SCHEMA_VERSION, path, path),
            {"path": str(path), "found": version, "expected": STATE_SCHEMA_VERSION},
        )
    for key in ("snapshots", "assignments"):
        if not isinstance(payload.get(key), list):
            raise StateError(
                "State file {} has a {!r} field that is not an array - move it "
                "aside with `mv {} {}.bak` and rerun.".format(path, key, path, path),
                {"path": str(path), "field": key},
            )
    return payload


def load_state(path):
    """Read the state file, or return a fresh document when it does not exist.

    A missing file and a never-written file are the same thing: no prior state.
    """
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
        raise StateError(
            "State file {} is not valid JSON ({} at line {} column {}) - move it "
            "aside with `mv {} {}.bak` and rerun to start a fresh ledger.".format(
                path, exc.msg, exc.lineno, exc.colno, path, path
            ),
            {"path": str(path)},
        ) from None

    return _validate(payload, path)


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
    """Append one role-to-agent assignment to the ledger."""
    state.setdefault("assignments", []).append({"at": at, "role": role, "agent": agent})
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
