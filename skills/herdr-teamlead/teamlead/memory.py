"""Lead-owned lessons and handoff captures; never task acceptance authority.

Each write atomically appends to one document under its existing OS state lock.
Readers are offline, never lock or write, and retain old revisions for audit.
The CLI owns the clock; timestamps are explicit throughout this module.
"""

import hashlib
import json
import re
from datetime import timezone
from pathlib import Path
from urllib.parse import urlsplit

from .chronology import timestamp
from .errors import StateError, UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1
#: Stow record version. 1 held free-text gaps. 2 makes each gap structured:
#: what is missing, which task it affects, and exactly one recovery -- a file
#: to re-read, a question for the operator, or an accepted loss with its
#: reason (#483). A version-1 stow upgrades on read (see `_migrate_stow`)
#: and the owner rewrites it on its next write.
STOW_VERSION = 2
STOW_VERSIONS = frozenset({SCHEMA_VERSION, STOW_VERSION})
GAP_RECOVERIES = ("reread", "ask", "accept")
COMMANDS = frozenset({"memory-record", "memory-list", "memory-show", "memory-stow"})
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


def location(path):
    return Path(str(Path(path).expanduser().resolve()) + ".memory") / "index.json"


def _require(condition, message):
    if not condition:
        raise UsageError(message, {})


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _names(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(_text(item) for item in value) and len(set(value)) == len(value))


def _id(value):
    _require(isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None,
             "Memory ids require 1-64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit.")
    return value


