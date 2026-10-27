#!/usr/bin/env python3
"""Trusted central publisher. Consumer files are data, never commands or hooks.

The workflow claims source, uploads intent.json, then publishes once and uploads
receipt.json. Both claim and immutable intent precede the publication effect.
A claimed source stamp is a STOP record, never a retry credential. GitHub API
failures, incomplete history and unavailable artifacts remain unknown/blocked.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any, Iterator
from datetime import datetime, timezone
from urllib.parse import quote

POLICY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POLICY / "skills/release"))
from fleet_publish_record import (  # noqa: E402 - trusted sibling schema owner.
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    FleetPublishRecord,
    decode_record,
    parse_record,
    valid_repository,
    valid_sha,
    valid_run_id,
)
from fleet_publish_github import FleetPublishLookupError, GitHubClient  # noqa: E402 - same trusted sibling owner.
from fleet_publish_producer import verify_workflow_protocol  # noqa: E402 - same trusted producer identity owner.

STAMP = ".github/fleet-publish-source.json"
MARKER = ".github/fleet-publish-enabled"
MANIFEST = ".tessl-plugin/plugin.json"
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 4096
MAX_IGNORE_BYTES = 64 * 1024
CLAIM_CI_INTERVAL_SECONDS = 10
CLAIM_CI_MAX_READS = 31  # Initial snapshot plus five minutes of bounded waiting.
CLAIM_CI_WAIT_SECONDS = 300
GITHUB_COMMAND_TIMEOUT_SECONDS = 30


class PublishRefused(ValueError):
    """A publication prerequisite is unknown or unsafe; do not replay it."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise PublishRefused(reason)


def native_object(value: object) -> dict[str, Any]:
    """Malformed native objects hold one consumer; never become authority."""
    if not isinstance(value, dict):
        raise PublishRefused(
            "Native GitHub metadata is incomplete; preserve this consumer's claim and repair its exact prerequisite."
        )
    return value


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON keys are unsupported.")
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    require(len(raw) <= MAX_BYTES, "Input exceeds the bounded JSON limit.")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: require(False, "Nonfinite JSON refused."),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PublishRefused("Restore complete valid UTF-8 JSON.") from error


def encoded(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


class GitHub:
    """Only fixed api.github.com routes; gh owns the installation credential."""

    def __init__(self) -> None:
        self._record_client = GitHubClient()

    def fresh_consumer(self) -> GitHub:
        """A consumer gets its own bounded evidence-client command allowance."""
        return GitHub()

    def json(self, route: str) -> object:
        """Read-only facade for the shared fixed central protocol verifier."""
        return self.api(route)

    def archive_operation(self, operation: Any) -> Any:
        # Each logical proof has its own bounded client; queued historical
        # bookkeeping cannot consume a later proof's lifetime allowance. The
        # shared reader retains its immutable/ref cache within this operation.
        from fleet_publish_archive import ArchiveReader

        central_token = os.environ.get("FLEET_CENTRAL_TOKEN")
        require(central_token, "Central evidence read credential is unavailable.")
        original = os.environ.get("GH_TOKEN")
        try:
            os.environ["GH_TOKEN"] = str(central_token)
            return operation(ArchiveReader(GitHubClient()))
        except FleetPublishLookupError as error:
            raise PublishRefused(
                "Restore complete central durable/native evidence; preserve STOP without replay."
            ) from error
        finally:
            if original is None:
                os.environ.pop("GH_TOKEN", None)
            else:
                os.environ["GH_TOKEN"] = original

    def record(self, run_id: str, kind: str) -> FleetPublishRecord | None:
        require(kind in ("intent", "receipt"), "Unsupported immutable record kind.")
        return self.archive_operation(lambda reader: reader.record(run_id, kind))

    def live_record(self, run_id: str, kind: str) -> FleetPublishRecord | None:
        require(kind in ("intent", "receipt"), "Unsupported immutable record kind.")
        return self.archive_operation(
            lambda reader: reader.client.artifact_evidence(run_id, kind)
        )

    def source_claim(self, repo: str, sha: str) -> dict[str, Any] | None:
        return self.archive_operation(lambda reader: reader.source_claim(repo, sha))

    def attempt_evidence(
        self, record: FleetPublishRecord, *, successful: bool
    ) -> dict[str, Any]:
        return self.archive_operation(
            lambda reader: reader.verify_record_attempt(record, successful=successful)
        )

    def verify_archive_claim(self, record: FleetPublishRecord, sha: str) -> None:
        from fleet_publish_archive import make_intent

        def check(reader: Any) -> None:
            require(
                reader.run_hold(record["run_id"]) is None
                and reader.run_intent(record["run_id"]) == make_intent(record, sha),
                "Durable source/run claim changed before effect; preserve STOP.",
            )

        self.archive_operation(check)

    def persist_archive_claim(self, record: FleetPublishRecord, sha: str) -> None:
        from fleet_publish_archive_write import persist_claim

        self.archive_operation(lambda reader: persist_claim(self, reader, record, sha))

    @contextmanager
    def read_deadline(self, deadline: float) -> Iterator[None]:
        """The CI watcher owns one wall-clock budget across all paginated reads."""
        previous = getattr(self, "_read_deadline", None)
        self._read_deadline = (
            min(previous, deadline) if previous is not None else deadline
        )
        try:
            yield
        finally:
            self._read_deadline = previous

    def command_timeout(self) -> float:
        remaining: float = GITHUB_COMMAND_TIMEOUT_SECONDS
        deadline = getattr(self, "_read_deadline", None)
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        require(
            remaining > 0,
            "GitHub read deadline expired; preserve the source claim without publication.",
        )
        return remaining

    def api(
        self,
        path: str,
        payload: Any = None,
        *,
        binary: bool = False,
        method: str = "POST",
    ) -> Any:
        args = ["gh", "api", "--hostname", "github.com", path]
        if payload is not None:
            args += ["--method", method, "--input", "-"]
        environment = dict(os.environ)
        if path.startswith(f"repos/{CENTRAL_REPOSITORY}/"):
            require(
                environment.get("FLEET_CENTRAL_TOKEN"),
                "Central artifact read credential is unavailable.",
            )
            environment["GH_TOKEN"] = environment["FLEET_CENTRAL_TOKEN"]
        try:
            result = subprocess.run(
                args,
                input=encoded(payload) if payload is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
                timeout=self.command_timeout(),
            )
        except subprocess.TimeoutExpired as error:
            raise PublishRefused(
                "GitHub command timed out; preserve STOP and verify its native outcome before any retry."
            ) from error
        require(
            result.returncode == 0,
            "GitHub API failed; preserve the source claim and inspect this run.",
        )
        require(
            len(result.stdout) <= MAX_BYTES,
            "GitHub response exceeds the admission bound.",
        )
        if not result.stdout and payload is not None:
            return None  # A successful dispatch is HTTP 204, never a missing read.
        return result.stdout if binary else decode(result.stdout)

    def optional_marker(self, repo: str) -> Any | None:
        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "--hostname",
                    "github.com",
                    f"repos/{repo}/contents/{MARKER}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.command_timeout(),
            )
        except subprocess.TimeoutExpired as error:
            raise PublishRefused(
                "Marker read timed out; enrollment remains unknown."
            ) from error
        if result.returncode != 0:
            require(
                b"HTTP 404" in result.stderr,
                "Marker read failed; enrollment is unknown.",
            )
            return None
        return decode(result.stdout)

    def pages(self, route: str, key: str | None = None) -> list[Any]:
        values: list[Any] = []
        identities: set[str] = set()
        expected_total: int | None = None
        for page in range(1, 11):
            joiner = "&" if "?" in route else "?"
            response = self.api(f"{route}{joiner}per_page=100&page={page}")
            require(
                not key or isinstance(response, dict) and key in response,
                "Native list envelope is malformed.",
            )
            items = response[key] if key else response
            require(
                isinstance(items, list) and len(items) <= 100,
                "GitHub pagination is malformed.",
            )
            if key:
                total = response.get("total_count")
                if type(total) is not int:
                    raise PublishRefused("Native pagination total is missing.")
                require(
                    type(total) is int
                    and 0 <= total <= 1000
                    and (expected_total is None or expected_total == total),
                    "Native pagination total is missing, changed, or beyond its bound.",
                )
                expected_total = total
                require(
                    len(items) == min(100, total - len(values)),
                    "Native pagination is incomplete; no absence or green gate inferred.",
                )
            for item in items:
                require(
                    isinstance(item, dict), "Native list contains a malformed object."
                )
                native_id = item.get("id")
                sha = item.get("sha")
                identity = (
                    str(native_id)
                    if type(native_id) is int and native_id > 0
                    else (sha if valid_sha(sha) else None)
                )
                if "/rules/branches/" in route:
                    ruleset_id, kind = item.get("ruleset_id"), item.get("type")
                    source_type, source_name = (
                        item.get("ruleset_source_type"),
                        item.get("ruleset_source"),
                    )
                    require(
                        type(ruleset_id) is int
                        and ruleset_id > 0
                        and isinstance(kind, str)
                        and kind
                        and source_type in ("Repository", "Organization")
                        and isinstance(source_name, str)
                        and source_name,
                        "Native branch rule identity is unavailable.",
                    )
                    identity = f"rule:{source_type}:{source_name}:{ruleset_id}:{kind}"
                require(
                    isinstance(identity, str)
                    and identity
                    and identity not in identities,
                    "Native pagination repeats or omits an object identity.",
                )
                if not isinstance(identity, str):
                    raise PublishRefused("Native object identity is missing.")
                identities.add(identity)
            values.extend(items)
            if (expected_total is not None and len(values) == expected_total) or (
                not key and len(items) < 100
            ):
                return values
        raise PublishRefused(
            "GitHub pagination exceeds the bound; no absence or completion inferred."
        )

    def blob(self, repo: str, sha: str) -> bytes:
        require(valid_sha(sha), "Invalid native blob identity.")
        response = native_object(self.api(f"repos/{repo}/git/blobs/{sha}"))
        require(
            response.get("encoding") == "base64", "Git blob encoding is unsupported."
        )
        try:
            raw = base64.b64decode(response["content"], validate=False)
        except (ValueError, TypeError) as error:
            raise PublishRefused("Git blob is malformed.") from error
        require(
            len(raw) == response.get("size") and len(raw) <= MAX_BYTES,
            "Git blob is clipped or oversized.",
        )
        return raw


