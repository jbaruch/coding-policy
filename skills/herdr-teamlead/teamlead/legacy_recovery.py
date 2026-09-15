"""Explicit owner recovery for duplicate pre-receipt ruling citations.

The recovery adds digest-bound receipts, retaining every checkpoint and all
attempt/authorization history. New checkpoints still consume the original
one-ruling allowance. Only version-2 citations can be recovered. Version-3
duplicates and all unrelated validation failures remain refusals.
"""

import copy
import hashlib
import json
import os
import stat
from pathlib import Path

from .errors import StateError, UsageError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_recoveries(store):
    """Validate immutable receipts against the entire original checkpoint rows."""
    from .recovery import authorization, text, validate_receipt

    records = store.get("legacy_ruling_recoveries")
    if not isinstance(records, list):
        raise UsageError("Legacy ruling recoveries must be an array; restore owner-written state.", {})
    checkpoints = {row["id"]: row for row in store["checkpoints"]}
    recovered = set()
    identities = set()
    for record in records:
        required = {"schema_version", "id", "task", "authorization", "backup", "checkpoints", "grants_future_attempts", "at"}
        if (not isinstance(record, dict) or set(record) != required
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["grants_future_attempts"] is not False):
            raise UsageError("Invalid legacy ruling recovery; restore its original owner receipt.", {})
        key = text(record["id"], "recovery identity")
        if key in identities:
            raise UsageError("Duplicate legacy recovery identity; restore its original owner receipt.", {})
        identities.add(key)
        text(record["at"], "recovery timestamp")
        authorization(record["authorization"])
        validate_receipt(record["backup"])
        rows = record["checkpoints"]
        if not isinstance(rows, dict) or len(rows) < 2:
            raise UsageError("A legacy recovery must bind multiple original citations; restore its receipt.", {})
        actual = {r["id"] for r in store["checkpoints"] if r["task"] == record["task"] and "judge_evidence" in r}
        if actual != set(rows):
            raise UsageError("Recovered task citations changed; review the original ledger before continuing.", {})
        for identifier, expected in rows.items():
            row = checkpoints.get(identifier)
            if (row is None or row["task"] != record["task"] or row["schema_version"] != 2
                    or "requested_by" in row or "judge_evidence" not in row
                    or expected != digest(row) or identifier in recovered):
                raise UsageError("Recovered legacy citations changed or overlap; restore the original checkpoints.", {})
            recovered.add(identifier)
    return recovered


def prepare(payload, request, at):
    """Build and fully validate a candidate without mutating the input."""
    from .recovery import authorization, migrate_store, text
    from .state import STATE_SCHEMA_VERSION, _NoUsableState, _validate

    if (not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA_VERSION
            or not isinstance(payload.get("recovery"), dict)
            or payload["recovery"].get("schema_version") not in {8, 9}):
        raise UsageError("Recovery requires state schema 6 with recovery schema 8 or 9; update the owner for other versions.", {})
    if not isinstance(request, dict) or set(request) != {"id", "state_sha256", "backup", "authorization"}:
        raise UsageError("Supply id, state_sha256, absolute backup path and the operator authorization source/quote.", {})
    text(request["id"], "recovery identity")
    authorization(request["authorization"])
    candidate = copy.deepcopy(payload)
    store = candidate["recovery"]
    migrate_store(store)
    groups = {}
    for row in store["checkpoints"]:
        if "judge_evidence" in row:
            groups.setdefault(row["task"], []).append(row)
    existing = validate_recoveries(store)
    added = []
    for task, rows in groups.items():
        if len(rows) < 2 or all(row["id"] in existing for row in rows):
            continue
        if any(row.get("schema_version") != 2 or "requested_by" in row for row in rows):
            raise UsageError("Only pre-receipt version-2 citations can be recovered; current operator rulings remain bounded.", {})
        record = {
            "schema_version": 1, "id": request["id"] + ":" + task, "task": task, "at": at,
            "authorization": copy.deepcopy(request["authorization"]),
            "backup": {"path": request["backup"], "sha256": request["state_sha256"]},
            "checkpoints": {row["id"]: digest(row) for row in rows}, "grants_future_attempts": False,
        }
        store["legacy_ruling_recoveries"].append(record)
        added.append(record)
    if not added:
        raise UsageError("No unrecovered duplicate legacy rulings exist; inspect the ledger before requesting recovery.", {})
    try:
        candidate, _ = _validate(candidate, Path(request["backup"]))
    except _NoUsableState as exc:
        raise UsageError("Legacy recovery leaves another validation failure: {}. Preserve state and recover that evidence first.".format(exc), {}) from None
    return candidate, added


