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
        and 0 < raw["run_attempt"] < 2**31
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


class ArchiveReader:
    """Read-only central authority. Missing evidence never clears a source gate."""

    def __init__(self, client: Any):
        self.client = client
        self._protocols: set[str] = set()
        self._objects: dict[str, object] = {}
        self._loaded: dict[str, tuple[str, dict[str, Any]]] = {}
        self._central = False

    def _protocol(self, sha: str) -> None:
        if sha not in self._protocols:
            from fleet_publish_producer import verify_workflow_protocol

            verify_workflow_protocol(self.client, sha)
            self._protocols.add(sha)

    def _optional(self, route: str) -> object | None:
        try:
            return self.client.native_json(route)
        except FleetPublishLookupError as error:
            if error.kind not in (
                "native_missing",
                "native_expired",
            ) or error.http_status not in (404, 410):
                raise
            return None

    def _immutable(self, route: str) -> object:
        if route not in self._objects:
            self._objects[route] = self.client.native_json(route)
        return self._objects[route]

    def _load(self, ref: str) -> dict[str, Any] | None:
        import base64
        import binascii
        import copy

        if not self._central:
            central = obj(self.client.native_json(f"repos/{CENTRAL_REPOSITORY}"))
            need(
                central.get("full_name") == CENTRAL_REPOSITORY
                and central.get("default_branch") == "main"
                and central.get("archived") is False
                and central.get("disabled") is False
            )
            main = obj(
                self.client.native_json(
                    f"repos/{CENTRAL_REPOSITORY}/git/ref/heads/main"
                )
            )
            target = obj(main.get("object"))
            need(
                main.get("ref") == "refs/heads/main"
                and target.get("type") == "commit"
                and valid_sha(target.get("sha"))
            )
            self._protocol(target["sha"])
            self._central = True
        need(ref.startswith(PREFIX))
        route = f"repos/{CENTRAL_REPOSITORY}/git/ref/{ref.removeprefix('refs/')}"
        raw = self._optional(route)
        if raw is None:
            need(ref not in self._loaded)  # deletion is not fresh absence
            return None
        native = obj(raw)
        target = obj(native.get("object"))
        sha = target.get("sha")
        need(
            native.get("ref") == ref
            and target.get("type") == "commit"
            and valid_sha(sha)
        )
        if ref in self._loaded:
            old_sha, value = self._loaded[ref]
            need(sha == old_sha)
            return copy.deepcopy(value)
        commit = obj(self._immutable(f"repos/{CENTRAL_REPOSITORY}/git/commits/{sha}"))
        tree_sha = obj(commit.get("tree")).get("sha")
        need(
            commit.get("sha") == sha
            and commit.get("parents") == []
            and valid_sha(tree_sha)
        )
        tree = obj(self._immutable(f"repos/{CENTRAL_REPOSITORY}/git/trees/{tree_sha}"))
        entries = tree.get("tree")
        need(
            tree.get("sha") == tree_sha
            and tree.get("truncated") is False
            and isinstance(entries, list)
            and len(entries) == 1
        )
        if not isinstance(entries, list):
            raise ArchiveError()
        entry = obj(entries[0])
        blob_sha = entry.get("sha")
        need(
            entry.get("path") == "record.json"
            and entry.get("type") == "blob"
            and entry.get("mode") == "100644"
            and valid_sha(blob_sha)
        )
        blob = obj(self._immutable(f"repos/{CENTRAL_REPOSITORY}/git/blobs/{blob_sha}"))
        content = blob.get("content")
        size = blob.get("size")
        need(
            blob.get("sha") == blob_sha
            and blob.get("encoding") == "base64"
            and isinstance(content, str)
            and type(size) is int
            and 0 < size <= MAX_ARCHIVE_BYTES
        )
        try:
            data = base64.b64decode(str(content).replace("\n", ""), validate=True)
        except (ValueError, binascii.Error):
            raise ArchiveError() from None
        need(
            len(data) == size
            and hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            == blob_sha
        )
        value = decode_archive(data)
        again = obj(self.client.native_json(route))
        need(
            again.get("ref") == ref
            and obj(again.get("object")).get("sha") == sha
            and obj(again.get("object")).get("type") == "commit"
        )
        self._loaded[ref] = (str(sha), value)
        return copy.deepcopy(value)

    def source_claim(self, repository: str, source_sha: str) -> dict[str, Any] | None:
        value = self._load(
            ref_name("source", repository=repository, source_sha=source_sha)
        )
        if value is None:
            return None
        value = parse_intent(value)
        intent = value["intent"]
        need(
            intent["consumer_repository"] == repository
            and intent["source_sha"] == source_sha
            and intent["origin_run_id"] == intent["run_id"]
        )
        self._protocol(intent["workflow_sha"])
        return value

    def run_intent(self, run_id: str, attempt: int = 1) -> dict[str, Any] | None:
        value = self._load(ref_name("intent", run_id=run_id, attempt=attempt))
        if value is None:
            return None
        value = parse_intent(value)
        intent = value["intent"]
        need(intent["run_id"] == run_id and intent["run_attempt"] == attempt)
        self._protocol(intent["workflow_sha"])
        original = self.source_claim(
            intent["consumer_repository"], intent["source_sha"]
        )
        need(original is not None)
        if original is None:
            raise ArchiveError()
        if intent["origin_run_id"] == run_id:
            need(original == value)
        else:
            need(
                original["intent"]["run_id"] == intent["origin_run_id"]
                and all(
                    original["intent"][key] == intent[key]
                    for key in (
                        "consumer_repository",
                        "source_sha",
                        "source_ref",
                        "source_tree_sha",
                        "plugin_name",
                        "version",
                        "registry_baseline",
                        "origin_run_id",
                        "origin_run_attempt",
                    )
                )
            )
        return value

    def run_hold(self, run_id: str) -> dict[str, Any] | None:
        value = self._load(ref_name("hold", run_id=run_id))
        if value is None:
            return None
        value = parse_hold(value)
        need(value["run_id"] == run_id)
        self._protocol(value["workflow_sha"])
        self._protocol(value["finalizer"]["workflow_sha"])
        return value

    def terminal(self, run_id: str, attempt: int = 1) -> dict[str, Any] | None:
        value = self._load(ref_name("terminal", run_id=run_id, attempt=attempt))
        if value is None:
            return None
        value = parse_terminal(value)
        need(value["intent"] == self.run_intent(run_id, attempt))
        self._protocol(value["finalizer"]["workflow_sha"])
        return value

    def record(
        self, run_id: str, kind: Literal["intent", "receipt"]
    ) -> FleetPublishRecord | None:
        need(kind in ("intent", "receipt") and self.run_hold(run_id) is None)
        live: FleetPublishRecord | None
        try:
            live = self.client.artifact_evidence(run_id, kind)
        except FleetPublishLookupError as error:
            if error.kind != "expired_artifact" and not (
                error.kind in ("native_missing", "native_expired")
                and error.http_status in (404, 410)
            ):
                raise
            live = None
        if live is not None:
            live = record(live)
        intent = self.run_intent(run_id)
        terminal = self.terminal(run_id) if kind == "receipt" else None
        saved = (
            (intent["intent"] if intent is not None else None)
            if kind == "intent"
            else (terminal["receipt"] if terminal is not None else None)
        )
        if live is not None and saved is not None:
            need(live == saved)
        if live is not None and kind == "intent":
            need(intent is not None)
        if live is not None and terminal is not None:
            need(terminal["receipt"] == live)
        if live is not None:
            need(intent is not None)
            return live
        return saved

    def _jobs(self, run_id: str) -> list[dict[str, Any]] | None:
        response = self._optional(
            f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}/attempts/1/jobs?per_page=100&page=1"
        )
        if response is None:
            return None
        data = obj(response)
        total, rows = data.get("total_count"), data.get("jobs")
        need(
            type(total) is int
            and 0 < total <= MAX_JOBS
            and isinstance(rows, list)
            and len(rows) == total
        )
        return rows

    def verify_record_attempt(
        self, expected: object, *, successful: bool
    ) -> dict[str, Any]:
        value = record(expected)
        need(value["run_attempt"] == 1 and self.run_hold(value["run_id"]) is None)
        if value["state"] != "claimed":
            need(self.record(value["run_id"], "receipt") == value)
        intent = self.run_intent(value["run_id"])
        archived = self.terminal(value["run_id"])
        if intent is not None:
            need(
                all(
                    value[key] == intent["intent"][key]
                    for key in value
                    if key not in MUTABLE | {"version"}
                )
            )
            if value["state"] == "claimed":
                need(value == intent["intent"])
            if archived is not None and value["state"] != "claimed":
                need(value == archived["receipt"])
        else:
            need(archived is None)
        prefix = f"repos/{CENTRAL_REPOSITORY}/actions/runs/{value['run_id']}"
        run = self._optional(prefix)
        attempt = self._optional(prefix + "/attempts/1")
        native: dict[str, Any] = {}
        for name, raw in (("run", run), ("attempt", attempt)):
            if raw is None:
                need(archived is not None)
                native[name] = archived["native"][name] if archived else None
            else:
                native[name] = native_run(raw, terminal=False)
                if archived is not None:
                    need(native[name] == archived["native"][name])
        need(
            native["run"] == native["attempt"]
            and native["run"]["id"] == value["run_id"]
            and native["run"]["head_sha"] == value["workflow_sha"]
            and native["run"]["display_title"]
            == f"fleet-publish-v1:{value['consumer_repository']}:{value['source_sha']}"
        )
        self._protocol(value["workflow_sha"])
        if archived is not None or successful:
            jobs = self._jobs(value["run_id"])
            if jobs is None:
                need(archived is not None)
                native["jobs"] = archived["native"]["jobs"] if archived else None
            else:
                native["jobs"] = native_jobs(jobs, native["run"])
                if archived is not None:
                    need(native["jobs"] == archived["native"]["jobs"])
        else:
            native["jobs"] = None
        if successful:
            need(value["state"] == "confirmed" and positive_native(native))
        return {**native, "archived": run is None or attempt is None}