def tree(github: GitHub, repo: str, sha: str) -> tuple[str, dict[str, dict[str, Any]]]:
    require(
        valid_repository(repo) and valid_sha(sha),
        "Select a same-owner native source SHA.",
    )
    commit = native_object(github.api(f"repos/{repo}/git/commits/{sha}"))
    require(
        commit.get("sha") == sha
        and valid_sha(native_object(commit.get("tree")).get("sha")),
        "Source commit is malformed.",
    )
    tree_sha = commit["tree"]["sha"]
    data = native_object(github.api(f"repos/{repo}/git/trees/{tree_sha}?recursive=1"))
    require(
        data.get("sha") == tree_sha and data.get("truncated") is False,
        "Source tree is incomplete.",
    )
    entries = data.get("tree")
    require(
        isinstance(entries, list) and len(entries) <= MAX_FILES,
        "Source tree exceeds its bound.",
    )
    if not isinstance(entries, list):
        raise PublishRefused("Native source tree entries are malformed.")
    result: dict[str, dict[str, Any]] = {}
    for value in entries:
        entry = native_object(value)
        path = entry.get("path")
        require(
            isinstance(path, str)
            and path
            and len(path) <= 4096
            and not any(
                ord(character) < 32 or ord(character) == 127 for character in path
            ),
            "Invalid source path.",
        )
        if not isinstance(path, str):
            raise PublishRefused("Native source path is malformed.")
        require(
            str(PurePosixPath(path)) == path
            and not path.startswith("/")
            and all(part not in (".", "..") for part in path.split("/")),
            "Unsafe source path.",
        )
        require(path not in result, "Duplicate source path.")
        result[path] = entry
    return tree_sha, result


def file(
    github: GitHub, repo: str, entries: dict[str, dict[str, Any]], path: str
) -> bytes:
    entry = entries.get(path, {})
    require(
        entry.get("type") == "blob" and entry.get("mode") in ("100644", "100755"),
        "Required source file is missing, special, or symlinked.",
    )
    return github.blob(repo, entry["sha"])


def enrollment(github: GitHub, repo: str, entries: dict[str, dict[str, Any]]) -> str:
    marker = decode(file(github, repo, entries, MARKER))
    require(
        isinstance(marker, dict)
        and set(marker) == {"schema_version", "plugin_name"}
        and type(marker["schema_version"]) is int
        and marker["schema_version"] == 1
        and isinstance(marker["plugin_name"], str)
        and re.fullmatch(r"jbaruch/[a-z0-9][a-z0-9_-]{0,99}", marker["plugin_name"]),
        "Enroll this exact plugin with the supported central publication marker.",
    )
    return marker["plugin_name"]


def default_tip(github: GitHub, repo: str) -> tuple[str, str]:
    require(
        valid_repository(repo) and repo != CENTRAL_REPOSITORY,
        "Select an enrolled consumer repository.",
    )
    metadata = native_object(github.api(f"repos/{repo}"))
    require(
        metadata.get("full_name") == repo
        and metadata.get("archived") is False
        and metadata.get("disabled") is False,
        "Consumer is not an active same-owner repository.",
    )
    branch = metadata.get("default_branch")
    require(
        isinstance(branch, str)
        and 0 < len(branch) <= 1000
        and not any(ord(c) < 32 for c in branch),
        "Default branch is unavailable.",
    )
    if not isinstance(branch, str):
        raise PublishRefused("Native default branch is malformed.")
    ref = native_object(
        github.api(f"repos/{repo}/git/ref/heads/{quote(branch, safe='')}")
    )
    sha = native_object(ref.get("object")).get("sha")
    require(
        valid_sha(sha) and native_object(ref.get("object")).get("type") == "commit",
        "Default tip is not a native commit.",
    )
    if not isinstance(sha, str):
        raise PublishRefused("Native default commit is malformed.")
    return "refs/heads/" + branch, sha


