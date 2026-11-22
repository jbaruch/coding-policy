#!/usr/bin/env python3
"""Historical fleet publication transport, evidence and inert package transforms.

Only the completion finalizer and historical package verifier use this module.
It has no publication, dispatch, consumer-write or controller entry point.
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
from typing import Any, Iterator
from urllib.parse import quote

POLICY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POLICY / "skills/release"))
from fleet_publish_record import (  # noqa: E402 - trusted sibling schema owner.
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    FleetPublishRecord,
    decode_record,
    valid_repository,
    valid_sha,
)
from fleet_publish_github import FleetPublishLookupError, GitHubClient  # noqa: E402 - same trusted sibling owner.
from fleet_publish_evidence import (  # noqa: E402 - explicit evidence owner.
    PriorPublication,
    PublicationFacts,
    native_publication,
)

STAMP = ".github/fleet-publish-source.json"
MANIFEST = ".tessl-plugin/plugin.json"
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 4096
MAX_IGNORE_BYTES = 64 * 1024
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

    def json(self, route: str) -> object:
        """Read-only facade for the shared fixed central protocol verifier."""
        return self.api(route)

    @contextmanager
    def _archive_client(self) -> Iterator[Any]:
        # Each logical proof has its own bounded client; queued historical
        # bookkeeping cannot consume a later proof's lifetime allowance. The
        # shared reader retains its immutable/ref cache within this operation.
        central_token = os.environ.get("FLEET_CENTRAL_TOKEN")
        require(central_token, "Central evidence read credential is unavailable.")
        original = os.environ.get("GH_TOKEN")
        try:
            os.environ["GH_TOKEN"] = str(central_token)
            yield GitHubClient()
        finally:
            if original is None:
                os.environ.pop("GH_TOKEN", None)
            else:
                os.environ["GH_TOKEN"] = original

    def archive_operation(self, operation: Any) -> Any:
        from fleet_publish_archive import ArchiveReader

        try:
            with self._archive_client() as client:
                return operation(ArchiveReader(client))
        except FleetPublishLookupError as error:
            raise PublishRefused(
                "Restore complete central durable/native evidence; preserve STOP without replay."
            ) from error

    def record(self, run_id: str, kind: str) -> FleetPublishRecord | None:
        require(kind in ("intent", "receipt"), "Unsupported immutable record kind.")
        return self.archive_operation(lambda reader: reader.record(run_id, kind))

    def historical_source_proof(self, run_id: str) -> dict[str, Any] | None:
        return self.archive_operation(
            lambda reader: reader.historical_source_proof(run_id)
        )

    def live_record(self, run_id: str, kind: str) -> FleetPublishRecord | None:
        require(kind in ("intent", "receipt"), "Unsupported immutable record kind.")
        return self.archive_operation(
            lambda reader: reader.client.artifact_evidence(run_id, kind)
        )

    def attempt_evidence(
        self, record: FleetPublishRecord, *, successful: bool
    ) -> dict[str, Any]:
        return self.archive_operation(
            lambda reader: reader.verify_record_attempt(record, successful=successful)
        )

    def _command_environment(self, path: str, payload: Any) -> dict[str, str]:
        require(
            payload is None
            or path in {
                f"repos/{CENTRAL_REPOSITORY}/git/{kind}"
                for kind in ("blobs", "trees", "commits", "refs")
            },
            "Only create-only central archival writes remain supported.",
        )
        environment = dict(os.environ)
        if path == f"repos/{CENTRAL_REPOSITORY}" or path.startswith(
            f"repos/{CENTRAL_REPOSITORY}/"
        ):
            require(
                environment.get("FLEET_CENTRAL_TOKEN"),
                "Central artifact read credential is unavailable.",
            )
            environment["GH_TOKEN"] = environment["FLEET_CENTRAL_TOKEN"]
        return environment

    def api(
        self,
        path: str,
        payload: Any = None,
    ) -> Any:
        args = ["gh", "api", "--hostname", "github.com", path]
        if payload is not None:
            args += ["--method", "POST", "--input", "-"]
        environment = self._command_environment(path, payload)
        try:
            result = subprocess.run(
                args,
                input=encoded(payload) if payload is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
                timeout=GITHUB_COMMAND_TIMEOUT_SECONDS,
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
            return None  # A successful create-only archive write may have no body.
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
) -> PriorPublication | None:
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
    receipt = github.record(claim["run_id"], "receipt")
    if receipt is None:
        from fleet_publish_historical import prior_facts

        proof = github.historical_source_proof(claim["run_id"])
        require(
            proof is not None,
            "Original receipt and historical proof are unavailable; preserve STOP.",
        )
        if proof is None:
            raise PublishRefused("Restore verified original publication evidence.")
        require(
            proof["intent"]["intent"] == intent, "Historical source intent changed."
        )
        derived = prior_facts(proof)
        _, stamped_tree = tree(github, repo, derived["bookkeeping_sha"])
        require(
            decode_record(file(github, repo, stamped_tree, STAMP)) == intent,
            "Historical bookkeeping no longer contains its exact original intent.",
        )
        return {
            "proof_kind": "historical_source_proof",
            "proof": proof,
            "facts": derived,
        }
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
    return native_publication(receipt)


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


def stamp_package(
    package: Path, manifest: dict[str, Any], baseline: str, date: str | None
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
    github: GitHub, repo: str, source_text: str, prior: PublicationFacts | None
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
