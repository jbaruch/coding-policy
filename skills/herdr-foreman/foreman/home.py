"""Move the owner's state and config homes from `teamlead` to `foreman` (#501).

The skill, CLI and package were renamed from teamlead to foreman with no
alias. The operator's durable homes move with them:

- `$XDG_STATE_HOME/teamlead/` -> `$XDG_STATE_HOME/foreman/`
- `$XDG_CONFIG_HOME/teamlead/` -> `$XDG_CONFIG_HOME/foreman/`

Records keep their contents, field names and `schema_version`s. Two things
make this more than a directory move:

- Owner stores carry a `state_path` identity field that must equal the
  canonical state path, or the store refuses to load. `migrate` rewrites
  every `state_path` field holding the old canonical path to the new one.
  Nothing else in a record changes
- Records also quote old absolute paths as history: stow required reads,
  retrospective notes, attention evidence. Rewriting history is forbidden, so
  the old home is left as a symlink to the new one and every quoted path
  still resolves

A home is in one of five states (`status`):

- `absent`   -- neither directory exists
- `legacy`   -- only the old directory exists: migrate it
- `current`  -- the new directory exists and the old one is absent or a
                symlink to it. Migrating again finishes a move a crash
                interrupted (symlink, identity fields) and changes nothing else
- `split`    -- both exist as separate directories, or the old one is a
                symlink elsewhere. Refused, never merged
- `blocked`  -- the old path is neither a directory nor a symlink

Any other command whose default home is `legacy` refuses and names
`migrate-home` (`require_current`); it never starts an empty store at the new
path. A command given explicit `--state`/`--config` paths is unaffected.

Every other command holds the home guard, `$XDG_STATE_HOME/.foreman-home.lock`,
shared for its whole run (`guard`). The guard lives beside both homes, never
inside one, so it stays put while they move. `migrate` takes it exclusively
before it looks at either home and holds it through both moves: it refuses
while any command holds the guard, and a command that starts mid-migration is
refused instead of reading a half-moved home. It also refuses while any owner
lock in the old state home is held, for a running foreman older than the guard.
"""

import fcntl
import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .errors import StateError, UsageError
from .state import save_state

LEGACY = "teamlead"
CURRENT = "foreman"
STATE_FILE = "state.json"
GUARD = ".foreman-home.lock"


def roots(environ=None):
    environ = os.environ if environ is None else environ
    state = environ.get("XDG_STATE_HOME")
    config = environ.get("XDG_CONFIG_HOME")
    return {
        "state": Path(state) if state else Path.home() / ".local" / "state",
        "config": Path(config) if config else Path.home() / ".config",
    }


def pair(root):
    return root / LEGACY, root / CURRENT


def status(root):
    old, new = pair(root)
    if old.is_symlink():
        target = Path(os.path.realpath(old))
        if new.is_dir() and target == Path(os.path.realpath(new)):
            return "current"
        return "split"
    if old.exists() and not old.is_dir():
        return "blocked"
    if old.is_dir():
        return "split" if new.exists() else "legacy"
    return "current" if new.is_dir() else "absent"


