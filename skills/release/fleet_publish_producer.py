"""Central producer identity; no generic YAML interpretation.

A live publisher, poller or finalizer run counts when its workflow commit is on
the central repository's main history. The `main-append-only` ruleset keeps
that history from being rewritten or deleted; review happens before merge.
Runs of the activated pre-0.8 producer graphs keep their exact frozen pinsets,
so the immutable historical records they wrote stay verifiable.
"""

from __future__ import annotations

from typing import Protocol

from fleet_publish_github import FleetPublishLookupError
from fleet_publish_record import CENTRAL_REPOSITORY, valid_sha

PRODUCER_OWNER_PATH = "skills/release/fleet_publish_producer.py"

# Exact graphs of the activated pre-0.8 producers, frozen. They verify immutable
# historical-source-proof records and never change; new producers add no set.
HISTORICAL_PRODUCERS: tuple[dict[str, str], ...] = (
    {
        ".github/workflows/fleet-publish.yml": "12205f89e681d02f53f85d1ef55e450c579f6196",
        ".github/workflows/fleet-publish-finalize.yml": "a6dbeea9400979c59a7608d7f16eb8156e30e4e1",
        "scripts/fleet_publish.py": "b654076a9cad81bd2181cd0998f6a835f572091b",
        "scripts/fleet_publish_workflow.py": "64792b8f218dfe3c012f01691f01fc7f938faf9e",
        "scripts/fleet_publish_validate.py": "7d450d115f6a661f3a0e1d3d8ab1a85451bb7a47",
        "scripts/fleet_publish_targets.py": "541634a66c1a86dfcb9b2667878eb108216b6728",
        "scripts/fleet_publish_archive_write.py": "06a1fbad49b7f95b26e407e6040f48f373f69cd5",
        "skills/release/fleet_publish_archive.py": "2a0e8973ab596919f8753de2f4194688bd448cb4",
        "skills/release/fleet_publish_record.py": "54c706f73092833ec395e8486dafeb6bcd376e48",
        "skills/release/fleet_publish_github.py": "f9d4ced404485acfd5c11762a8a416a3b1f9c4c3",
        "skills/release/stamp-changelog.py": "9bc1ef64bc6473fcbbf14ad0873f6a64614dd0dc",
        ".github/actions/skill-review/action.yml": "799534fc0e7cf057db37e20611e161c94c9d6433",
        ".github/actions/skill-review/review-skills.sh": "42f635be4c5e377914266caf2be10107d1bb2f98",
        "skills/release/smart-publish.sh": "145178416aceea117b0997b61c1f04ccf28eeafb",
        "skills/release/registry-baseline.sh": "e6c55b47d6dd199fa5697f6cfe2531e8d5dd43b3",
        "skills/release/registry-version.sh": "a2109fc98dec02bd4c42218e6e4826596145f75e",
        "skills/release/registry-has-version.sh": "6266e89adb0c0bb40cec3bb1f0df9a8780931557",
        "skills/release/verify-moderation-cleared.sh": "36b4463cc2fb5b857e08ec15a36b05f4c218ef71",
        "skills/release/lib/bounded-run.sh": "b07e25df9c65f6ede8b39404c4ae7ecb0dc95ba0",
        "skills/release/fleet_publish_producer.py": "29f1df98fecccfdee93ed0b588c5fe57d03ac1c4",
    },
    # Retain the activated 0.5.17 graph under its original reader owner.
    {
        ".github/workflows/fleet-publish.yml": "12205f89e681d02f53f85d1ef55e450c579f6196",
        ".github/workflows/fleet-publish-finalize.yml": "9c2c60bf62730cc224792df21610c2bb3c27bba6",
        "scripts/fleet_publish.py": "c1a6e7ab7645fa7aad6752459d41e9cab25aa52a",
        "scripts/fleet_publish_workflow.py": "64792b8f218dfe3c012f01691f01fc7f938faf9e",
        "scripts/fleet_publish_validate.py": "7d450d115f6a661f3a0e1d3d8ab1a85451bb7a47",
        "scripts/fleet_publish_targets.py": "82e38c5c042f5f6ff09824b2c48e00c001b650db",
        "scripts/fleet_publish_archive_write.py": "711b57e318bf3f23d3d32bca60ef708d3a255a39",
        "skills/release/fleet_publish_archive.py": "f8e7083a225dec26c91bcbce624a0fbfbe795556",
        "skills/release/fleet_publish_historical.py": "02de6085aac707cff012302136f8e5be59aca7ca",
        "skills/release/fleet_publish_evidence.py": "97e44101819dfc6153b2fefd8031472821c32f96",
        "skills/release/fleet_publish_resolver.py": "83378bb646f0de4a3d8acad3a644e76cf85ecccd",
        "scripts/fleet_publish_historical_package.py": "4877ee6df9b496fd04b0a94978d9a6fab374fff0",
        "skills/release/fleet_publish_record.py": "54c706f73092833ec395e8486dafeb6bcd376e48",
        "skills/release/fleet_publish_github.py": "9639bdafb12db516f4cfcd20da4f2ed154964eb1",
        "skills/release/stamp-changelog.py": "582db93a14c067d55fb3f5a0b7ec0f14a99d645a",
        ".github/actions/skill-review/action.yml": "799534fc0e7cf057db37e20611e161c94c9d6433",
        ".github/actions/skill-review/review-skills.sh": "42f635be4c5e377914266caf2be10107d1bb2f98",
        "skills/release/smart-publish.sh": "145178416aceea117b0997b61c1f04ccf28eeafb",
        "skills/release/registry-baseline.sh": "e6c55b47d6dd199fa5697f6cfe2531e8d5dd43b3",
        "skills/release/registry-version.sh": "a2109fc98dec02bd4c42218e6e4826596145f75e",
        "skills/release/registry-has-version.sh": "6266e89adb0c0bb40cec3bb1f0df9a8780931557",
        "skills/release/verify-moderation-cleared.sh": "36b4463cc2fb5b857e08ec15a36b05f4c218ef71",
        "skills/release/lib/bounded-run.sh": "b07e25df9c65f6ede8b39404c4ae7ecb0dc95ba0",
        "skills/release/fleet_publish_producer.py": "2c84adb081309726b585014de135620943821005",
    },
    # Retain the published 0.5.23 graph under its original reader owner.
    {
        ".github/workflows/fleet-publish.yml": "12205f89e681d02f53f85d1ef55e450c579f6196",
        ".github/workflows/fleet-publish-finalize.yml": "9c2c60bf62730cc224792df21610c2bb3c27bba6",
        "scripts/fleet_publish.py": "c1a6e7ab7645fa7aad6752459d41e9cab25aa52a",
        "scripts/fleet_publish_workflow.py": "64792b8f218dfe3c012f01691f01fc7f938faf9e",
        "scripts/fleet_publish_validate.py": "7d450d115f6a661f3a0e1d3d8ab1a85451bb7a47",
        "scripts/fleet_publish_targets.py": "82e38c5c042f5f6ff09824b2c48e00c001b650db",
        "scripts/fleet_publish_archive_write.py": "32bd9e3a14dcf4d59b2cf18137c615f4619539e3",
        "skills/release/fleet_publish_archive.py": "f8e7083a225dec26c91bcbce624a0fbfbe795556",
        "skills/release/fleet_publish_historical.py": "02de6085aac707cff012302136f8e5be59aca7ca",
        "skills/release/fleet_publish_evidence.py": "97e44101819dfc6153b2fefd8031472821c32f96",
        "skills/release/fleet_publish_resolver.py": "83378bb646f0de4a3d8acad3a644e76cf85ecccd",
        "scripts/fleet_publish_historical_package.py": "4877ee6df9b496fd04b0a94978d9a6fab374fff0",
        "skills/release/fleet_publish_record.py": "54c706f73092833ec395e8486dafeb6bcd376e48",
        "skills/release/fleet_publish_github.py": "9639bdafb12db516f4cfcd20da4f2ed154964eb1",
        "skills/release/stamp-changelog.py": "582db93a14c067d55fb3f5a0b7ec0f14a99d645a",
        ".github/actions/skill-review/action.yml": "799534fc0e7cf057db37e20611e161c94c9d6433",
        ".github/actions/skill-review/review-skills.sh": "42f635be4c5e377914266caf2be10107d1bb2f98",
        "skills/release/smart-publish.sh": "145178416aceea117b0997b61c1f04ccf28eeafb",
        "skills/release/registry-baseline.sh": "e6c55b47d6dd199fa5697f6cfe2531e8d5dd43b3",
        "skills/release/registry-version.sh": "a2109fc98dec02bd4c42218e6e4826596145f75e",
        "skills/release/registry-has-version.sh": "6266e89adb0c0bb40cec3bb1f0df9a8780931557",
        "skills/release/verify-moderation-cleared.sh": "36b4463cc2fb5b857e08ec15a36b05f4c218ef71",
        "skills/release/lib/bounded-run.sh": "b07e25df9c65f6ede8b39404c4ae7ecb0dc95ba0",
        "skills/release/fleet_publish_producer.py": "ccbaeff9b2c8a5df3b09882861105adcf7d01d54",
    },

)