def utc_time(value: object) -> datetime:
    require(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value),
        "Native UTC timestamp is unavailable.",
    )
    if not isinstance(value, str):
        raise PublishRefused("Native UTC timestamp is unavailable.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError as error:
        raise PublishRefused("Native UTC timestamp is invalid.") from error


def merged_source(github: GitHub, repo: str, sha: str, ref: str) -> dict[str, Any]:
    """Read the actual merged PR, including its host-assigned merge timestamp."""
    pulls = github.pages(f"repos/{repo}/commits/{sha}/pulls")
    matches = []
    for value in pulls:
        candidate = native_object(value)
        if candidate.get("merge_commit_sha") != sha or not candidate.get("merged_at"):
            continue
        base = native_object(candidate.get("base"))
        if base.get("ref") != ref.removeprefix("refs/heads/"):
            continue
        head = native_object(candidate.get("head"))
        head_repo = native_object(head.get("repo"))
        if head_repo.get("full_name") == repo:
            matches.append(candidate)
    require(
        len(matches) == 1, "Source tip requires one verified merged same-repository PR."
    )
    summary = matches[0]
    require(
        type(summary.get("number")) is int and summary["number"] > 0,
        "Native source PR number is invalid.",
    )
    pull = native_object(github.api(f"repos/{repo}/pulls/{summary['number']}"))
    base = native_object(pull.get("base"))
    head = native_object(pull.get("head"))
    base_repo = native_object(base.get("repo"))
    head_repo = native_object(head.get("repo"))
    require(
        pull.get("number") == summary["number"]
        and pull.get("merged") is True
        and pull.get("state") == "closed"
        and pull.get("merge_commit_sha") == sha
        and base.get("ref") == ref.removeprefix("refs/heads/")
        and base_repo.get("full_name") == repo
        and head_repo.get("full_name") == repo,
        "Actual native PR does not bind this merged source and default branch.",
    )
    utc_time(pull.get("merged_at"))
    return pull


def required_contexts(
    github: GitHub, repo: str, ref: str
) -> set[tuple[str, int | None]]:
    """Union explicit legacy and active ruleset requirements; never infer 403/absence."""
    branch = ref.removeprefix("refs/heads/")
    route_branch = quote(branch, safe="")
    native = native_object(github.api(f"repos/{repo}/branches/{route_branch}"))
    require(
        native.get("name") == branch
        and type(native.get("protected")) is bool
        and valid_sha(native_object(native.get("commit")).get("sha")),
        "Native default-branch protection metadata is unavailable.",
    )
    requirements: set[tuple[str, int | None]] = set()

    def context(value: object) -> str:
        require(
            isinstance(value, str)
            and 0 < len(value) <= 255
            and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "Required CI context is unsupported.",
        )
        if not isinstance(value, str):
            raise PublishRefused("Required CI context is unavailable.")
        return value

    def app(value: object) -> int | None:
        require(
            value is None or type(value) is int and value > 0,
            "Required CI application binding is unknown.",
        )
        return value if type(value) is int else None

    protection = native_object(native.get("protection"))
    require(
        isinstance(protection, dict) and type(protection.get("enabled")) is bool,
        "Legacy branch requirements are incomplete.",
    )
    legacy = protection.get("required_status_checks")
    if not isinstance(legacy, dict):
        raise PublishRefused("Legacy required context/app data is unavailable.")
    require(
        isinstance(legacy, dict)
        and isinstance(legacy.get("contexts"), list)
        and isinstance(legacy.get("checks"), list),
        "Legacy required context/app data is incomplete.",
    )
    contexts = {context(name) for name in legacy["contexts"]}
    checks = legacy["checks"]
    require(
        len(legacy["contexts"]) <= 256 and len(checks) <= 256,
        "Too many required CI contexts.",
    )
    bound_names = set()
    for check in checks:
        require(
            isinstance(check, dict) and "app_id" in check,
            "Legacy required application binding is missing.",
        )
        name = context(check.get("context"))
        requirements.add((name, app(check["app_id"])))
        bound_names.add(name)
    require(
        contexts == bound_names, "Legacy contexts and application bindings disagree."
    )
    if protection["enabled"] is False:
        require(
            not requirements, "Disabled legacy protection contradicts required checks."
        )
    if native["protected"] is False:
        require(
            protection["enabled"] is False and not requirements,
            "Unprotected legacy branch metadata contradicts required checks.",
        )
    rules = github.pages(f"repos/{repo}/rules/branches/{route_branch}")
    if native["protected"] is True and protection["enabled"] is False:
        require(
            rules, "Protected branch has no complete legacy or active ruleset metadata."
        )
    non_ci_rules = {
        "creation",
        "update",
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "required_signatures",
        "pull_request",
    }
    for rule in rules:
        require(
            isinstance(rule, dict) and isinstance(rule.get("type"), str),
            "Active branch rule is malformed.",
        )
        kind = rule["type"]
        if kind in non_ci_rules:
            continue
        require(
            kind == "required_status_checks",
            "An unknown required workflow or CI rule must be inspected before publication.",
        )
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            raise PublishRefused("Active ruleset required checks are unavailable.")
        require(
            isinstance(parameters, dict)
            and isinstance(parameters.get("required_status_checks"), list),
            "Active ruleset required checks are unavailable.",
        )
        configured = parameters["required_status_checks"]
        require(len(configured) <= 256, "Too many ruleset required checks.")
        for check in configured:
            require(
                isinstance(check, dict) and "integration_id" in check,
                "Ruleset required application binding is missing.",
            )
            requirements.add(
                (context(check.get("context")), app(check["integration_id"]))
            )
    return requirements


def gate_source(github: GitHub, repo: str, sha: str, ref: str) -> str:
    """Require merged source + current dual reviews/CI; return native merge time."""
    pull = merged_source(github, repo, sha, ref)
    head = pull["head"]["sha"]
    require(valid_sha(head), "Merged PR head is unavailable.")
    reviews = github.pages(f"repos/{repo}/pulls/{pull['number']}/reviews")
    identities = {
        "policy": {"coding-policy-fleet-reviewer[bot]", "github-actions[bot]"},
        "quality": {"jbaruch-claude-reviewer[bot]"},
    }
    attributed = []
    for value in reviews:
        review = native_object(value)
        require("user" in review, "Native review attribution is missing.")
        if review["user"] is None:
            # GitHub retains a deleted account's review with no actor. It can
            # never count for either trusted bot lane, including an old bot.
            continue
        actor = native_object(review["user"])
        login = actor.get("login")
        require(
            isinstance(login, str)
            and 0 < len(login) <= 100
            and not any(ord(c) < 32 or ord(c) == 127 for c in login),
            "Native review attribution is malformed.",
        )
        attributed.append((login, review))
    for lane, logins in identities.items():
        latest: dict[str, Any] = {}
        latest_order: dict[str, tuple[datetime, int]] = {}
        for login, review in attributed:
            if login not in logins or review.get("commit_id") != head:
                continue
            body = review.get("body")
            require(
                body is None or isinstance(body, str),
                "Native bot review body is malformed.",
            )
            quality = isinstance(body, str) and body.startswith(
                "<!-- coding-policy:claude-quality-review:v1 -->"
            )
            if quality != (lane == "quality"):
                continue
            submitted = utc_time(review.get("submitted_at"))
            review_id = review.get("id")
            if type(review_id) is not int or review_id <= 0:
                raise PublishRefused("Native bot review identity is unavailable.")
            order = (submitted, review_id)
            if login not in latest_order or order > latest_order[login]:
                latest[login] = review
                latest_order[login] = order
        require(
            latest and all(r.get("state") == "APPROVED" for r in latest.values()),
            "Both current-head independent review lanes must approve the merged PR.",
        )
    checks = github.pages(f"repos/{repo}/commits/{head}/check-runs", "check_runs")
    statuses = github.pages(f"repos/{repo}/commits/{head}/status", "statuses")
    required = required_contexts(github, repo, ref)
    require(
        all(
            c.get("status") == "completed"
            and c.get("conclusion") in ("success", "neutral", "skipped")
            for c in checks
        )
        and (not checks or any(c.get("conclusion") == "success" for c in checks)),
        "Merged PR CI is not complete and green.",
    )
    require(
        all(status.get("state") == "success" for status in statuses),
        "Merged PR commit status is not green.",
    )
    for name, app_id in required:
        check_green = any(
            check.get("name") == name
            and check.get("status") == "completed"
            and check.get("conclusion") == "success"
            and (
                app_id is None
                or isinstance(check.get("app"), dict)
                and type(check["app"].get("id")) is int
                and check["app"]["id"] == app_id
            )
            for check in checks
        )
        status_green = app_id is None and any(
            status.get("context") == name and status.get("state") == "success"
            for status in statuses
        )
        require(
            check_green or status_green,
            "A configured required CI context is missing, incomplete, red, or from the wrong application.",
        )
    return pull["merged_at"]


def wait_claim_ci(github: GitHub, repo: str, sha: str, ref: str) -> None:
    """Read only the exact claimed commit; a timeout preserves its STOP record."""
    deadline = time.monotonic() + CLAIM_CI_WAIT_SECONDS
    with github.read_deadline(deadline):
        for attempt in range(CLAIM_CI_MAX_READS):
            require(
                time.monotonic() < deadline,
                "Claimed commit CI deadline expired; preserve STOP.",
            )
            required = required_contexts(github, repo, ref)
            checks = github.pages(
                f"repos/{repo}/commits/{sha}/check-runs", "check_runs"
            )
            statuses = github.pages(f"repos/{repo}/commits/{sha}/status", "statuses")
            pending = False
            for check in checks:
                status, conclusion = check.get("status"), check.get("conclusion")
                require(
                    status
                    in (
                        "queued",
                        "in_progress",
                        "completed",
                        "waiting",
                        "requested",
                        "pending",
                    ),
                    "Claimed commit CI status is unsupported; preserve the claim.",
                )
                if status != "completed":
                    require(
                        conclusion is None,
                        "Claimed commit CI metadata contradicts its pending state.",
                    )
                    pending = True
                else:
                    require(
                        conclusion in ("success", "neutral", "skipped"),
                        "Claimed commit CI failed; preserve the claim without publication.",
                    )
            for status in statuses:
                require(
                    status.get("state") in ("pending", "success"),
                    "Claimed commit status failed or is unsupported; preserve the claim.",
                )
                pending |= status["state"] == "pending"
            for name, app_id in required:
                check_green = any(
                    check.get("name") == name
                    and check.get("status") == "completed"
                    and check.get("conclusion") == "success"
                    and (
                        app_id is None
                        or isinstance(check.get("app"), dict)
                        and type(check["app"].get("id")) is int
                        and check["app"]["id"] == app_id
                    )
                    for check in checks
                )
                status_green = app_id is None and any(
                    status.get("context") == name and status.get("state") == "success"
                    for status in statuses
                )
                pending |= not (check_green or status_green)
            require(
                time.monotonic() < deadline,
                "Claimed commit CI exceeded its wall-clock deadline; preserve STOP.",
            )
            if not pending:
                return
            if attempt + 1 < CLAIM_CI_MAX_READS:
                remaining = deadline - time.monotonic()
                require(
                    remaining > 0, "Claimed commit CI deadline expired; preserve STOP."
                )
                time.sleep(min(CLAIM_CI_INTERVAL_SECONDS, remaining))
    raise PublishRefused(
        "Claimed commit CI remains missing or pending after the fixed wait; preserve the claim and do not replay publication."
    )


def dispatch_catalog_route(merged_at: str) -> str:
    utc_time(merged_at)
    return (
        f"repos/{CENTRAL_REPOSITORY}/actions/workflows/fleet-publish.yml/runs"
        f"?event=workflow_dispatch&created={quote('>=' + merged_at, safe='')}"
    )


def artifact(
    github: GitHub, run_id: str, name: str, basename: str
) -> FleetPublishRecord:
    require(
        (name, basename)
        in (
            ("fleet-publish-intent-v1", "intent.json"),
            ("fleet-publish-receipt-v1", "receipt.json"),
        ),
        "Unsupported artifact selection.",
    )
    record = github.record(run_id, "intent" if basename == "intent.json" else "receipt")
    require(
        record is not None, "Immutable central artifact is missing; absence is unknown."
    )
    if record is None:
        raise PublishRefused("Immutable central artifact is missing.")
    return record


def verify_attempt(
    github: GitHub, record: FleetPublishRecord, *, successful: bool
) -> dict[str, Any]:
    evidence = github.attempt_evidence(record, successful=successful)
    run, attempt = evidence["run"], evidence["attempt"]
    require(
        str(run.get("id")) == record["run_id"]
        and run.get("run_attempt") == record["run_attempt"] == 1
        and run.get("head_sha") == record["workflow_sha"]
        and run.get("head_branch") == "main"
        and run.get("path") == CENTRAL_WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and (
            run.get("head_repository") == CENTRAL_REPOSITORY
            or isinstance(run.get("head_repository"), dict)
            and run["head_repository"].get("full_name") == CENTRAL_REPOSITORY
        )
        and attempt.get("head_sha") == record["workflow_sha"]
        and str(attempt.get("id")) == record["run_id"]
        and attempt.get("head_branch") == "main"
        and attempt.get("event") == "workflow_dispatch"
        and attempt.get("path") == CENTRAL_WORKFLOW
        and attempt.get("run_attempt") == record["run_attempt"],
        "Central run/attempt binding is invalid.",
    )
    if successful:
        require(
            attempt.get("status") == "completed"
            and attempt.get("conclusion") == "success",
            "A successful actual central attempt is required for confirmed publication.",
        )
    commit = github.api(
        f"repos/{record['consumer_repository']}/git/commits/{record['source_sha']}"
    )
    commit = native_object(commit)
    require(
        commit.get("sha") == record["source_sha"]
        and native_object(commit.get("tree")).get("sha") == record["source_tree_sha"],
        "Source tree binding changed.",
    )
    return evidence


def previous(
    github: GitHub, repo: str, entries: dict[str, dict[str, Any]]
) -> FleetPublishRecord | None:
    history = github.pages(f"repos/{repo}/commits?path={quote(STAMP, safe='')}")
    if STAMP not in entries:
        require(
            not history,
            "A deleted historical claim cannot authorize replay; recover its original run.",
        )
        return None
    require(
        history,
        "A source-provided claim without native history cannot authorize publication.",
    )
    claim = decode_record(file(github, repo, entries, STAMP))
    require(
        claim["consumer_repository"] == repo and claim["state"] == "claimed",
        "Unsupported source claim.",
    )
    intent = artifact(github, claim["run_id"], "fleet-publish-intent-v1", "intent.json")
    require(
        claim == intent, "Source claim does not match its immutable central intent."
    )
    verify_attempt(github, intent, successful=False)
    receipt = artifact(
        github, claim["run_id"], "fleet-publish-receipt-v1", "receipt.json"
    )
    for key in (
        "consumer_repository",
        "source_sha",
        "source_ref",
        "source_tree_sha",
        "workflow_sha",
        "run_id",
        "run_attempt",
        "plugin_name",
        "origin_run_id",
        "origin_run_attempt",
    ):
        require(
            receipt[key] == intent[key],
            "Terminal receipt does not match its original intent.",
        )
    require(
        receipt["state"] in ("published", "confirmed")
        and receipt["version"] is not None
        and receipt["bookkeeping_sha"] is not None
        and receipt["publish_outcome"] == "success"
        and receipt["publish_exit_code"] == 0
        and not receipt["landed_after_error"],
        "Existing publication is uncertain or failed; preserve it without replay.",
    )
    terminal_evidence = verify_attempt(
        github, receipt, successful=receipt["state"] == "confirmed"
    )
    terminal_attempt = terminal_evidence["attempt"]
    require(
        terminal_attempt.get("status") == "completed",
        "Previous publication attempt is still in progress.",
    )
    bookkeeping_sha = receipt["bookkeeping_sha"]
    if bookkeeping_sha is None:
        raise PublishRefused("Published bookkeeping SHA is missing.")
    _, stamped_tree = tree(github, repo, bookkeeping_sha)
    require(
        decode_record(file(github, repo, stamped_tree, STAMP)) == intent,
        "Published bookkeeping commit does not contain its exact intent.",
    )
    if receipt["origin_run_id"] != receipt["run_id"]:
        origin_intent = artifact(
            github, receipt["origin_run_id"], "fleet-publish-intent-v1", "intent.json"
        )
        origin = artifact(
            github, receipt["origin_run_id"], "fleet-publish-receipt-v1", "receipt.json"
        )
        verify_attempt(github, origin, successful=False)
        require(
            origin_intent["state"] == "claimed"
            and all(
                origin_intent[key] == origin[key]
                for key in (
                    "consumer_repository",
                    "source_sha",
                    "source_ref",
                    "source_tree_sha",
                    "workflow_sha",
                    "run_id",
                    "run_attempt",
                    "plugin_name",
                    "origin_run_id",
                    "origin_run_attempt",
                )
            ),
            "Original publication intent does not match its terminal receipt.",
        )
        require(
            origin["run_attempt"] == receipt["origin_run_attempt"]
            and origin["origin_run_id"] == origin["run_id"]
            and origin["consumer_repository"] == repo
            and origin["source_sha"] == receipt["source_sha"]
            and origin["source_tree_sha"] == receipt["source_tree_sha"]
            and origin["plugin_name"] == receipt["plugin_name"]
            and origin["version"] == receipt["version"]
            and origin["publish_outcome"] == "success"
            and origin["publish_exit_code"] == 0
            and not origin["landed_after_error"],
            "Moderation recovery must retain a proven actual successful publication origin.",
        )
        origin_bookkeeping = origin["bookkeeping_sha"]
        if origin_bookkeeping is None:
            raise PublishRefused("Original publication bookkeeping is missing.")
        _, origin_tree = tree(github, repo, origin_bookkeeping)
        require(
            decode_record(file(github, repo, origin_tree, STAMP)) == origin_intent,
            "Original native bookkeeping does not contain its immutable intent.",
        )
    return receipt


def projection(
    github: GitHub,
    repo: str,
    entries: dict[str, dict[str, Any]],
    destination: Path,
    plugin_name: str,
) -> dict[str, Any]:
    """Copy declared data only. Never consumer config, git, workflow or hooks files."""
    ignore = file(github, repo, entries, ".tesslignore")
    require(
        len(ignore) <= MAX_IGNORE_BYTES and b"\x00" not in ignore,
        "Restore a bounded regular UTF-8 .tesslignore before publication.",
    )
    try:
        ignore.decode("utf-8")
    except UnicodeError as error:
        raise PublishRefused(
            "Restore valid UTF-8 package exclusion patterns."
        ) from error
    manifest = decode(file(github, repo, entries, MANIFEST))
    require(
        isinstance(manifest, dict)
        and manifest.get("name") == plugin_name
        and isinstance(manifest.get("version"), str)
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"]),
        "Plugin identity/version mismatches enrollment.",
    )
    require(
        set(manifest)
        <= {
            "name",
            "version",
            "description",
            "private",
            "skills",
            "rules",
            "hooks",
            "nativeHooks",
        },
        "Unsupported manifest fields require trusted central packaging support.",
    )
    require(
        not manifest.get("hooks") and not manifest.get("nativeHooks"),
        "Lifecycle-hook plugins require reviewed inert packaging support; no consumer hook executes here.",
    )
    roots: list[str] = []
    for kind in ("skills", "rules"):
        values = manifest.get(kind, [])
        require(
            isinstance(values, list) and len(values) <= 256,
            "Invalid declared plugin surface.",
        )
        for path in values:
            require(
                isinstance(path, str)
                and path.startswith(kind + "/")
                and str(PurePosixPath(path)) == path
                and all(p not in ("", ".", "..") for p in path.split("/")),
                "Unsafe declared plugin path.",
            )
            require(path in entries, "Declared plugin surface is missing.")
            roots.append(path)
    require(len(set(roots)) == len(roots), "Duplicate declared plugin roots.")
    selected = {MANIFEST, "README.md", "CHANGELOG.md", ".tesslignore"}
    selected.update(p for p in ("LICENSE", "LICENSE.md", "LICENSE.txt") if p in entries)
    for path, entry in entries.items():
        if entry.get("type") == "tree":
            continue
        if any(path == root or path.startswith(root + "/") for root in roots):
            # These can become runtime instruction/config injection to the CLI;
            # declared script payload remains inert data, never executed here.
            require(
                not any(
                    p
                    in (
                        ".tessl",
                        ".git",
                        ".claude",
                        ".codex",
                        ".mcp.json",
                        "AGENTS.md",
                        "tessl.json",
                    )
                    for p in path.split("/")
                ),
                "Nested CLI configuration is not package data.",
            )
            selected.add(path)
    require(
        not destination.exists(),
        "Package destination must be new and centrally selected.",
    )
    destination.mkdir(mode=0o700, parents=True)
    total = 0
    for path in sorted(selected):
        raw = file(github, repo, entries, path)
        total += len(raw)
        require(total <= MAX_BYTES, "Plugin projection exceeds its byte bound.")
        output = destination / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
        output.chmod(0o644)
    # Preserve the exact inert source patterns; CLI packaging owns their grammar.
    # .git/.github are absent from this projection, even if source patterns negate
    # an ignore for them. No consumer CLI config, hooks or commands are loaded.
    # The canonical review helper discovers an empty skill set with find().
    # A rule-only bootstrap has no physical skills directory in Git.
    (destination / "skills").mkdir(exist_ok=True)
    return manifest


def helper(
    name: str, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    script = POLICY / "skills/release" / name
    interpreter = "python3" if name.endswith(".py") else "bash"
    return subprocess.run(
        [interpreter, str(script), *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        check=False,
    )


def stamp_package(
    package: Path, manifest: dict[str, Any], baseline: str, date: str
) -> str:
    spec = importlib.util.spec_from_file_location(
        "fleet_stamp", POLICY / "skills/release/stamp-changelog.py"
    )
    if spec is None or spec.loader is None:
        raise PublishRefused("Trusted changelog helper is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = module.compute_version(
        manifest["version"], None if baseline == "unpublished" else baseline
    )
    text = (package / "CHANGELOG.md").read_text()
    stamped, _ = module.stamp_changelog(text, version, date)
    (package / "CHANGELOG.md").write_text(stamped)
    manifest["version"] = version
    (package / MANIFEST).write_bytes(encoded(manifest))
    return version


def pending_sources(
    github: GitHub, repo: str, tip: str, prior: FleetPublishRecord | None, plugin: str
) -> list[str]:
    """First-parent queue, not a latest-tip coalescer. Bound or refuse history."""
    pending: list[str] = []
    cursor = tip
    for _ in range(128):
        if prior and cursor == prior["source_sha"]:
            return list(reversed(pending))
        commit = github.api(f"repos/{repo}/git/commits/{cursor}")
        require(
            commit.get("sha") == cursor and isinstance(commit.get("parents"), list),
            "First-parent source history is incomplete.",
        )
        _, entries = tree(github, repo, cursor)
        if MARKER not in entries:
            require(
                prior is None, "Enrollment was removed inside the pending source queue."
            )
            history = github.pages(
                f"repos/{repo}/commits?path={quote(MARKER, safe='')}"
            )
            require(
                len(history) == 1,
                "Deleted/re-enrolled marker history cannot reset the source boundary.",
            )
            return list(reversed(pending))
        require(
            enrollment(github, repo, entries) == plugin,
            "Plugin identity changed inside the source queue.",
        )
        bookkeeping = previous(github, repo, entries) if STAMP in entries else None
        if bookkeeping is None or bookkeeping["bookkeeping_sha"] != cursor:
            pending.append(cursor)
        else:
            recovered = (
                prior is not None
                and prior["state"] == "confirmed"
                and all(
                    bookkeeping[key] == prior[key]
                    for key in (
                        "consumer_repository",
                        "source_sha",
                        "source_tree_sha",
                        "version",
                        "origin_run_id",
                        "origin_run_attempt",
                    )
                )
            )
            require(
                bookkeeping["state"] == "confirmed" or recovered,
                "A paused earlier publication blocks later source admission.",
            )
        parents = commit["parents"]
        if not parents:
            require(
                prior is None,
                "The previously published source is no longer in main history.",
            )
            marker_history = github.pages(
                f"repos/{repo}/commits?path={quote(MARKER, safe='')}"
            )
            require(
                len(marker_history) == 1,
                "Initial native enrollment history is ambiguous.",
            )
            return list(reversed(pending))
        require(
            isinstance(parents[0], dict) and valid_sha(parents[0].get("sha")),
            "Native first parent is invalid.",
        )
        cursor = parents[0]["sha"]
    raise PublishRefused(
        "Pending first-parent history exceeds its bound; do not skip earlier merges."
    )


def changelog_body(text: str) -> str:
    require(
        text.startswith("# Changelog\n"),
        "Restore the canonical changelog H1 before publication.",
    )
    return text[len("# Changelog\n") :]


def replace_suffix(current: str, old: str, new: str) -> str:
    """Stamp only the selected source; retain every newer unheaded entry."""
    current_body, old_body, new_body = map(changelog_body, (current, old, new))
    require(
        old_body and current_body.endswith(old_body),
        "Changelog history is not an exact suffix; do not stamp newer or reordered entries.",
    )
    return "# Changelog\n" + current_body[: -len(old_body)] + new_body


def overlay_previous_changelog(
    github: GitHub, repo: str, source_text: str, prior: FleetPublishRecord | None
) -> str:
    if prior is None:
        return source_text
    _, old_entries = tree(github, repo, prior["source_sha"])
    original = file(github, repo, old_entries, "CHANGELOG.md").decode("utf-8")
    bookkeeping_sha = prior["bookkeeping_sha"]
    if bookkeeping_sha is None:
        raise PublishRefused("Previous bookkeeping is unknown.")
    _, published_entries = tree(github, repo, bookkeeping_sha)
    live_stamped = file(github, repo, published_entries, "CHANGELOG.md").decode("utf-8")
    # Native trusted bookkeeping may also contain NEWER unheaded entries.
    # Only its exact published-version suffix belongs in the old source package.
    lines = live_stamped.splitlines(keepends=True)
    matches = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(
            r"## " + re.escape(str(prior["version"])) + r" — \d{4}-\d{2}-\d{2}\n?", line
        )
    ]
    require(len(matches) == 1, "Previous stamped version cannot be uniquely recovered.")
    published = "# Changelog\n\n" + "".join(lines[matches[0] :])
    if changelog_body(source_text).endswith(changelog_body(published)):
        return source_text
    return replace_suffix(source_text, original, published)


def push_claim(
    github: GitHub, record: FleetPublishRecord, expected: str, files: dict[str, bytes]
) -> str:
    repo, ref = record["consumer_repository"], record["source_ref"]
    require(
        default_tip(github, repo) == (ref, expected),
        "Main advanced before claim; no publication dispatched.",
    )
    base = github.api(f"repos/{repo}/git/commits/{expected}")
    entries = []
    for path, raw in {**files, STAMP: encoded(record)}.items():
        blob = github.api(
            f"repos/{repo}/git/blobs",
            {"content": base64.b64encode(raw).decode(), "encoding": "base64"},
        )
        require(valid_sha(blob.get("sha")), "Bookkeeping blob creation failed.")
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    new_tree = github.api(
        f"repos/{repo}/git/trees", {"base_tree": base["tree"]["sha"], "tree": entries}
    )
    commit = github.api(
        f"repos/{repo}/git/commits",
        {
            "message": f"Claim central publication {record['run_id']}",
            "tree": new_tree["sha"],
            "parents": [expected],
            "author": {
                "name": "github-actions[bot]",
                "email": "41898282+github-actions[bot]@users.noreply.github.com",
            },
        },
    )
    require(valid_sha(commit.get("sha")), "Bookkeeping commit creation failed.")
    # REST force:false only forbids non-fast-forward updates. GraphQL beforeOid
    # also rejects concurrent ancestor resets, so the expected tip is atomic.
    metadata = native_object(github.api(f"repos/{repo}"))
    node_id = metadata.get("node_id")
    require(
        metadata.get("full_name") == repo
        and isinstance(node_id, str)
        and 0 < len(node_id) <= 256
        and node_id.isascii(),
        "Native selected repository identity is unavailable.",
    )
    mutation_id = f"fleet-publish:{record['run_id']}:{record['run_attempt']}"
    changed = github.api(
        "graphql",
        {
            "query": "mutation FleetPublishClaim($input: UpdateRefsInput!) { updateRefs(input: $input) { clientMutationId } }",
            "variables": {
                "input": {
                    "repositoryId": node_id,
                    "clientMutationId": mutation_id,
                    "refUpdates": [
                        {
                            "name": ref,
                            "beforeOid": expected,
                            "afterOid": commit["sha"],
                            "force": False,
                        }
                    ],
                }
            },
        },
    )
    require(
        isinstance(changed, dict)
        and changed.get("errors") in (None, [])
        and isinstance(changed.get("data"), dict)
        and isinstance(changed["data"].get("updateRefs"), dict)
        and changed["data"]["updateRefs"].get("clientMutationId") == mutation_id,
        "Atomic source claim failed or is uncertain; do not publish or retry.",
    )
    require(
        default_tip(github, repo) == (ref, commit["sha"]),
        "Claim push is uncertain; do not publish or retry.",
    )
    return commit["sha"]


def save(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(encoded(value))


def initial_record(
    repo: str,
    sha: str,
    ref: str,
    tree_sha: str,
    plugin: str,
    baseline: str,
    version: str,
    environment: dict[str, str],
) -> FleetPublishRecord:
    require(
        environment.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and environment.get("GITHUB_REF") == "refs/heads/main"
        and environment.get("GITHUB_WORKFLOW_REF", "")
        == CENTRAL_REPOSITORY + "/" + CENTRAL_WORKFLOW + "@refs/heads/main"
        and environment.get("GITHUB_RUN_ATTEMPT") == "1",
        "Only a new selected central main workflow run can publish; reruns are refused.",
    )
    return parse_record(
        {
            "schema_version": 1,
            "consumer_repository": repo,
            "source_sha": sha,
            "source_ref": ref,
            "source_tree_sha": tree_sha,
            "workflow_repository": CENTRAL_REPOSITORY,
            "workflow_sha": environment["GITHUB_SHA"],
            "run_id": environment["GITHUB_RUN_ID"],
            "run_attempt": 1,
            "plugin_name": plugin,
            "state": "claimed",
            "registry_baseline": baseline,
            "version": version,
            "publish_outcome": "not_started",
            "publish_exit_code": None,
            "landed_after_error": False,
            "moderation": "not_checked",
            "origin_run_id": environment["GITHUB_RUN_ID"],
            "origin_run_attempt": 1,
            "bookkeeping_sha": None,
        }
    )


def run_binding(github: GitHub, run: dict[str, Any], workflow_id: int) -> None:
    run = native_object(run)
    require(
        valid_run_id(str(run.get("id")))
        and run.get("workflow_id") == workflow_id
        and run.get("path") == CENTRAL_WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "main"
        and valid_sha(run.get("head_sha"))
        and native_object(run.get("head_repository")).get("full_name")
        == CENTRAL_REPOSITORY
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1,
        "Native central dispatch identity is incomplete or unsupported.",
    )
    actual = github.api(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run['id']}")
    attempt = github.api(
        f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
    )
    actual, attempt = native_object(actual), native_object(attempt)
    for key in (
        "id",
        "workflow_id",
        "path",
        "event",
        "head_branch",
        "head_sha",
        "run_attempt",
    ):
        require(
            actual.get(key) == run.get(key) == attempt.get(key),
            "Native dispatch listing/run/attempt identity changed.",
        )


def independent_source_claims(
    github: GitHub,
    source: FleetPublishRecord,
    environment: dict[str, str],
    prior: FleetPublishRecord | None,
    merged_at: str,
) -> None:
    """The create-only central SOURCE key survives consumer and Actions expiry.

    Absence is eligible only under the reviewed mandatory durable protocol and
    current native run/source binding. Existing originals permit only their
    proven moderation lineage; source-controlled stamps never reset identity.
    """
    workflow = github.api(
        f"repos/{CENTRAL_REPOSITORY}/actions/workflows/fleet-publish.yml"
    )
    require(
        workflow.get("path") == CENTRAL_WORKFLOW
        and type(workflow.get("id")) is int
        and workflow["id"] > 0,
        "Fixed central workflow identity is unavailable.",
    )
    current_id = environment.get("GITHUB_RUN_ID", "")
    require(
        valid_run_id(current_id)
        and environment.get("GITHUB_SHA") == source["workflow_sha"]
        and current_id == source["run_id"]
        and environment.get("GITHUB_RUN_ATTEMPT") == "1",
        "Actual current workflow context is missing.",
    )
    current = github.api(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{current_id}")
    run_binding(github, current, workflow["id"])
    require(
        str(current.get("id")) == current_id
        and current.get("head_sha") == source["workflow_sha"]
        and current.get("run_attempt") == 1,
        "Current run cannot be excluded from uniqueness checks.",
    )
    cutoff = utc_time(merged_at)
    require(
        utc_time(current.get("created_at")) >= cutoff,
        "A dispatch queued before the native source merge cannot publish this source.",
    )
    try:
        verify_workflow_protocol(github, current["head_sha"])
    except FleetPublishLookupError as error:
        raise PublishRefused(
            "Central durable producer protocol is unknown; preserve source claims."
        ) from error
    require(
        current.get("display_title")
        == f"fleet-publish-v1:{source['consumer_repository']}:{source['source_sha']}",
        "Actual current dispatch locator does not bind this source.",
    )
    original = github.source_claim(source["consumer_repository"], source["source_sha"])
    if original is None:
        require(
            source["origin_run_id"] == source["run_id"],
            "Recovery requires the original durable source claim.",
        )
        return
    intent = original["intent"]
    require(
        source["origin_run_id"] != source["run_id"]
        and prior is not None
        and intent["run_id"] == source["origin_run_id"]
        and all(
            intent[key] == prior[key] == source[key]
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
        ),
        "An independent durable original already claims this source; never publish it again.",
    )


def prepare(
    github: GitHub,
    repo: str,
    expected_sha: str,
    work: Path,
    environment: dict[str, str],
) -> None:
    """No publish or cross-repository write in this phase."""
    ref, tip = default_tip(github, repo)
    tree_sha, entries = tree(github, repo, tip)
    plugin = enrollment(github, repo, entries)
    prior = previous(github, repo, entries)
    pending = (
        []
        if prior and prior["state"] == "published"
        else pending_sources(github, repo, tip, prior, plugin)
    )
    if (
        prior
        and prior["state"] == "confirmed"
        and not pending
        and tip == prior["bookkeeping_sha"]
    ):
        require(
            expected_sha in (tip, prior["source_sha"]),
            "Requested source is not this confirmed publication.",
        )
        save(
            work / "selection.json",
            {"mode": "already_confirmed", "consumer_repository": repo},
        )
        return
    if prior:
        compare = github.api(f"repos/{repo}/compare/{prior['bookkeeping_sha']}...{tip}")
        require(
            compare.get("status") in ("ahead", "identical"),
            "Source history diverged from the proven publication.",
        )
    if prior and prior["state"] == "published":
        if tip != prior["bookkeeping_sha"]:
            old_claim = decode_record(file(github, repo, entries, STAMP))
            prior_bookkeeping = prior["bookkeeping_sha"]
            if prior_bookkeeping is None:
                raise PublishRefused("Pending moderation bookkeeping is missing.")
            retained_claim(github, old_claim, prior_bookkeeping, tip)
        require(
            expected_sha == prior["source_sha"],
            "Select the original published source for moderation recovery.",
        )
        record = initial_record(
            repo,
            prior["source_sha"],
            ref,
            prior["source_tree_sha"],
            plugin,
            prior["registry_baseline"],
            str(prior["version"]),
            environment,
        )
        record.update(
            origin_run_id=prior["origin_run_id"],
            origin_run_attempt=prior["origin_run_attempt"],
        )
        mode = "moderation_only"
        merged_at = merged_source(github, repo, prior["source_sha"], ref)["merged_at"]
        independent_source_claims(github, record, environment, prior, merged_at)
    else:
        require(
            pending and pending[0] == expected_sha,
            "Select the EARLIEST pending merged source; never skip or coalesce merges.",
        )
        source_sha = pending[0]
        tree_sha, source_entries = tree(github, repo, source_sha)
        merged_at = gate_source(github, repo, source_sha, ref)
        manifest = projection(github, repo, source_entries, work / "package", plugin)
        original_manifest = dict(manifest)
        package_changelog = work / "package/CHANGELOG.md"
        selected_text = overlay_previous_changelog(
            github, repo, package_changelog.read_text(), prior
        )
        package_changelog.write_text(selected_text)
        baseline_result = helper("registry-baseline.sh", *plugin.split("/"))
        require(
            baseline_result.returncode == 0,
            "Registry baseline is unknown; no publication claimed.",
        )
        baseline = baseline_result.stdout.decode().strip()
        version = stamp_package(
            work / "package", manifest, baseline, environment["FLEET_PUBLISH_DATE"]
        )
        record = initial_record(
            repo, source_sha, ref, tree_sha, plugin, baseline, version, environment
        )
        independent_source_claims(github, record, environment, prior, merged_at)
        bookkeeping_dir = work / "bookkeeping"
        bookkeeping_dir.mkdir(mode=0o700)
        main_changelog = file(github, repo, entries, "CHANGELOG.md").decode("utf-8")
        (bookkeeping_dir / "CHANGELOG.md").write_text(
            replace_suffix(main_changelog, selected_text, package_changelog.read_text())
        )
        current_manifest_bytes = file(github, repo, entries, MANIFEST)
        current_manifest = decode(current_manifest_bytes)
        # Never overwrite a newer source's manifest metadata/manual version.
        current_metadata = {k: v for k, v in current_manifest.items() if k != "version"}
        source_metadata = {k: v for k, v in original_manifest.items() if k != "version"}
        allowed_versions = {
            original_manifest["version"],
            prior["version"] if prior else None,
        }
        if (
            current_metadata == source_metadata
            and current_manifest.get("version") in allowed_versions
        ):
            current_manifest_bytes = (work / "package" / MANIFEST).read_bytes()
        (bookkeeping_dir / "manifest.json").write_bytes(current_manifest_bytes)
        # Scrub the App/dispatch tokens before the data-only Tessl commands.
        cli_env = {
            k: v
            for k, v in environment.items()
            if k not in ("GH_TOKEN", "GITHUB_TOKEN", "FLEET_CENTRAL_TOKEN")
        }
        lint = subprocess.run(
            ["tessl", "plugin", "lint"], cwd=work / "package", env=cli_env, check=False
        )
        require(lint.returncode == 0, "Package lint failed; no publication claimed.")
        cli_env.update(
            WORKSPACE="jbaruch",
            THRESHOLD="85",
            SKILLS_DIR="skills",
            CREDIT_OUTAGE="fail",
            EVENT_NAME="workflow_dispatch",
            BASE_OVERRIDE="",
            EVENT_BEFORE="",
            GITHUB_OUTPUT=os.devnull,
            GITHUB_STEP_SUMMARY=os.devnull,
        )
        reviewed = subprocess.run(
            ["bash", str(POLICY / ".github/actions/skill-review/review-skills.sh")],
            cwd=work / "package",
            env=cli_env,
            check=False,
        )
        require(
            reviewed.returncode == 0,
            "Mandatory skill review failed; no publication claimed.",
        )
        mode = "publish"
    save(work / "intent.json", record)
    save(work / "selection.json", {"mode": mode, "expected_sha": tip})


def claim(github: GitHub, work: Path) -> None:
    record = decode_record((work / "intent.json").read_bytes())
    selection = decode((work / "selection.json").read_bytes())
    require(
        selection.get("mode") in ("publish", "moderation_only"),
        "No new selected intent exists.",
    )
    verify_attempt(github, record, successful=False)
    files = (
        {}
        if selection["mode"] == "moderation_only"
        else {
            MANIFEST: (work / "bookkeeping/manifest.json").read_bytes(),
            "CHANGELOG.md": (work / "bookkeeping/CHANGELOG.md").read_bytes(),
        }
    )
    claimed_sha = push_claim(github, record, selection["expected_sha"], files)
    _, claimed_entries = tree(github, record["consumer_repository"], claimed_sha)
    require(
        decode_record(
            file(github, record["consumer_repository"], claimed_entries, STAMP)
        )
        == record,
        "Actual claimed commit does not retain its exact intent; preserve STOP.",
    )
    github.persist_archive_claim(record, claimed_sha)
    save(work / "claim.json", {"bookkeeping_sha": claimed_sha})


def retained_claim(
    github: GitHub, intent: FleetPublishRecord, claim_sha: str, tip: str
) -> None:
    """Later main merges cannot erase/reintroduce or replace an in-flight claim."""
    cursor = tip
    for _ in range(128):
        if cursor == claim_sha:
            return
        _, entries = tree(github, intent["consumer_repository"], cursor)
        require(
            decode_record(file(github, intent["consumer_repository"], entries, STAMP))
            == intent
            and enrollment(github, intent["consumer_repository"], entries)
            == intent["plugin_name"],
            "A later main change altered enrollment or claim; do not dispatch.",
        )
        commit = github.api(
            f"repos/{intent['consumer_repository']}/git/commits/{cursor}"
        )
        parents = native_object(commit).get("parents")
        require(
            isinstance(parents, list)
            and parents
            and valid_sha(native_object(parents[0]).get("sha")),
            "Claimed first-parent history is incomplete or divergent.",
        )
        if not isinstance(parents, list):
            raise PublishRefused("Native first-parent history is malformed.")
        cursor = parents[0]["sha"]
    raise PublishRefused(
        "Post-claim main history exceeds its bound; preserve STOP without publication."
    )


def verify_current_claim(
    github: GitHub, intent: FleetPublishRecord, claim_sha: str
) -> None:
    """Re-read current authority; CI waiting does not lease branch enrollment."""
    current_ref, current_tip = default_tip(github, intent["consumer_repository"])
    require(
        current_ref == intent["source_ref"],
        "Default source branch changed after claim.",
    )
    if current_tip != claim_sha:
        comparison = github.api(
            f"repos/{intent['consumer_repository']}/compare/{claim_sha}...{current_tip}"
        )
        require(
            native_object(comparison).get("status") == "ahead",
            "Claimed history diverged; preserve STOP without publication.",
        )
        retained_claim(github, intent, claim_sha, current_tip)


def execute(github: GitHub, work: Path) -> bool:
    """Only this phase can invoke publish, once, after immutable+durable intent."""
    intent = decode_record((work / "intent.json").read_bytes())
    record = parse_record(intent)
    selection = decode((work / "selection.json").read_bytes())
    claim_data = decode((work / "claim.json").read_bytes())
    claim_sha = claim_data.get("bookkeeping_sha")
    require(
        github.live_record(record["run_id"], "intent") == intent,
        "Immutable intent upload is unavailable; retain durable STOP without publication.",
    )
    require(valid_sha(claim_sha), "Claimed native commit is missing.")
    verify_current_claim(github, intent, claim_sha)
    _, claimed_tree = tree(github, record["consumer_repository"], claim_sha)
    require(
        decode_record(file(github, record["consumer_repository"], claimed_tree, STAMP))
        == intent,
        "Durable pre-effect claim is missing or changed.",
    )
    wait_claim_ci(
        github, record["consumer_repository"], claim_sha, record["source_ref"]
    )
    github.verify_archive_claim(intent, claim_sha)
    # Reviews and source CI are live gates, not leased by prepare() or the
    # bookkeeping wait. Bind the immutable selected source again before effects.
    gate_source(
        github,
        record["consumer_repository"],
        record["source_sha"],
        record["source_ref"],
    )
    verify_current_claim(github, intent, claim_sha)
    record["bookkeeping_sha"] = claim_sha
    # Persist conservative local terminal evidence BEFORE the effect. Workflow
    # failure/cancellation still leaves the durable claimed source STOP record.
    record.update(state="uncertain", publish_outcome="unknown", moderation="unknown")
    save(work / "receipt.json", record)
    if selection["mode"] == "publish":
        cli_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GH_TOKEN", "GITHUB_TOKEN", "FLEET_CENTRAL_TOKEN")
        }
        result = helper(
            "smart-publish.sh",
            "as-is",
            str(work / "package"),
            record["source_ref"].removeprefix("refs/heads/"),
            "branch",
            "true",
            env=cli_env,
        )
        output = decode(result.stdout)
        require(
            isinstance(output, dict)
            and type(output.get("publish_exit_code")) is int
            and type(output.get("landed_after_error")) is bool
            and (
                output.get("version") is None
                or output.get("version") == intent["version"]
            ),
            "Publish result is malformed; preserve uncertainty.",
        )
        if result.returncode == 0 and output.get("outcome") == "success":
            require(
                output.get("version") == intent["version"],
                "Published version does not match the exact prepared source intent.",
            )
        record.update(
            publish_exit_code=output["publish_exit_code"],
            landed_after_error=output["landed_after_error"],
            version=output.get("version"),
        )
        record["publish_outcome"] = (
            "success"
            if result.returncode == 0 and output.get("outcome") == "success"
            else "failure"
        )
        if (
            record["publish_outcome"] != "success"
            or record["publish_exit_code"] != 0
            or record["landed_after_error"]
        ):
            record["state"] = "uncertain"
            (work / "receipt.json").write_bytes(encoded(parse_record(record)))
            return False
    else:
        require(
            selection["mode"] == "moderation_only"
            and record["origin_run_id"] != record["run_id"],
            "Recovery must be a new run, never a publication replay.",
        )
        record.update(
            publish_outcome="success", publish_exit_code=0, landed_after_error=False
        )
    record.update(state="published", moderation="not_checked")
    (work / "receipt.json").write_bytes(encoded(parse_record(record)))
    version = record["version"]
    if version is None:
        raise PublishRefused("A published version must be known before landing checks.")
    landing = helper(
        "registry-has-version.sh", *record["plugin_name"].split("/"), version
    )
    require(
        landing.returncode == 0 and decode(landing.stdout).get("exists") is True,
        "Exact version landing is unconfirmed; no retry permitted.",
    )
    moderation = helper(
        "verify-moderation-cleared.sh", *record["plugin_name"].split("/"), version
    )
    if moderation.returncode in (0, 1):
        response = decode(moderation.stdout)
        status = response.get("moderation_status")
        record["moderation"] = (
            "pass"
            if moderation.returncode == 0 and response.get("ok") is True
            else (
                "fail"
                if status
                in (
                    "fail",
                    "failed",
                    "flagged",
                    "blocked",
                    "rejected",
                    "reject",
                    "denied",
                )
                else "pending"
            )
        )
    else:
        record["moderation"] = "unknown"
    if record["moderation"] == "pass":
        record["state"] = "confirmed"
    (work / "receipt.json").write_bytes(encoded(parse_record(record)))
    return record["state"] == "confirmed"