def read_backup(path):
    """Read a regular private backup without following a replaced symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise UsageError("Backup must be a private regular file; restore its original permissions and bytes.", {})
        content = stream.read()
        if not os.path.samestat(info, os.lstat(path)):
            raise UsageError("Backup changed while being read; restore its original file before retrying.", {})
        return content


def recover_file(state_path, request_path, at, *, dry_run=False):
    """CLI owns the state lock; validate, back up exact bytes, then atomically save."""
    from .state import _NoUsableState, _validate, save_state

    state_path = Path(state_path).expanduser().resolve()
    try:
        raw = state_path.read_bytes()
        payload = json.loads(raw)
        request = json.loads(request_path.read_text())
    except (OSError, ValueError) as exc:
        raise StateError("Cannot read recovery input: {}. Restore the original ledger and request.".format(exc), {}) from None
    if not isinstance(request, dict):
        raise UsageError("Recovery request must be an object; supply the reviewed input fields.", {})
    # Exact retries verify the existing receipt and backed-up bytes, never
    # rewrite history or refresh the authorization.
    records = payload.get("recovery", {}).get("legacy_ruling_recoveries", []) if isinstance(payload, dict) else []
    matched = [r for r in records if isinstance(r, dict) and r.get("id", "").startswith(str(request.get("id")) + ":")]
    backup_name = request.get("backup")
    if not isinstance(backup_name, str) or not Path(backup_name).is_absolute():
        raise UsageError("Use an absolute new backup path outside the live state and its sidecars.", {})
    backup = Path(backup_name)
    if backup.resolve() == state_path or backup.is_symlink():
        raise UsageError("Backup must be a distinct regular file; choose a new recovery backup path.", {})
    if matched:
        if any(r["authorization"] != request.get("authorization") or r["backup"] != {
                "path": backup_name, "sha256": request.get("state_sha256")} for r in matched):
            raise UsageError("Recovery identity already has different input; preserve the original receipt.", {})
        try:
            _validate(copy.deepcopy(payload), state_path)
            if hashlib.sha256(read_backup(backup)).hexdigest() != request.get("state_sha256"):
                raise UsageError("Recovery backup changed; restore its original bytes before continuing.", {})
        except (_NoUsableState, OSError) as exc:
            raise StateError("Cannot verify completed recovery: {}. Restore its preserved evidence.".format(exc), {}) from None
        return {"ok": True, "replayed": True, "records": matched}
    if hashlib.sha256(raw).hexdigest() != request.get("state_sha256"):
        raise UsageError("The ledger changed after review; inspect its current bytes and prepare a new request.", {})
    candidate, records = prepare(payload, request, at)
    if dry_run:
        return {"ok": True, "dry_run": True, "records": records}
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        try:
            same = read_backup(backup) == raw
        except OSError as exc:
            raise StateError("Cannot verify existing backup: {}. Restore access before retrying.".format(exc), {}) from None
        if not same:
            raise UsageError("Backup already contains different bytes; preserve it and choose a new path.", {})
    except OSError as exc:
        raise StateError("Cannot create recovery backup: {}. Choose an accessible private directory.".format(exc), {}) from None
    try:
        unchanged = state_path.read_bytes() == raw
    except OSError as exc:
        raise StateError("Cannot recheck ledger: {}. Restore access before retrying; backup was preserved.".format(exc), {}) from None
    if not unchanged:
        raise StateError("Ledger changed during recovery; inspect it before retrying. Backup was preserved.", {})
    save_state(state_path, candidate)
    return {"ok": True, "replayed": False, "records": records}