@contextmanager
def guard(exclusive, environ=None):
    """Hold the home guard: exclusive for `migrate`, shared for every other command."""
    root = roots(environ)["state"]
    path = root / GUARD
    if not root.is_dir():
        # No state root means no home to move, and a migration has nothing to
        # take: creating the root here would be a write a read-only command
        # never makes.
        yield
        return
    try:
        handle = path.open("a", encoding="utf-8")
    except OSError as exc:
        raise StateError("Cannot open the home guard {}: {}. Restore access to {}, then run the command again; "
                         "nothing was read or written.".format(path, exc, root), {"guard": str(path)}) from None
    with handle:
        try:
            fcntl.flock(handle.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            if exclusive:
                raise UsageError("A foreman command is running (it holds {}). Stop every foreman and let running "
                                 "commands finish, then run migrate-home again; nothing was moved.".format(path),
                                 {"guard": str(path)}) from None
            raise UsageError("migrate-home is moving the foreman homes (it holds {}). Wait for it to finish, then run "
                             "this command again; nothing was read or written.".format(path), {"guard": str(path)}) from None
        except OSError as exc:
            raise StateError("Cannot lock the home guard {}: {}. Use a filesystem supporting process locks; nothing "
                             "was read or written.".format(path, exc), {"guard": str(path)}) from None
        yield


def require_current(kinds, environ=None):
    """Refuse a default home still at the legacy path; `kinds` names the defaults a command uses."""
    for kind, root in roots(environ).items():
        if kind not in kinds:
            continue
        state = status(root)
        if state == "legacy":
            old, new = pair(root)
            raise StateError("The {} home is still at {}. Stop every foreman, then run `foreman migrate-home` to move it "
                             "to {}; nothing was read or written.".format(kind, old, new), {"legacy": str(old), "current": str(new)})
        if state in ("split", "blocked"):
            raise StateError(_refusal(kind, root, state), {"legacy": str(pair(root)[0]), "current": str(pair(root)[1])})


def _refusal(kind, root, state):
    old, new = pair(root)
    if state == "blocked":
        return "The legacy {} home {} is not a directory; move it aside, then run migrate-home.".format(kind, old)
    return ("Both {} and {} exist as separate {} homes. They are never merged; keep the one holding the live "
            "records, move the other aside, then run migrate-home.".format(old, new, kind))


def _held_locks(directory):
    """Take every owner lock in the home without blocking; a held one refuses."""
    stack = ExitStack()
    for lock_path in sorted(directory.rglob("*.lock")):
        try:
            handle = stack.enter_context(lock_path.open("a", encoding="utf-8"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stack.close()
            raise UsageError("A foreman command holds {}. Stop every foreman and let running commands finish, then "
                             "run migrate-home again; nothing was moved.".format(lock_path), {"lock": str(lock_path)}) from None
        except OSError as exc:
            stack.close()
            raise StateError("Cannot lock {}: {}. Restore access to the state home, then run migrate-home again; "
                             "nothing was moved.".format(lock_path, exc), {"lock": str(lock_path)}) from None
    return stack


def _rewrite_identity(value, old, new):
    """Replace `state_path` fields equal to `old`; return (value, count)."""
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            if key == "state_path" and item == old:
                value[key] = new
                count += 1
            else:
                value[key], found = _rewrite_identity(item, old, new)
                count += found
        return value, count
    if isinstance(value, list):
        count = 0
        for index, item in enumerate(value):
            value[index], found = _rewrite_identity(item, old, new)
            count += found
        return value, count
    return value, 0


def _rewrite_home(home, old_state, new_state):
    rewritten = []
    for path in sorted(home.rglob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateError("Cannot read {} while moving the state home: {}. Restore the file, then run migrate-home "
                             "again; files already rewritten stay valid.".format(path, exc), {"path": str(path)}) from None
        document, count = _rewrite_identity(document, old_state, new_state)
        if count:
            save_state(path, document)
            rewritten.append({"path": str(path), "fields": count})
    return rewritten


def _move(kind, root, rewrite):
    old, new = pair(root)
    state = status(root)
    if state in ("split", "blocked"):
        raise UsageError(_refusal(kind, root, state), {"legacy": str(old), "current": str(new)})
    if state == "absent":
        return {"kind": kind, "status": "absent", "moved": False, "rewritten": []}
    moved = False
    with ExitStack() as stack:
        # Lock fds follow the directory through the rename.
        stack.enter_context(_held_locks(old if state == "legacy" else new))
        if state == "legacy":
            os.rename(old, new)
            moved = True
        if not old.is_symlink():
            os.symlink(new, old, target_is_directory=True)
        rewritten = []
        if rewrite:
            # Stores recorded the canonical path, so compare against the resolved root.
            base = root.resolve()
            rewritten = _rewrite_home(new, str(base / LEGACY / STATE_FILE), str(base / CURRENT / STATE_FILE))
    return {"kind": kind, "status": "current", "moved": moved, "legacy": str(old), "current": str(new), "rewritten": rewritten}


def migrate(environ=None):
    """Move both homes; idempotent, and a crash midway is finished by a re-run."""
    homes = roots(environ)
    with guard(True, environ):
        for kind, root in homes.items():
            state = status(root)
            if state in ("split", "blocked"):
                raise UsageError(_refusal(kind, root, state), {"legacy": str(pair(root)[0]), "current": str(pair(root)[1])})
        return {"schema_version": 1, "homes": [_move("state", homes["state"], True), _move("config", homes["config"], False)]}
