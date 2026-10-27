"""Central-owned durable fleet evidence, schema1; no effects at import.

Only the fixed central writer creates refs/tags/fleet-publish-v1 records. A
SOURCE ref names the original intent forever; recovery writes only its own run
refs. The intent is durable after consumer CAS and BEFORE any external publish.
A separate trusted finalizer archives native completed run/attempt/jobs and the
exact records. A publisher cannot attest its own still-running attempt.

Canonical Git records are authoritative CENTRAL state, never consumer claims.
No overwrite, removal, reset, migration, inferred success or republish exists.
Readers reject unknown versions/conflicts. A terminal failure remains failure;
only validated native success supports confirmed receipts after Actions expiry.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from fleet_publish_github import FleetPublishLookupError
from fleet_publish_record import (
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    FleetPublishRecord,
    FleetRecordError,
    parse_record,
    valid_repository,
    valid_run_id,
    valid_sha,
)

PREFIX = "refs/tags/fleet-publish-v1/"
FINALIZER_WORKFLOW = ".github/workflows/fleet-publish-finalize.yml"
MAX_ARCHIVE_BYTES = 256 * 1024
MAX_JOBS = 16
MAX_STEPS = 100
CLAIM_STEP = "Claim source atomically before publication"
EFFECT_STEP = "Publish once or perform moderation readback only"
CONCLUSIONS = {
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
}
MUTABLE = {
    "state",
    "publish_outcome",
    "publish_exit_code",
    "landed_after_error",
    "moderation",
    "bookkeeping_sha",
}


class ArchiveError(FleetPublishLookupError):
    def __init__(self, kind: str = "archive_binding") -> None:
        super().__init__(kind)


def need(value: object) -> None:
    if not value:
        raise ArchiveError() from None


def obj(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArchiveError() from None
    return value


def identifier(value: object) -> str:
    if type(value) is int:
        value = str(value)
    need(valid_run_id(value))
    return str(value)


def utc(value: object) -> str:
    need(
        isinstance(value, str)
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
        )
    )
    try:
        datetime.fromisoformat(str(value))
    except ValueError:
        raise ArchiveError() from None
    return str(value)


def ref_name(
    kind: Literal["source", "intent", "terminal", "hold"],
    *,
    repository: str | None = None,
    source_sha: str | None = None,
    run_id: str | None = None,
    attempt: int = 1,
) -> str:
    need(type(attempt) is int and attempt == 1)
    if kind == "source":
        need(
            valid_repository(repository)
            and repository != CENTRAL_REPOSITORY
            and valid_sha(source_sha)
            and run_id is None
        )
        return (
            PREFIX
            + "sources/"
            + hashlib.sha256(str(repository).encode()).hexdigest()
            + "/"
            + str(source_sha)
        )
    if kind == "hold":
        need(valid_run_id(run_id) and repository is None and source_sha is None)
        return PREFIX + f"runs/{run_id}/hold"
    need(
        kind in ("intent", "terminal")
        and valid_run_id(run_id)
        and repository is None
        and source_sha is None
    )
    return PREFIX + f"runs/{run_id}/1/{kind}"


def record(value: object) -> FleetPublishRecord:
    try:
        return parse_record(value)
    except FleetRecordError:
        raise ArchiveError() from None


def make_intent(intent: object, bookkeeping_sha: str) -> dict[str, Any]:
    value = record(intent)
    need(
        value["state"] == "claimed"
        and value["publish_outcome"] == "not_started"
        and value["publish_exit_code"] is None
        and value["landed_after_error"] is False
        and value["moderation"] == "not_checked"
        and value["bookkeeping_sha"] is None
        and value["version"] is not None
        and value["run_attempt"] == value["origin_run_attempt"] == 1
        and valid_sha(bookkeeping_sha)
    )
    return {
        "schema_version": 1,
        "kind": "fleet-publish-intent",
        "intent": value,
        "bookkeeping_sha": bookkeeping_sha,
    }


def parse_intent(value: object) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw) == {"schema_version", "kind", "intent", "bookkeeping_sha"}
        and type(raw.get("schema_version")) is int
        and raw["schema_version"] == 1
        and raw["kind"] == "fleet-publish-intent"
    )
    return make_intent(raw["intent"], raw["bookkeeping_sha"])


def native_run(
    value: object, *, projected: bool = False, terminal: bool = True
) -> dict[str, Any]:
    raw = obj(value)
    names = {
        "id",
        "run_attempt",
        "workflow_id",
        "path",
        "event",
        "head_branch",
        "head_sha",
        "repository",
        "head_repository",
        "display_title",
        "created_at",
        "status",
        "conclusion",
    }
    if projected:
        need(set(raw) == names)
    repo = (
        raw.get("repository")
        if projected
        else obj(raw.get("repository")).get("full_name")
    )
    head_repo = (
        raw.get("head_repository")
        if projected
        else obj(raw.get("head_repository")).get("full_name")
    )
    need(
        repo == head_repo == CENTRAL_REPOSITORY
        and type(raw.get("run_attempt")) is int
        and raw["run_attempt"] == 1
        and type(raw.get("workflow_id")) is int
        and raw["workflow_id"] > 0
        and raw.get("path") == CENTRAL_WORKFLOW
        and raw.get("event") == "workflow_dispatch"
        and raw.get("head_branch") == "main"
        and valid_sha(raw.get("head_sha"))
        and (
            raw.get("status") == "completed"
            if terminal
            else raw.get("status")
            in ("queued", "in_progress", "waiting", "requested", "pending", "completed")
        )
        and (
            raw.get("conclusion") in CONCLUSIONS
            if raw.get("status") == "completed"
            else raw.get("conclusion") is None
        )
        and isinstance(raw.get("display_title"), str)
    )
    return {
        **{
            name: raw[name]
            for name in names - {"id", "repository", "head_repository", "created_at"}
        },
        "id": identifier(raw.get("id")),
        "repository": repo,
        "head_repository": head_repo,
        "created_at": utc(raw.get("created_at")),
    }


def native_jobs(
    values: object, run: dict[str, Any], *, projected: bool = False
) -> list[dict[str, Any]]:
    need(isinstance(values, list) and 1 <= len(values) <= MAX_JOBS)
    if not isinstance(values, list):
        raise ArchiveError()
    jobs = []
    for value in values:
        raw = obj(value)
        if projected:
            need(
                set(raw)
                == {
                    "id",
                    "name",
                    "run_id",
                    "run_attempt",
                    "head_sha",
                    "status",
                    "conclusion",
                    "steps",
                }
            )
        need(
            raw.get("name") in ("poll", "publish")
            and identifier(raw.get("run_id")) == run["id"]
            and type(raw.get("run_attempt")) is int
            and raw["run_attempt"] == 1
            and raw.get("head_sha") == run["head_sha"]
            and raw.get("status") == "completed"
            and raw.get("conclusion") in CONCLUSIONS
        )
        steps = raw.get("steps")
        need(isinstance(steps, list) and len(steps) <= MAX_STEPS)
        if not isinstance(steps, list):
            raise ArchiveError()
        clean = []
        for step in steps:
            row = obj(step)
            if projected:
                need(set(row) == {"number", "name", "status", "conclusion"})
            name = row.get("name")
            need(
                type(row.get("number")) is int
                and row["number"] > 0
                and isinstance(name, str)
                and 0 < len(name) <= 256
                and not any(ord(c) < 32 or ord(c) == 127 for c in str(name))
                and row.get("status") == "completed"
                and row.get("conclusion") in CONCLUSIONS
            )
            clean.append(
                {key: row[key] for key in ("number", "name", "status", "conclusion")}
            )
        need(len({row["number"] for row in clean}) == len(clean))
        jobs.append(
            {
                "id": identifier(raw.get("id")),
                "name": raw["name"],
                "run_id": run["id"],
                "run_attempt": 1,
                "head_sha": run["head_sha"],
                "status": "completed",
                "conclusion": raw["conclusion"],
                "steps": clean,
            }
        )
    need(
        len({row["id"] for row in jobs}) == len(jobs)
        and len([row for row in jobs if row["name"] == "publish"]) == 1
    )
    return jobs


def finalizer_identity(value: object) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw) == {"workflow_path", "workflow_sha", "run_id", "run_attempt"}
        and raw.get("workflow_path") == FINALIZER_WORKFLOW
        and valid_sha(raw.get("workflow_sha"))
        and valid_run_id(raw.get("run_id"))
        and type(raw.get("run_attempt")) is int
        and raw["run_attempt"] == 1
    )
    return dict(raw)


def positive_native(native: dict[str, Any]) -> bool:
    if any(native[key]["conclusion"] != "success" for key in ("run", "attempt")):
        return False
    publish = [job for job in native["jobs"] if job["name"] == "publish"]
    if len(publish) != 1 or publish[0]["conclusion"] != "success":
        return False
    for name in (CLAIM_STEP, EFFECT_STEP):
        matches = [step for step in publish[0]["steps"] if step["name"] == name]
        if len(matches) != 1 or matches[0]["conclusion"] != "success":
            return False
    return True


def make_terminal(
    intent_envelope: object,
    receipt: object | None,
    run: object,
    attempt: object,
    jobs: object,
    *,
    finalizer: object,
) -> dict[str, Any]:
    intent = parse_intent(intent_envelope)
    native: dict[str, Any] = {"run": native_run(run), "attempt": native_run(attempt)}
    need(native["run"] == native["attempt"])
    original = intent["intent"]
    need(
        native["run"]["id"] == original["run_id"]
        and native["run"]["head_sha"] == original["workflow_sha"]
        and native["run"]["display_title"]
        == f"fleet-publish-v1:{original['consumer_repository']}:{original['source_sha']}"
    )
    native["jobs"] = native_jobs(jobs, native["run"])
    result = None if receipt is None else record(receipt)
    if result is not None:
        need(
            all(
                result[key] == original[key]
                for key in original
                if key not in MUTABLE | {"version"}
            )
            and result["bookkeeping_sha"] == intent["bookkeeping_sha"]
            and result["state"] in ("published", "confirmed", "uncertain", "blocked")
        )
        if result["state"] in ("published", "confirmed"):
            need(
                result["version"] == original["version"]
                and result["publish_outcome"] == "success"
                and result["publish_exit_code"] == 0
                and result["landed_after_error"] is False
                and result["version"] is not None
            )
    return {
        "schema_version": 1,
        "kind": "fleet-publish-terminal",
        "intent": intent,
        "receipt": result,
        "native": native,
        "finalizer": finalizer_identity(finalizer),
    }


def parse_terminal(value: object) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw)
        == {"schema_version", "kind", "intent", "receipt", "native", "finalizer"}
        and type(raw.get("schema_version")) is int
        and raw["schema_version"] == 1
        and raw["kind"] == "fleet-publish-terminal"
    )
    native = obj(raw["native"])
    need(set(native) == {"run", "attempt", "jobs"})
    run = native_run(native["run"], projected=True)
    attempt = native_run(native["attempt"], projected=True)
    jobs = native_jobs(native["jobs"], run, projected=True)
    # Reuse the same proof predicates, converting only the two repository fields
    # expected from actual native REST. No raw input is persisted or executed.
    actual = lambda row: {
        **row,
        "repository": {"full_name": row["repository"]},
        "head_repository": {"full_name": row["head_repository"]},
    }
    return make_terminal(
        raw["intent"],
        raw["receipt"],
        actual(run),
        actual(attempt),
        jobs,
        finalizer=raw["finalizer"],
    )


def make_hold(
    run_id: str, observed_attempt: int, workflow_sha: str, *, finalizer: object
) -> dict[str, Any]:
    need(
        valid_run_id(run_id)
        and type(observed_attempt) is int
        and 1 < observed_attempt < 2**31
        and valid_sha(workflow_sha)
    )
    return {
        "schema_version": 1,
        "kind": "fleet-publish-hold",
        "run_id": run_id,
        "observed_attempt": observed_attempt,
        "workflow_sha": workflow_sha,
        "reason": "unsupported_rerun",
        "finalizer": finalizer_identity(finalizer),
    }


def parse_hold(value: object) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw)
        == {
            "schema_version",
            "kind",
            "run_id",
            "observed_attempt",
            "workflow_sha",
            "reason",
            "finalizer",
        }
        and type(raw.get("schema_version")) is int
        and raw["schema_version"] == 1
        and raw["kind"] == "fleet-publish-hold"
        and raw["reason"] == "unsupported_rerun"
    )
    return make_hold(
        raw["run_id"],
        raw["observed_attempt"],
        raw["workflow_sha"],
        finalizer=raw["finalizer"],
    )


def encode_archive(value: object) -> bytes:
    parsed = parse_archive(value)
    raw = (
        json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )
    need(len(raw) <= MAX_ARCHIVE_BYTES)
    return raw


def parse_archive(value: object) -> dict[str, Any]:
    raw = obj(value)
    if raw.get("kind") == "fleet-publish-hold":
        return parse_hold(raw)
    if raw.get("kind") == "fleet-publish-intent":
        return parse_intent(raw)
    return parse_terminal(raw)


def decode_archive(raw: bytes) -> dict[str, Any]:
    need(isinstance(raw, bytes) and len(raw) <= MAX_ARCHIVE_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
        parsed = parse_archive(value)
        need(
            encode_archive(parsed) == raw
        )  # rejects duplicates, coercions and unknown fields
        return parsed
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise ArchiveError() from None
