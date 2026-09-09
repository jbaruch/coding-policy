"""Durable, lead-authored retrospective notes, separate from dispatch state.

The index is the commit point: immutable note bytes are installed before its
atomic replacement. A pending transaction allows an identical interrupted
record to finish without treating an orphan note as completed coverage.
Readers never create files or migrate dispatch state. All times are injected.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

from .chronology import timestamp
from .errors import StateError, UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1
INTERVAL = timedelta(hours=24)
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TRIGGERS = frozenset({"daily", "transition"})
SECTIONS = ("Outcomes", "Quality", "Coordination", "Seats and models", "Improvements")


def canonical_state(path):
    return Path(path).expanduser().resolve()


def directory(path):
    return Path(str(canonical_state(path)) + ".retrospectives")


def lock(path):
    """Acquire after the dispatch-state lock; offline readers take neither."""
    return state_lock(directory(path) / "index.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc(value):
    return timestamp(value, "Retrospective time").astimezone(timezone.utc).isoformat()


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise UsageError("{} must be a non-empty string; supply the actual retrospective metadata.".format(label), {})
    return value


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise UsageError("Retrospective id must use 1-64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit.", {})
    return value


def file_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise StateError("Cannot read retrospective evidence {}: {}. Restore the file or explicitly record the evidence as unavailable.".format(path, exc), {}) from None


def receipt(path):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise UsageError("Retrospective evidence paths must be absolute; supply the original file path.", {})
    resolved = str(Path(path).resolve())
    data = file_bytes(resolved)
    return {"path": resolved, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def current_receipt(record):
    if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
        raise StateError("Malformed retrospective file receipt; restore its original metadata without rewriting history.", {})
    return receipt(record["path"]) == record


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError("Cannot read retrospective JSON {}: {}. Preserve its bytes and restore a valid backup before recording or dispatching.".format(path, exc), {}) from None


def empty(path):
    return {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)),
            "baseline_at": None, "records": [], "transitions": []}


def _malformed(label):
    raise StateError("Malformed retrospective {}; preserve its bytes and restore the original artifact.".format(label), {})


def validate_receipt(value):
    if (not isinstance(value, dict) or set(value) != {"path", "sha256", "size"}
            or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or type(value["size"]) is not int or value["size"] < 0):
        _malformed("file receipt")


def validate_coverage(value):
    if not isinstance(value, list):
        _malformed("coverage")
    for row in value:
        if (not isinstance(row, dict) or row.get("schema_version") != SCHEMA_VERSION
                or not isinstance(row.get("agent"), str) or not row["agent"]
                or type(row.get("first_start")) is not bool or type(row.get("transition_required")) is not bool
                or not isinstance(row.get("source"), dict) or not isinstance(row.get("target"), dict)):
            _malformed("coverage descriptor")
        source, target = row["source"], row["target"]
        if set(source) != {"assignment_index", "assignment_digest", "dispatch_id", "task", "role", "tier", "observation", "report", "unavailable"}:
            _malformed("coverage source")
        validate_observation(source["observation"])
        if source["report"] is not None:
            validate_receipt(source["report"])
        if set(target) != {"role", "model", "effort", "context", "task", "brief", "common"} or target["context"] not in {"start", "clear", "retain"}:
            _malformed("coverage target")
        for key in ("brief", "common"):
            if target[key] is not None:
                validate_receipt(target[key])


def validate_observation(value):
    if (not isinstance(value, dict) or set(value) != {"pane_id", "native", "process", "readiness", "shell"}
            or not isinstance(value["pane_id"], str) or not isinstance(value["process"], dict)
            or set(value["process"]) != {"pid", "argv"}
            or type(value["process"]["pid"]) is not int or value["process"]["pid"] <= 0
            or not isinstance(value["process"]["argv"], list)
            or not value["process"]["argv"] or any(not isinstance(arg, str) for arg in value["process"]["argv"])
            or not isinstance(value["readiness"], str) or type(value["shell"]) is not bool):
        _malformed("worker observation")


def validate_record(row):
    required = {"schema_version", "id", "completed_at", "period_start", "period_end", "triggers", "tasks", "participants", "unavailable", "sources", "note", "coverage", "input_digest"}
    if not isinstance(row, dict) or set(row) != required or row["schema_version"] != SCHEMA_VERSION:
        _malformed("record schema")
    identifier(row["id"])
    for key in ("completed_at", "period_start", "period_end"):
        utc(row[key])
    for key in ("triggers", "tasks", "participants"):
        if not isinstance(row[key], list) or any(not isinstance(item, str) or not item for item in row[key]):
            _malformed("record " + key)
    if not row["triggers"] or not set(row["triggers"]) <= TRIGGERS or not isinstance(row["unavailable"], dict):
        _malformed("record metadata")
    if not isinstance(row["sources"], list):
        _malformed("record sources")
    for source in row["sources"]:
        validate_receipt(source)
    validate_receipt(row["note"])
    validate_coverage(row["coverage"])
    if not isinstance(row["input_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["input_digest"]):
        _malformed("record identity")


def load(path, *, allow_pending=False):
    root = directory(path)
    index = root / "index.json"
    if not index.exists():
        if root.exists():
            leftovers = [item.name for item in root.iterdir() if item.name != "index.json.lock"]
            if leftovers and not (allow_pending and (root / "pending.json").is_file()):
                raise StateError("Retrospective index is missing beside saved artifacts; preserve them and resume its pending transaction or restore the index backup.", {})
        return empty(path)
    result = _json(index)
    if (not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION
            or result.get("state_path") != str(canonical_state(path))
            or not isinstance(result.get("records"), list) or not isinstance(result.get("transitions"), list)):
        raise StateError("Retrospective index has an unsupported schema or state identity; preserve its bytes and update the owner or restore its backup.", {})
    if result.get("baseline_at") is not None:
        utc(result["baseline_at"])
    ids = set()
    for record in result["records"]:
        validate_record(record)
        name = identifier(record.get("id"))
        if name in ids:
            raise StateError("Retrospective ids are duplicated; restore the original index before writing.", {})
        ids.add(name)
        utc(record.get("completed_at"))
        if record.get("note", {}).get("path") != str(root / (name + ".md")) or not current_receipt(record["note"]):
            raise StateError("Saved retrospective note {} changed or is missing; restore its immutable bytes before using its coverage.".format(name), {})
    for row in result["transitions"]:
        if (not isinstance(row, dict) or set(row) != {"schema_version", "id", "at", "agent", "descriptor", "incoming"}
                or row["schema_version"] != SCHEMA_VERSION):
            _malformed("transition schema")
        utc(row["at"])
        validate_coverage([row["descriptor"]])
        validate_observation(row["incoming"])
        descriptor = row["descriptor"]
        if row["agent"] != descriptor["agent"] or row["id"] != digest({key: value for key, value in row.items() if key not in {"at", "id"}}):
            _malformed("transition identity")
        if not descriptor["first_start"] and not any(descriptor in record["coverage"] for record in result["records"]):
            _malformed("transition coverage provenance")
    return result


def cadence(index, at, *, existing_work=False):
    now = timestamp(utc(at), "Retrospective checkpoint")
    dates = [timestamp(row["completed_at"], "Completed retrospective") for row in index["records"]]
    if any(value > now for value in dates):
        raise UsageError("Retrospective checkpoint precedes a saved completion; use the current UTC checkpoint without rewriting history.", {})
    last = max(dates) if dates else None
    baseline = timestamp(index["baseline_at"], "Retrospective baseline") if index.get("baseline_at") else None
    if baseline is not None and baseline > now:
        raise UsageError("Retrospective checkpoint precedes the saved baseline; use a current UTC checkpoint.", {})
    reference = last or baseline
    due = (now >= reference + INTERVAL) if reference else existing_work
    return {"due": due, "last_completed_at": last.isoformat() if last else None,
            "next_due_at": (reference + INTERVAL).isoformat() if reference else None,
            "reason": "24_hours" if due and reference else "existing_work_without_history" if due else "not_due"}


def establish_baseline(path, index, at):
    if index["baseline_at"] is None and not index["records"]:
        index["baseline_at"] = utc(at)
        save_state(directory(path) / "index.json", index)


def _install_note(path, data):
    if path.exists():
        if file_bytes(path) != data:
            raise StateError("Retrospective note id already has different bytes; preserve it and choose a new id.", {})
        return
    fd, temporary = tempfile.mkstemp(prefix="note-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if file_bytes(path) != data:
                raise StateError("Retrospective note id was claimed with different bytes; preserve it and use a new id.", {}) from None
    except OSError as exc:
        raise StateError("Cannot install retrospective note {}: {}. Restore directory access and retry the same record.".format(path, exc), {}) from None
    finally:
        Path(temporary).unlink()


def record(path, data, coverage, at):
    """Commit a validated live coverage snapshot and lead-authored Markdown."""
    required = {"id", "note", "period_start", "period_end", "triggers", "tasks", "participants", "unavailable", "sources", "completed", "check"}
    if not isinstance(data, dict) or set(data) != required or data.get("completed") is not True:
        raise UsageError("Retrospective record requires id, note, period_start/end, triggers, tasks, participants, unavailable, sources, completed: true, and check.", {})
    name = identifier(data["id"])
    at = utc(at)
    start, end = utc(data["period_start"]), utc(data["period_end"])
    if not timestamp(start, "Period start") <= timestamp(end, "Period end") <= timestamp(at, "Completion"):
        raise UsageError("Retrospective period must end no later than completion and begin no later than its end.", {})
    for key in ("tasks", "participants"):
        if not isinstance(data[key], list) or any(not isinstance(item, str) or not item.strip() for item in data[key]) or len(set(data[key])) != len(data[key]):
            raise UsageError("Retrospective {} must be a list of distinct non-empty names.".format(key), {})
    if (not isinstance(data["triggers"], list) or not data["triggers"]
            or any(not isinstance(item, str) for item in data["triggers"])
            or not set(data["triggers"]) <= TRIGGERS):
        raise UsageError("Retrospective triggers must name daily and/or transition.", {})
    if not isinstance(data["unavailable"], dict) or any(not isinstance(key, str) or not isinstance(value, str) or not value.strip() for key, value in data["unavailable"].items()):
        raise UsageError("Retrospective unavailable must map worker names to explicit reasons.", {})
    if set(data["participants"]) & set(data["unavailable"]):
        raise UsageError("A retrospective worker cannot both participate and be unavailable; record what actually happened.", {})
    if not isinstance(data["sources"], list):
        raise UsageError("Retrospective sources must list absolute evidence-file paths.", {})
    receipt(data["note"])
    note_data = file_bytes(data["note"])
    try:
        note_text = note_data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError("Retrospective note must be UTF-8 Markdown; save the lead's substantive synthesis and retry.", {}) from None
    for section in SECTIONS:
        match = re.search(r"^#{1,6} " + re.escape(section) + r"[ \t]*$\n(.*?)(?=^#{1,6} |\Z)", note_text, re.M | re.S)
        body = re.sub(r"<!--.*?-->", "", match[1], flags=re.S).strip() if match else ""
        if not body or body.lower() in {"todo", "tbd", "n/a", "..."}:
            raise UsageError("Retrospective note needs a nonempty {} section with the lead's synthesis; headings or template placeholders do not complete it.".format(section), {})
    validate_coverage(coverage)
    sources = [receipt(source) for source in data["sources"]]
    if not sources and not coverage:
        raise UsageError("A quiet retrospective needs an actual ledger or prior note source; provide evidence for the interval.", {})
    metadata = {"schema_version": SCHEMA_VERSION, "id": name, "period_start": start, "period_end": end,
                "triggers": data["triggers"], "tasks": data["tasks"], "participants": data["participants"],
                "unavailable": data["unavailable"], "sources": sources, "coverage": coverage}
    note_data = ("---\n" + json.dumps(metadata, indent=2, sort_keys=True) + "\n---\n\n" + note_text).encode("utf-8")
    root = directory(path)
    note = {"path": str(root / (name + ".md")), "sha256": hashlib.sha256(note_data).hexdigest(), "size": len(note_data)}
    item = {"schema_version": SCHEMA_VERSION, "id": name, "completed_at": at,
            "period_start": start, "period_end": end, "triggers": data["triggers"], "tasks": data["tasks"],
            "participants": data["participants"], "unavailable": data["unavailable"], "sources": sources,
            "note": note, "coverage": coverage, "input_digest": digest(data)}
    index = load(path, allow_pending=True)
    pending_path = root / "pending.json"
    if pending_path.exists():
        pending = _json(pending_path)
        if not isinstance(pending, dict) or set(pending) != {"schema_version", "previous_index", "record"} or pending["schema_version"] != SCHEMA_VERSION:
            _malformed("pending transaction")
        validate_record(pending["record"])
        committed = next((row for row in index["records"] if row["id"] == pending["record"]["id"]), None)
        if committed:
            if committed != pending["record"]:
                _malformed("conflicting committed transaction")
            pending_path.unlink()
    prior = next((row for row in index["records"] if row["id"] == name), None)
    if prior:
        if (prior["input_digest"] != item["input_digest"] or prior["note"] != note
                or prior["sources"] != sources or prior["coverage"] != coverage):
            raise UsageError("Retrospective id already records different metadata; preserve it and use a new id.", {})
        return {**prior, "replayed": True}
    pending_path = root / "pending.json"
    transaction = {"schema_version": SCHEMA_VERSION, "previous_index": digest(index), "record": item}
    if pending_path.exists():
        pending = _json(pending_path)
        if (pending.get("schema_version") != SCHEMA_VERSION or pending.get("previous_index") != digest(index)
                or pending.get("record", {}).get("input_digest") != item["input_digest"]
                or pending.get("record", {}).get("note") != note):
            raise StateError("A different retrospective transaction is pending; resume its original record before starting another.", {})
        item = pending["record"]
    else:
        save_state(pending_path, transaction)
    _install_note(root / (name + ".md"), note_data)
    index["records"].append(item)
    save_state(root / "index.json", index)
    pending_path.unlink()
    return {**item, "replayed": False}


def list_notes(path, *, task=None, since=None):
    index = load(path)
    cutoff = timestamp(utc(since), "Retrospective since") if since else None
    rows = [row for row in index["records"] if (task is None or task in row["tasks"])
            and (cutoff is None or timestamp(row["completed_at"], "Retrospective completion") >= cutoff)]
    return {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)), "records": rows}


def show(path, name="latest", *, task=None):
    records = list_notes(path, task=task)["records"]
    record = (max(records, key=lambda row: timestamp(row["completed_at"], "Retrospective completion")) if records else None) if name == "latest" else next((row for row in records if row["id"] == name), None)
    if record is None:
        raise UsageError("No saved retrospective matches; use retro-list with the same --state path to inspect available notes.", {})
    return {"schema_version": SCHEMA_VERSION, "record": record, "markdown": file_bytes(record["note"]["path"]).decode("utf-8")}
