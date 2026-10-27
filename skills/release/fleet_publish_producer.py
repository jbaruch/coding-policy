"""Reviewed central producer protocol identity; no generic YAML interpretation.

Each coherent pinset covers exactly four co-shipped source files. Renew by
reviewing all four and retaining older supported pinsets when the producer
changes; unknown versions hold. Native central-main ancestry is also required.
"""

from __future__ import annotations

from typing import Protocol

from fleet_publish_github import FleetPublishLookupError
from fleet_publish_record import CENTRAL_REPOSITORY, valid_sha

PRODUCER_PATHS = (
    ".github/workflows/fleet-publish.yml",
    "scripts/fleet_publish.py",
    "scripts/fleet_publish_workflow.py",
    "scripts/fleet_publish_validate.py",
)
# Reviewed together at writer freeze 6c10a9c81700650e964bbe1129cee7bce0b16635.
# This binds the fixed dispatch locator/input protocol, not its transitive
# execution dependencies. Renew only after reviewing all four source files;
# retain older coherent pinsets only while their protocols remain supported.
TRUSTED_PRODUCERS: tuple[dict[str, str], ...] = (
    {
        ".github/workflows/fleet-publish.yml": "65901aceb2df5cfbda830fec53877ff71f4926b5",
        "scripts/fleet_publish.py": "4fbd373795f5170373461b9fa7a3e236850fe1c2",
        "scripts/fleet_publish_workflow.py": "d7f7cc97389737c6b50c3c180b4a409802e4268f",
        "scripts/fleet_publish_validate.py": "7d450d115f6a661f3a0e1d3d8ab1a85451bb7a47",
    },
)


class GitHubRead(Protocol):
    def json(self, route: str) -> object: ...


def _require(value: object, kind: str = "unknown_producer") -> None:
    if not value:
        raise FleetPublishLookupError(kind) from None


def verify_workflow_protocol(client: GitHubRead, workflow_sha: str) -> None:
    """Verify immutable source plus actual own-main ancestry; grants no effect."""
    _require(valid_sha(workflow_sha))
    metadata = client.json(f"repos/{CENTRAL_REPOSITORY}")
    _require(isinstance(metadata, dict), "workflow_metadata")
    if not isinstance(metadata, dict):
        raise FleetPublishLookupError("workflow_metadata")
    _require(
        metadata.get("full_name") == CENTRAL_REPOSITORY
        and metadata.get("default_branch") == "main"
        and metadata.get("archived") is False
        and metadata.get("disabled") is False,
        "workflow_metadata",
    )
    ref = client.json(f"repos/{CENTRAL_REPOSITORY}/git/ref/heads/main")
    _require(isinstance(ref, dict), "workflow_metadata")
    if not isinstance(ref, dict):
        raise FleetPublishLookupError("workflow_metadata")
    obj = ref.get("object")
    _require(isinstance(obj, dict), "workflow_metadata")
    if not isinstance(obj, dict):
        raise FleetPublishLookupError("workflow_metadata")
    tip = obj.get("sha")
    _require(
        ref.get("ref") == "refs/heads/main" and obj.get("type") == "commit" and valid_sha(tip),
        "workflow_metadata",
    )
    comparison = client.json(f"repos/{CENTRAL_REPOSITORY}/compare/{workflow_sha}...{tip}")
    _require(isinstance(comparison, dict), "workflow_ancestry")
    if not isinstance(comparison, dict):
        raise FleetPublishLookupError("workflow_ancestry")
    _require(
        comparison.get("status") in ("ahead", "identical")
        and isinstance(comparison.get("base_commit"), dict)
        and comparison["base_commit"].get("sha") == workflow_sha
        and isinstance(comparison.get("merge_base_commit"), dict)
        and comparison["merge_base_commit"].get("sha") == workflow_sha,
        "workflow_ancestry",
    )
    commit = client.json(f"repos/{CENTRAL_REPOSITORY}/git/commits/{workflow_sha}")
    _require(isinstance(commit, dict))
    if not isinstance(commit, dict):
        raise FleetPublishLookupError("unknown_producer")
    root = commit.get("tree")
    _require(commit.get("sha") == workflow_sha and isinstance(root, dict))
    if not isinstance(root, dict):
        raise FleetPublishLookupError("unknown_producer")
    tree_cache: dict[str, dict[str, dict]] = {}

    def tree(sha: object) -> dict[str, dict]:
        _require(valid_sha(sha))
        if not isinstance(sha, str):
            raise FleetPublishLookupError("unknown_producer")
        if sha in tree_cache:
            return tree_cache[sha]
        response = client.json(f"repos/{CENTRAL_REPOSITORY}/git/trees/{sha}")
        _require(isinstance(response, dict))
        if not isinstance(response, dict):
            raise FleetPublishLookupError("unknown_producer")
        entries = response.get("tree")
        _require(
            response.get("sha") == sha
            and response.get("truncated") is False
            and isinstance(entries, list)
            and len(entries) <= 4096
        )
        if not isinstance(entries, list):
            raise FleetPublishLookupError("unknown_producer")
        result = {}
        for entry in entries:
            _require(isinstance(entry, dict))
            if not isinstance(entry, dict):
                raise FleetPublishLookupError("unknown_producer")
            name = entry.get("path")
            _require(
                isinstance(name, str)
                and name not in ("", ".", "..")
                and "/" not in name
                and "\x00" not in name
                and name not in result
                and valid_sha(entry.get("sha"))
            )
            result[name] = entry
        tree_cache[sha] = result
        return result

    actual = {}
    for path in PRODUCER_PATHS:
        sha = root.get("sha")
        parts = path.split("/")
        for index, component in enumerate(parts):
            entry = tree(sha).get(component)
            _require(entry is not None)
            if entry is None:
                raise FleetPublishLookupError("unknown_producer")
            if index + 1 == len(parts):
                _require(entry.get("type") == "blob" and entry.get("mode") in ("100644", "100755"))
                actual[path] = entry["sha"]
            else:
                _require(entry.get("type") == "tree" and entry.get("mode") == "040000")
                sha = entry["sha"]
    _require(
        any(set(pinset) == set(PRODUCER_PATHS) and actual == pinset for pinset in TRUSTED_PRODUCERS)
    )
