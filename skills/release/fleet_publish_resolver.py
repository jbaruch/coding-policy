#!/usr/bin/env python3
"""Positive central-run selection and strict native publication confirmation.

No poll, mutation, registry inference or retry permission. All namespace/source
and original-effect proofs precede selection. A selected pending intent remains
unconfirmed; resolve/confirm additionally require actual successful terminal
receipts and attempts, including the original publication for recovery runs.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from datetime import datetime
from typing import Any, NoReturn

from fleet_publish_github import FleetPublishLookupError, GitHubClient
from fleet_publish_producer import verify_workflow_protocol
from fleet_publish_record import (
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    MAX_RECORD_BYTES,
    FleetPublishRecord,
    FleetRecordError,
    decode_record,
    parse_record,
    valid_repository,
    valid_run_id,
    valid_sha,
)

STAMP = ".github/fleet-publish-source.json"
MARKER = ".github/fleet-publish-enabled"
MANIFEST = ".tessl-plugin/plugin.json"
MAX_HISTORY = 128
MAX_PAGES = 10
PAGE_SIZE = 100
MAX_TREE_ENTRIES = 4096
CLAIM_STEP = "Claim source atomically before publication"
EFFECT_STEP = "Publish once or perform moderation readback only"
MUTABLE_RECEIPT = {
    "state",
    "publish_outcome",
    "publish_exit_code",
    "landed_after_error",
    "moderation",
    "bookkeeping_sha",
}


class CentralPublishHold(RuntimeError):
    """Keep publication unconfirmed; kind never authorizes a publish retry."""

    def __init__(self, kind: str) -> None:
        super().__init__(
            "Verify complete native central publication evidence; preserve claims without replay."
        )
        self.kind = kind


def _hold(kind: str) -> NoReturn:
    raise CentralPublishHold(kind) from None


def _need(value: object, kind: str) -> None:
    if not value:
        _hold(kind)


def _object(value: object, kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _hold(kind)
    return value


def _list(value: object, kind: str) -> list[Any]:
    if not isinstance(value, list):
        _hold(kind)
    return value


def _text(value: object, kind: str) -> str:
    if not isinstance(value, str):
        _hold(kind)
    return value


def _positive(value: object) -> bool:
    return type(value) is int and valid_run_id(str(value))


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        _need(key not in value, "source_binding")
        value[key] = item
    return value


def _unsupported(_value: str) -> NoReturn:
    _hold("source_binding")


def _json_file(raw: bytes) -> dict[str, Any]:
    _need(len(raw) <= MAX_RECORD_BYTES, "source_binding")
    try:
        return _object(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_unsupported),
            "source_binding",
        )
    except (UnicodeError, ValueError, RecursionError):
        _hold("source_binding")


class _Evidence:
    def __init__(self, github: Any, repository: str, source_sha: str) -> None:
        _need(
            valid_repository(repository)
            and repository != CENTRAL_REPOSITORY
            and valid_sha(source_sha),
            "invalid_selection",
        )
        self.github = github
        self.repo = repository
        self.source = source_sha
        self.commits: dict[tuple[str, str], dict[str, Any]] = {}
        self.trees: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.artifacts: dict[tuple[str, str], FleetPublishRecord | None] = {}
        self.protocols: set[str] = set()
        self.attempts: dict[str, dict[str, Any]] = {}
        self.intents: dict[str, FleetPublishRecord] = {}
        self.ref, self.tip = self.default()
        self.history = []
        cursor = self.tip
        for _ in range(MAX_HISTORY):
            _need(cursor not in self.history, "source_binding")
            self.history.append(cursor)
            commit = self.commit(self.repo, cursor)
            if cursor == self.source:
                break
            _need(bool(commit["parents"]), "source_binding")
            cursor = commit["parents"][0]["sha"]
        else:
            _hold("source_binding")
        _need(self.history[-1] == self.source, "source_binding")
        self.source_tree = self.commit(self.repo, self.source)["tree"]["sha"]
        self.merged_pr()
        self.claims: list[tuple[FleetPublishRecord, str]] = []
        previous = None
        for sha in reversed(self.history):
            raw = self.file(self.repo, sha, STAMP)
            claim = None
            if raw is not None:
                try:
                    claim = decode_record(raw)
                except FleetRecordError:
                    _hold("claim_binding")
                _need(
                    claim["state"] == "claimed" and claim["consumer_repository"] == self.repo,
                    "claim_binding",
                )
            if claim != previous and claim is not None and claim["source_sha"] == self.source:
                _need(
                    sha != self.source and len(self.commit(self.repo, sha)["parents"]) == 1,
                    "claim_binding",
                )
                self.claims.append((claim, sha))
            previous = claim
        workflow = self.obj(
            f"repos/{CENTRAL_REPOSITORY}/actions/workflows/fleet-publish.yml", "workflow_binding"
        )
        _need(
            _positive(workflow.get("id"))
            and workflow.get("path") == CENTRAL_WORKFLOW
            and workflow.get("state") == "active",
            "workflow_binding",
        )
        self.workflow_id = workflow["id"]
        self.run_snapshot: list[tuple[Any, ...]] | None = None

    def obj(self, route: str, kind: str) -> dict[str, Any]:
        return _object(self.github.json(route), kind)

    def default(self) -> tuple[str, str]:
        meta = self.obj(f"repos/{self.repo}", "source_binding")
        branch = _text(meta.get("default_branch"), "source_binding")
        _need(
            meta.get("full_name") == self.repo
            and meta.get("archived") is False
            and meta.get("disabled") is False
            and isinstance(branch, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", branch)
            and all(part not in (".", "..") for part in branch.split("/")),
            "source_binding",
        )
        ref = self.obj(f"repos/{self.repo}/git/ref/heads/{branch}", "source_binding")
        obj = _object(ref.get("object"), "source_binding")
        _need(
            ref.get("ref") == "refs/heads/" + branch
            and obj.get("type") == "commit"
            and valid_sha(obj.get("sha")),
            "source_binding",
        )
        return ref["ref"], obj["sha"]

    def commit(self, repo: str, sha: str) -> dict[str, Any]:
        key = repo, sha
        if key not in self.commits:
            raw = self.obj(f"repos/{repo}/git/commits/{sha}", "source_binding")
            tree = _object(raw.get("tree"), "source_binding")
            parents = _list(raw.get("parents"), "source_binding")
            _need(
                raw.get("sha") == sha
                and valid_sha(tree.get("sha"))
                and isinstance(parents, list)
                and len(parents) <= 16,
                "source_binding",
            )
            seen = set()
            for parent in parents:
                value = _object(parent, "source_binding").get("sha")
                _need(valid_sha(value) and value != sha and value not in seen, "source_binding")
                seen.add(value)
            self.commits[key] = raw
        return self.commits[key]

    def tree(self, repo: str, sha: str) -> dict[str, dict[str, Any]]:
        key = repo, sha
        if key not in self.trees:
            raw = self.obj(f"repos/{repo}/git/trees/{sha}", "source_binding")
            rows = _list(raw.get("tree"), "source_binding")
            _need(
                raw.get("sha") == sha
                and raw.get("truncated") is False
                and isinstance(rows, list)
                and len(rows) <= MAX_TREE_ENTRIES,
                "source_binding",
            )
            result = {}
            for row in rows:
                item = _object(row, "source_binding")
                name = item.get("path")
                _need(
                    isinstance(name, str)
                    and name not in ("", ".", "..")
                    and "/" not in name
                    and not any(ord(c) < 32 or ord(c) == 127 for c in name)
                    and name not in result
                    and valid_sha(item.get("sha")),
                    "source_binding",
                )
                result[name] = item
            self.trees[key] = result
        return self.trees[key]

    def file(self, repo: str, commit_sha: str, path: str) -> bytes | None:
        sha = self.commit(repo, commit_sha)["tree"]["sha"]
        parts = path.split("/")
        for index, part in enumerate(parts):
            item = self.tree(repo, sha).get(part)
            if item is None:
                return None
            if index + 1 != len(parts):
                _need(item.get("type") == "tree" and item.get("mode") == "040000", "source_binding")
                sha = item["sha"]
                continue
            _need(
                item.get("type") == "blob" and item.get("mode") in ("100644", "100755"),
                "source_binding",
            )
            key = repo, item["sha"]
            if key not in self.blobs:
                raw = self.obj(f"repos/{repo}/git/blobs/{item['sha']}", "source_binding")
                content, size = _text(raw.get("content"), "source_binding"), raw.get("size")
                _need(
                    raw.get("sha") == item["sha"]
                    and raw.get("encoding") == "base64"
                    and isinstance(content, str)
                    and len(content) <= 96 * 1024
                    and type(size) is int
                    and 0 <= size <= MAX_RECORD_BYTES,
                    "source_binding",
                )
                try:
                    value = base64.b64decode(
                        content.replace("\n", "").replace("\r", ""), validate=True
                    )
                except (ValueError, binascii.Error):
                    _hold("source_binding")
                digest = hashlib.sha1(f"blob {len(value)}\0".encode() + value).hexdigest()
                _need(len(value) == size and digest == item["sha"], "source_binding")
                self.blobs[key] = value
            return self.blobs[key]
        _hold("source_binding")

    def pages(self, route: str, key: str | None = None) -> list[Any]:
        result = []
        total = None
        for page in range(1, MAX_PAGES + 1):
            data = self.github.json(
                f"{route}{'&' if '?' in route else '?'}per_page={PAGE_SIZE}&page={page}"
            )
            if key is None:
                rows = data
            else:
                obj = _object(data, "unavailable")
                count = obj.get("total_count")
                _need(type(count) is int and 0 <= count <= MAX_PAGES * PAGE_SIZE, "unavailable")
                _need(total is None or total == count, "unavailable")
                total = count
                rows = obj.get(key)
            rows = _list(rows, "unavailable")
            _need(len(rows) <= PAGE_SIZE, "unavailable")
            if total is not None:
                _need(len(rows) == min(PAGE_SIZE, total - len(result)), "unavailable")
            result.extend(rows)
            if (total is not None and len(result) == total) or (
                total is None and len(rows) < PAGE_SIZE
            ):
                return result
        _hold("unavailable")

    def merged_pr(self) -> None:
        rows = self.pages(f"repos/{self.repo}/commits/{self.source}/pulls")
        matches = []
        for raw in rows:
            row = _object(raw, "source_binding")
            if row.get("merge_commit_sha") == self.source and row.get("merged_at"):
                matches.append(row)
        _need(len(matches) == 1 and _positive(matches[0].get("number")), "source_binding")
        pull = self.obj(f"repos/{self.repo}/pulls/{matches[0]['number']}", "source_binding")
        base = _object(pull.get("base"), "source_binding")
        head = _object(pull.get("head"), "source_binding")
        _need(
            pull.get("number") == matches[0]["number"]
            and pull.get("merged") is True
            and pull.get("state") == "closed"
            and pull.get("merge_commit_sha") == self.source
            and base.get("ref") == self.ref.removeprefix("refs/heads/")
            and _object(base.get("repo"), "source_binding").get("full_name") == self.repo
            and _object(head.get("repo"), "source_binding").get("full_name") == self.repo
            and valid_sha(head.get("sha"))
            and isinstance(pull.get("merged_at"), str),
            "source_binding",
        )
        try:
            merged = datetime.fromisoformat(pull["merged_at"])
            _need(merged.utcoffset() is not None, "source_binding")
        except (ValueError, KeyError, TypeError):
            _hold("source_binding")

    def artifact(self, run_id: str, kind: str) -> FleetPublishRecord | None:
        key = run_id, kind
        if key not in self.artifacts:
            _need(kind in ("intent", "receipt"), "invalid_selection")
            actual = self.github.artifact(run_id, kind)
            self.artifacts[key] = None if actual is None else parse_record(actual)
        return self.artifacts[key]

    def native_row(self, row: dict[str, Any], run_id: str) -> None:
        _need(
            _positive(row.get("id"))
            and str(row["id"]) == run_id
            and _positive(row.get("run_attempt"))
            and type(row.get("workflow_id")) is int
            and row["workflow_id"] == self.workflow_id
            and row.get("path") == CENTRAL_WORKFLOW
            and row.get("event") == "workflow_dispatch"
            and row.get("head_branch") == "main"
            and valid_sha(row.get("head_sha"))
            and _object(row.get("repository"), "workflow_binding").get("full_name")
            == CENTRAL_REPOSITORY
            and _object(row.get("head_repository"), "workflow_binding").get("full_name")
            == CENTRAL_REPOSITORY,
            "workflow_binding",
        )
        status, conclusion = row.get("status"), row.get("conclusion")
        _need(
            status in ("queued", "in_progress", "waiting", "requested", "pending", "completed")
            and "conclusion" in row,
            "workflow_binding",
        )
        if status == "completed":
            _need(
                conclusion
                in (
                    "success",
                    "failure",
                    "cancelled",
                    "timed_out",
                    "action_required",
                    "neutral",
                    "skipped",
                    "stale",
                    "startup_failure",
                ),
                "workflow_binding",
            )
        else:
            _need(conclusion is None, "workflow_binding")
        _need(isinstance(row.get("display_title"), str), "workflow_binding")

    def protocol(self, workflow_sha: str) -> None:
        if workflow_sha not in self.protocols:
            verify_workflow_protocol(self.github, workflow_sha)
            self.protocols.add(workflow_sha)

    def fingerprint(self, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(row["id"]),
            row.get("head_sha"),
            row.get("run_attempt"),
            row.get("display_title"),
            row.get("workflow_id"),
            row.get("path"),
            row.get("event"),
            row.get("head_branch"),
            _object(row.get("repository"), "workflow_binding").get("full_name"),
            _object(row.get("head_repository"), "workflow_binding").get("full_name"),
        )

    def run(
        self, run_id: str, expected_sha: str | None = None, *, successful: bool = False
    ) -> dict[str, Any]:
        raw = self.obj(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}", "workflow_binding")
        attempt = self.obj(
            f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}/attempts/1", "workflow_binding"
        )
        self.native_row(raw, run_id)
        self.native_row(attempt, run_id)
        _need(raw["run_attempt"] == attempt["run_attempt"] == 1, "workflow_binding")
        _need(
            self.fingerprint(raw) == self.fingerprint(attempt)
            and (expected_sha is None or raw["head_sha"] == expected_sha),
            "workflow_binding",
        )
        self.protocol(raw["head_sha"])
        self.attempts[run_id] = attempt
        if successful:
            _need(
                all(
                    row.get("status") == "completed" and row.get("conclusion") == "success"
                    for row in (raw, attempt)
                ),
                "unconfirmed",
            )
        return raw

    def no_effect(self, run_id: str, run: dict[str, Any]) -> bool:
        if run.get("status") != "completed":
            _hold("pending")
        jobs = self.pages(
            f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}/attempts/1/jobs", "jobs"
        )
        _need(
            jobs
            and all(
                isinstance(job, dict)
                and job.get("name") in ("poll", "publish")
                and _positive(job.get("id"))
                for job in jobs
            ),
            "unavailable",
        )
        _need(len({job["id"] for job in jobs}) == len(jobs), "unavailable")
        publish = [row for row in jobs if isinstance(row, dict) and row.get("name") == "publish"]
        _need(len(publish) == 1, "unavailable")
        job = publish[0]
        _need(
            type(job.get("run_id")) is int
            and str(job["run_id"]) == run_id
            and type(job.get("run_attempt")) is int
            and job.get("run_attempt") == 1
            and job.get("head_sha") == run["head_sha"]
            and job.get("status") == "completed",
            "unavailable",
        )
        steps = _list(job.get("steps"), "unavailable")
        _need(isinstance(steps, list) and len(steps) <= 100, "unavailable")
        if job.get("conclusion") == "skipped" and not steps:
            return True
        for name in (CLAIM_STEP, EFFECT_STEP):
            matches = [row for row in steps if isinstance(row, dict) and row.get("name") == name]
            if (
                len(matches) != 1
                or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != "skipped"
            ):
                return False
        return True

    def originals(self) -> dict[str, FleetPublishRecord]:
        rows = self.pages(
            f"repos/{CENTRAL_REPOSITORY}/actions/workflows/{self.workflow_id}/runs?event=workflow_dispatch",
            "workflow_runs",
        )
        seen = set()
        result = {}
        snapshot = []
        locator = f"fleet-publish-v1:{self.repo}:{self.source}"
        for item in rows:
            row = _object(item, "unavailable")
            _need(_positive(row.get("id")) and row["id"] not in seen, "unavailable")
            seen.add(row["id"])
            run_id = str(row["id"])
            self.native_row(row, run_id)
            self.protocol(row["head_sha"])
            title = row.get("display_title")
            _need(
                isinstance(title, str)
                and re.fullmatch(
                    r"fleet-publish-v1:jbaruch/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}:[a-f0-9]{40}", title
                ),
                "workflow_binding",
            )
            snapshot.append(self.fingerprint(row))
            if title != locator:
                continue
            run = self.run(run_id, row["head_sha"])
            _need(self.fingerprint(run) == self.fingerprint(row), "workflow_binding")
            intent = self.artifact(run_id, "intent")
            if intent is None:
                _need(self.no_effect(run_id, run), "claim_binding")
                continue
            self.identity(intent)
            _need(
                intent["run_id"] == run_id and intent["workflow_sha"] == run["head_sha"],
                "workflow_binding",
            )
            self.intents[run_id] = intent
            if intent["origin_run_id"] == run_id:
                result[run_id] = intent
        self.run_snapshot = sorted(snapshot)
        _need(
            all(intent["origin_run_id"] in result for intent in self.intents.values()),
            "unavailable",
        )
        return result

    def identity(self, intent: FleetPublishRecord) -> None:
        _need(
            intent["state"] == "claimed"
            and intent["publish_outcome"] == "not_started"
            and intent["publish_exit_code"] is None
            and intent["landed_after_error"] is False
            and intent["moderation"] == "not_checked"
            and intent["bookkeeping_sha"] is None
            and intent["version"] is not None
            and intent["consumer_repository"] == self.repo
            and intent["source_sha"] == self.source
            and intent["source_tree_sha"] == self.source_tree
            and intent["source_ref"] == self.ref
            and intent["run_attempt"] == intent["origin_run_attempt"] == 1,
            "claim_binding",
        )
        marker = self.file(self.repo, self.source, MARKER)
        manifest = self.file(self.repo, self.source, MANIFEST)
        _need(marker is not None and manifest is not None, "source_binding")
        if marker is None or manifest is None:
            _hold("source_binding")
        enrollment = _json_file(marker)
        _need(
            set(enrollment) == {"schema_version", "plugin_name"}
            and type(enrollment["schema_version"]) is int
            and enrollment["schema_version"] == 1
            and enrollment["plugin_name"] == intent["plugin_name"]
            and _json_file(manifest).get("name") == intent["plugin_name"],
            "source_binding",
        )

    def claim(self, intent: FleetPublishRecord) -> str:
        self.identity(intent)
        matches = [sha for stored, sha in self.claims if stored == intent]
        if not matches and self.run(intent["run_id"])["status"] != "completed":
            _hold("pending")
        _need(len(matches) == 1, "claim_binding")
        sha = matches[0]
        _need(self.history.index(sha) < self.history.index(self.source), "claim_binding")
        return sha

    def selected(self, run_id: str | None) -> tuple[FleetPublishRecord, str]:
        originals = self.originals()
        _need(len(originals) <= 1, "ambiguous")
        if not originals:
            _hold("unavailable" if self.claims else "not_found")
        original_id = next(iter(originals))
        chosen = original_id if run_id is None else run_id
        _need(valid_run_id(chosen), "invalid_selection")
        intent = self.artifact(chosen, "intent")
        if intent is None:
            _hold("unavailable")
        self.identity(intent)
        run = self.run(chosen, intent["workflow_sha"])
        _need(
            chosen in self.intents
            and run.get("display_title") == f"fleet-publish-v1:{self.repo}:{self.source}",
            "workflow_binding",
        )
        _need(intent["origin_run_id"] == original_id, "ambiguous")
        return intent, self.claim(intent)

    def receipt(
        self, intent: FleetPublishRecord, claim_sha: str, *, confirmed: bool
    ) -> FleetPublishRecord:
        receipt = self.artifact(intent["run_id"], "receipt")
        if receipt is None:
            run = self.run(intent["run_id"], intent["workflow_sha"])
            _hold("pending" if run.get("status") != "completed" else "unavailable")
        _need(
            all(receipt[key] == intent[key] for key in intent if key not in MUTABLE_RECEIPT),
            "receipt_binding",
        )
        _need(
            receipt["bookkeeping_sha"] == claim_sha
            and receipt["version"] is not None
            and receipt["publish_outcome"] == "success"
            and receipt["publish_exit_code"] == 0
            and receipt["landed_after_error"] is False,
            "receipt_binding",
        )
        self.run(receipt["run_id"], receipt["workflow_sha"], successful=confirmed)
        if confirmed:
            _need(
                receipt["state"] == "confirmed" and receipt["moderation"] == "pass", "unconfirmed"
            )
        else:
            _need(receipt["state"] in ("published", "confirmed"), "receipt_binding")
            current = self.run(receipt["run_id"])
            _need(
                current["status"] == "completed"
                and self.attempts[receipt["run_id"]].get("status") == "completed",
                "pending",
            )
        retained = self.file(self.repo, claim_sha, STAMP)
        try:
            _need(retained is not None and decode_record(retained) == intent, "claim_binding")
        except FleetRecordError:
            _hold("claim_binding")
        return receipt

    def stable(self) -> None:
        _need(self.default() == (self.ref, self.tip), "source_binding")
        rows = self.pages(
            f"repos/{CENTRAL_REPOSITORY}/actions/workflows/{self.workflow_id}/runs?event=workflow_dispatch",
            "workflow_runs",
        )
        snapshot = []
        seen = set()
        for raw in rows:
            row = _object(raw, "unavailable")
            _need(_positive(row.get("id")) and row["id"] not in seen, "unavailable")
            seen.add(row["id"])
            self.native_row(row, str(row["id"]))
            snapshot.append(self.fingerprint(row))
        _need(sorted(snapshot) == self.run_snapshot, "unavailable")


def _execute(github: Any, repository: str, source_sha: str, run_id: str | None, *, confirmed: bool):
    try:
        _need(run_id is None or valid_run_id(run_id), "invalid_selection")
        evidence = _Evidence(github, repository, source_sha)
        intent, claim_sha = evidence.selected(run_id)
        if not confirmed:
            evidence.stable()
            return {
                "schema_version": 1,
                "database_id": int(intent["run_id"]),
                "confirmed": False,
                "record": intent,
            }
        receipt = evidence.receipt(intent, claim_sha, confirmed=True)
        if intent["origin_run_id"] != intent["run_id"]:
            origin = evidence.artifact(intent["origin_run_id"], "intent")
            _need(origin is not None, "unavailable")
            if origin is None:
                _hold("unavailable")
            origin_claim = evidence.claim(origin)
            original = evidence.receipt(origin, origin_claim, confirmed=False)
            _need(
                origin["origin_run_id"] == origin["run_id"]
                and all(
                    original[key] == receipt[key]
                    for key in (
                        "consumer_repository",
                        "source_sha",
                        "source_ref",
                        "source_tree_sha",
                        "plugin_name",
                        "registry_baseline",
                        "version",
                        "origin_run_id",
                        "origin_run_attempt",
                    )
                ),
                "receipt_binding",
            )
        evidence.stable()
        return receipt
    except FleetPublishLookupError as error:
        _hold(
            "expired"
            if error.kind == "expired_artifact"
            else "unknown_producer"
            if error.kind == "unknown_producer"
            else "unavailable"
        )
    except FleetRecordError:
        _hold("receipt_binding")


def select(
    github: Any, consumer_repository: str, source_sha: str, *, run_id: str | None = None
) -> dict[str, Any]:
    return _object(
        _execute(github, consumer_repository, source_sha, run_id, confirmed=False), "source_binding"
    )


def resolve(
    github: Any, consumer_repository: str, source_sha: str, *, run_id: str | None = None
) -> FleetPublishRecord:
    return parse_record(_execute(github, consumer_repository, source_sha, run_id, confirmed=True))


confirm = resolve


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default echoes arbitrary caller data; never expose it.
        _hold("source_binding")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Read source-bound central publication evidence.")
    parser.add_argument("repository")
    parser.add_argument("source_sha")
    parser.add_argument("--run-id")
    parser.add_argument("--confirmed", action="store_true")
    try:
        args = parser.parse_args(argv)
        api = GitHubClient()
        result = (
            resolve(api, args.repository, args.source_sha, run_id=args.run_id)
            if args.confirmed
            else select(api, args.repository, args.source_sha, run_id=args.run_id)
        )
        if args.confirmed:
            result = {
                "schema_version": 1,
                "database_id": int(result["run_id"]),
                "confirmed": True,
                "record": result,
            }
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except CentralPublishHold as error:
        print(json.dumps({"schema_version": 1, "status": "hold", "reason": error.kind}))
        print(
            "Central publication remains unconfirmed; restore native evidence and never replay an uncertain attempt.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