class GitHubRead(Protocol):
    def json(self, route: str) -> object: ...


def _require(value: object, kind: str = "unknown_producer") -> None:
    if not value:
        raise FleetPublishLookupError(kind) from None


def verify_workflow_protocol(client: GitHubRead, workflow_sha: str) -> None:
    """Require the workflow commit on central main's append-only history; grants no effect."""
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
        ref.get("ref") == "refs/heads/main"
        and obj.get("type") == "commit"
        and valid_sha(tip),
        "workflow_metadata",
    )
    comparison = client.json(
        f"repos/{CENTRAL_REPOSITORY}/compare/{workflow_sha}...{tip}"
    )
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


def historical_pinset(client: GitHubRead, workflow_sha: str) -> dict[str, str]:
    """The frozen historical pinset the workflow commit's tree matches exactly."""
    verify_workflow_protocol(client, workflow_sha)
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

    def lookup(path: str) -> str:
        sha = root.get("sha")
        parts = path.split("/")
        for index, component in enumerate(parts):
            entry = tree(sha).get(component)
            _require(entry is not None)
            if entry is None:
                raise FleetPublishLookupError("unknown_producer")
            if index + 1 == len(parts):
                _require(
                    entry.get("type") == "blob"
                    and entry.get("mode") in ("100644", "100755")
                )
                return str(entry["sha"])
            else:
                _require(entry.get("type") == "tree" and entry.get("mode") == "040000")
                sha = entry["sha"]
        raise FleetPublishLookupError("unknown_producer")

    remote_owner = lookup(PRODUCER_OWNER_PATH)
    for pins in HISTORICAL_PRODUCERS:
        if pins.get(PRODUCER_OWNER_PATH) == remote_owner and all(
            lookup(path) == expected for path, expected in pins.items()
        ):
            return dict(pins)
    raise FleetPublishLookupError("unknown_producer")