def _utc(value):
    return timestamp(value, "Memory time").astimezone(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError("Cannot read memory JSON {}: {}. Preserve its bytes and restore a valid file before retrying.".format(path, exc), {}) from None


def _receipt(path):
    _require(isinstance(path, str) and Path(path).is_absolute(),
             "Memory evidence and required reads must use absolute file paths; supply the durable source location.")
    try:
        resolved = Path(path).resolve()
        data = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise UsageError("Cannot read memory source {}: {}. Restore it or capture the missing evidence as an explicit stow gap.".format(path, exc), {}) from None
    return {"schema_version": SCHEMA_VERSION, "kind": "file", "path": str(resolved),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _source(value):
    _require(_text(value), "Memory sources must be absolute paths or HTTPS evidence links; supply at least one concrete source.")
    if value.startswith("https://"):
        _url(value)
        return {"schema_version": SCHEMA_VERSION, "kind": "url", "url": value}
    return _receipt(value)


def _url(value):
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        valid = (parts is not None and parts.scheme == "https" and bool(parts.hostname)
                 and parts.username is None and parts.password is None and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    _require(valid, "Memory evidence links must be HTTPS URLs without credentials or whitespace; supply a shareable source link.")


def _validate_source(row):
    _require(isinstance(row, dict) and type(row.get("schema_version")) is int and row["schema_version"] == SCHEMA_VERSION,
             "Memory source schema is unsupported; preserve the artifact and update the owner skill.")
    if row.get("kind") == "url":
        _require(set(row) == {"schema_version", "kind", "url"},
                 "Memory source URL is malformed; restore the original record.")
        _url(row["url"])
        return
    _require(set(row) == {"schema_version", "kind", "path", "sha256", "size"}
             and row.get("kind") == "file" and isinstance(row.get("path"), str) and Path(row["path"]).is_absolute()
             and isinstance(row.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None
             and type(row.get("size")) is int and row["size"] >= 0,
             "Memory file receipt is malformed; restore the original record.")


def _valid_gap(gap):
    """A version-2 gap: what is missing, the task it affects, and one recovery."""
    if not (isinstance(gap, dict) and set(gap) == {"missing", "task", "recovery"}
            and _text(gap["missing"]) and _text(gap["task"])
            and isinstance(gap["recovery"], dict) and len(gap["recovery"]) == 1):
        return False
    (kind, value), = gap["recovery"].items()
    if kind == "reread":
        return isinstance(value, str) and Path(value).is_absolute()
    return kind in GAP_RECOVERIES and _text(value)


#: The task a migrated version-1 gap names: its free text never recorded one.
UNRECORDED_TASK = "unrecorded"


def _migrate_stow(row):
    """Upgrade a version-1 stow to version 2 without inventing what it never held.

    A free-text gap recorded no task and no recovery, so the only honest
    recovery is a question to the operator quoting the original text. The
    `input_digest` keeps binding the original input, so an exact retry of it
    still replays this record.
    """
    if row.get("kind") != "stow" or row.get("schema_version") != SCHEMA_VERSION:
        return row
    gaps = [{"missing": text, "task": UNRECORDED_TASK,
             "recovery": {"ask": "A handoff recorded this gap without a task or recovery: {} How should it be recovered?".format(text)}}
            for text in row["gaps"]]
    return {**row, "schema_version": STOW_VERSION, "gaps": gaps}


def _validate_record(row):
    versions = STOW_VERSIONS if isinstance(row, dict) and row.get("kind") == "stow" else {SCHEMA_VERSION}
    _require(isinstance(row, dict) and type(row.get("schema_version")) is int and row["schema_version"] in versions,
             "Memory record schema is unsupported; preserve the artifact and update the owner skill.")
    common = {"schema_version", "id", "kind", "recorded_at", "input_digest"}
    _id(row.get("id"))
    _utc(row.get("recorded_at"))
    _require(isinstance(row.get("input_digest"), str) and re.fullmatch(r"[0-9a-f]{64}", row["input_digest"]) is not None,
             "Memory input identity is malformed; restore the original record.")
    if row.get("kind") == "lesson":
        _require(set(row) == common | {"lesson_id", "supersedes", "status", "lesson", "scopes", "sources", "last_verified_at", "expires_at", "revalidate_when", "reason"},
                 "Memory lesson fields are unsupported; preserve the record and update the owner skill.")
        _id(row["lesson_id"])
        if row["supersedes"] is not None:
            _id(row["supersedes"])
        _require(isinstance(row["status"], str) and row["status"] in {"active", "archived"} and _text(row["lesson"]) and _text(row["reason"])
                 and _names(row["scopes"], nonempty=True) and _text(row["revalidate_when"]),
                 "Memory lessons need active/archived status, lesson, reason, scopes and a concrete revalidation condition.")
        _require(timestamp(_utc(row["last_verified_at"]), "Verification") <= timestamp(row["recorded_at"], "Recording"),
                 "Memory verification cannot be in the future; supply the actual evidence review time.")
        if row["expires_at"] is not None:
            _require(timestamp(_utc(row["expires_at"]), "Expiry") > timestamp(row["last_verified_at"], "Verification"),
                     "Memory expiry must follow verification; correct the dates or use a revalidation condition without expiry.")
        _require(isinstance(row["sources"], list) and bool(row["sources"]),
                 "Memory lessons need evidence sources; capture unevidenced observations as stow gaps instead.")
        for source in row["sources"]:
            _validate_source(source)
    else:
        _require(row.get("kind") == "stow" and set(row) == common | {"capture", "unresolved_work", "gaps", "required_reads"},
                 "Memory stow fields are unsupported; preserve the record and update the owner skill.")
        gaps_valid = (_names(row["gaps"]) if row["schema_version"] == SCHEMA_VERSION
                      else isinstance(row["gaps"], list) and all(_valid_gap(gap) for gap in row["gaps"]))
        _require(_text(row["capture"]) and _names(row["unresolved_work"]) and isinstance(row["required_reads"], list)
                 and bool(row["required_reads"]),
                 "Memory stow needs a substantive capture, an explicit unresolved_work list, and at least one required read.")
        _require(gaps_valid,
                 "Each stow gap needs missing, task and one recovery: {\"reread\": \"/absolute/path\"}, {\"ask\": \"question\"} or {\"accept\": \"reason the loss is safe\"}.")
        for source in row["required_reads"]:
            _validate_source(source)
            _require(source["kind"] == "file", "Stow required reads must name durable local files; put remote references in their contents.")


def _validated(row):
    _validate_record(row)
    return row


def load(path):
    target = location(path)
    empty = {"schema_version": SCHEMA_VERSION, "state_path": str(Path(path).expanduser().resolve()), "records": []}
    if not target.exists():
        if target.is_symlink():
            raise StateError("Memory index {} is a dangling link; preserve the link and restore its saved target before reading or recording memory.".format(target), {})
        return empty
    document = _json(target)
    try:
        _require(isinstance(document, dict) and set(document) == set(empty)
                 and type(document.get("schema_version")) is int and document["schema_version"] == SCHEMA_VERSION
                 and document.get("state_path") == empty["state_path"] and isinstance(document.get("records"), list),
                 "Memory document has an unsupported schema or state identity; preserve it and update the owner skill or restore its backup.")
        ids, lessons = set(), {}
        previous_time = None
        # Validate each record at its saved version, then upgrade it; the next
        # owner write persists the upgrade (rules/stateful-artifacts.md).
        document["records"] = [_migrate_stow(_validated(row)) for row in document["records"]]
        for row in document["records"]:
            _require(row["id"] not in ids, "Memory record ids are duplicated; restore the original history.")
            ids.add(row["id"])
            recorded = timestamp(row["recorded_at"], "Memory recording")
            _require(previous_time is None or recorded >= previous_time, "Memory history is out of order; restore the original history.")
            previous_time = recorded
            if row["kind"] == "lesson":
                _require(row["supersedes"] == lessons.get(row["lesson_id"]), "Memory lesson revision chain is broken; restore the original history.")
                _require(row["lesson_id"] in lessons or row["status"] == "active", "Memory archive has no prior lesson; restore the original history.")
                lessons[row["lesson_id"]] = row["id"]
        return document
    except UsageError as exc:
        raise StateError(str(exc), {}) from None


def _append(path, data, kind, at):
    _require(isinstance(data, dict), "Memory input must be a JSON object; supply the documented record fields.")
    at = _utc(at)
    common = {"id"}
    fields = ({"lesson_id", "supersedes", "status", "lesson", "scopes", "sources", "last_verified_at", "expires_at", "revalidate_when", "reason"}
              if kind == "lesson" else {"capture", "unresolved_work", "gaps", "required_reads"})
    _require(set(data) == common | fields, "Memory {} input needs exactly: {}.".format(kind, ", ".join(sorted(common | fields))))
    _id(data["id"])
    with state_lock(location(path)):
        document = load(path)
        prior = next((row for row in document["records"] if row["id"] == data["id"]), None)
        if prior:
            _require(prior["kind"] == kind and prior["input_digest"] == _digest(data),
                     "Memory id already records different content; preserve it and use a new id.")
            _require(timestamp(at, "Retry") >= timestamp(prior["recorded_at"], "Original recording"),
                     "Memory retry precedes its original recording; supply the current UTC checkpoint.")
            return _write_result(path, prior, at, replayed=True)
        _require(not document["records"] or timestamp(at, "Recording") >= timestamp(document["records"][-1]["recorded_at"], "Previous recording"),
                 "Memory recording precedes saved history; use the current UTC checkpoint without rewriting old records.")
        row = {"schema_version": SCHEMA_VERSION if kind == "lesson" else STOW_VERSION,
               "kind": kind, "recorded_at": at, "input_digest": _digest(data), **data}
        source_key = "sources" if kind == "lesson" else "required_reads"
        _require(_names(data[source_key], nonempty=True), "Memory {} must list distinct evidence locations.".format(source_key))
        row[source_key] = [_source(value) if kind == "lesson" else _receipt(value) for value in data[source_key]]
        _require(all(source.get("path") != str(location(path)) for source in row[source_key]),
                 "Do not receipt memory's own changing index; replacement leads read memory_path automatically, then required_reads.")
        _validate_record(row)
        if kind == "lesson":
            prior_lesson = next((old for old in reversed(document["records"]) if old["kind"] == "lesson" and old["lesson_id"] == row["lesson_id"]), None)
            _require(row["supersedes"] == (prior_lesson["id"] if prior_lesson else None),
                     "Memory revision must supersede the current lesson record id; reload memory-list and retain its history.")
            _require(prior_lesson is not None or row["status"] == "active", "Record an active lesson before archiving it; preserve existing revision history.")
        document["records"].append(row)
        save_state(location(path), document)
    return _write_result(path, row, at, replayed=False)


def record(path, data, at):
    """Append a curated lesson revision, retaining all superseded/archive records."""
    return _append(path, data, "lesson", at)


def stow(path, data, at):
    """Capture local handoff knowledge without claiming task or fleet reconciliation."""
    return _append(path, data, "stow", at)


def _source_status(source):
    if source["kind"] == "url":
        return {**source, "observation": "not_checked_offline"}
    try:
        current = _receipt(source["path"])
    except UsageError:
        return {**source, "observation": "unavailable"}
    return {**source, "observation": "same_bytes" if current == source else "changed"}


def _view(row, at):
    result = dict(row)
    source_key = "sources" if row["kind"] == "lesson" else "required_reads"
    result[source_key] = [_source_status(source) for source in row[source_key]]
    if row["kind"] == "lesson":
        result["expired"] = row["expires_at"] is not None and timestamp(at, "Checkpoint") >= timestamp(row["expires_at"], "Expiry")
        result["use_requires_live_verification"] = True
    else:
        # Every stow is version 2 once loaded, and a structured gap names its
        # own recovery, so gaps no longer block a reset (#483).
        result["reset_ready"] = all(source["observation"] == "same_bytes" for source in result[source_key])
        result["readiness_scope"] = "local_capture_only"
    return result


def _write_result(path, row, at, *, replayed):
    return {"schema_version": SCHEMA_VERSION, "memory_path": str(location(path)), "record": _view(row, at), "replayed": replayed}


def _checkpoint(document, at):
    _require(not document["records"] or timestamp(at, "Checkpoint") >= timestamp(document["records"][-1]["recorded_at"], "Latest recording"),
             "Memory checkpoint precedes saved history; use the current UTC checkpoint.")


def list_lessons(path, at, *, scopes=None, include_archived=False):
    at = _utc(at)
    _require(scopes is None or _names(scopes), "Memory scopes must be distinct non-empty strings; use project, task or role identifiers.")
    document = load(path)
    _checkpoint(document, at)
    latest = {row["lesson_id"]: row for row in document["records"] if row["kind"] == "lesson"}
    rows = [_view(row, at) for row in latest.values()
            if (include_archived or row["status"] == "active")
            and (not scopes or "*" in row["scopes"] or set(scopes) & set(row["scopes"]))]
    return {"schema_version": SCHEMA_VERSION, "memory_path": str(location(path)), "checked_at": at, "lessons": rows}


def show(path, at, name="latest"):
    at = _utc(at)
    document = load(path)
    _checkpoint(document, at)
    rows = document["records"]
    selected = ([row for row in rows if row["kind"] == "stow"] if name == "latest"
                else [row for row in rows if row["id"] == name])
    _require(bool(selected), "No saved memory matches; use memory-list or memory-show --id latest with the same --state path.")
    return {"schema_version": SCHEMA_VERSION, "memory_path": str(location(path)), "checked_at": at,
            "record": _view(selected[-1], at), "history": [row for row in rows if selected[-1]["kind"] == "lesson"
                                                          and row["kind"] == "lesson" and row["lesson_id"] == selected[-1]["lesson_id"]]}


def register_commands(sub, common):
    for command in sorted(COMMANDS):
        parser = sub.add_parser(command, parents=[common], help="Read or curate persistent lead working memory offline.")
        parser.add_argument("--now", metavar="ISO", help="Injected observation or recording time (default: current UTC).")
        if command in {"memory-record", "memory-stow"}:
            parser.add_argument("--record", required=True, metavar="FILE", help="Lead-authored JSON record; see references/working-memory.md.")
        elif command == "memory-list":
            parser.add_argument("--scope", action="append", dest="scopes")
            parser.add_argument("--include-archived", action="store_true")
        else:
            parser.add_argument("--id", default="latest", help="Immutable record id, or latest for the latest lead stow.")


def run_command(args, state_path, now):
    """Return a JSON payload; call before config or Herdr setup, with no outer lock."""
    if args.command == "memory-record":
        return record(state_path, _json(args.record), now)
    if args.command == "memory-stow":
        return stow(state_path, _json(args.record), now)
    if args.command == "memory-list":
        return list_lessons(state_path, now, scopes=args.scopes, include_archived=args.include_archived)
    if args.command == "memory-show":
        return show(state_path, now, args.id)
    raise UsageError("Unknown memory command; use memory-record, memory-stow, memory-list or memory-show.", {})
