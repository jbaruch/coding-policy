"""Reviewed central producer protocol identity; no generic YAML interpretation.

Each coherent pinset covers the complete repository-owned executable closure.
The trust root is this actual locally installed, published reader owner, not a
target checkout or a caller's receipt. An arbitrary source checkout confers no
publication authority. Unknown versions hold; native main ancestry is required.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Protocol

from fleet_publish_github import FleetPublishLookupError
from fleet_publish_record import CENTRAL_REPOSITORY, valid_sha

# Closed repository-owned executable graph reached by the publisher, poller and
# terminal archive verifier. Embedded shell/Python programs are covered by their
# containing blob. Runner tools and Actions are selected by the pinned workflow;
# consumer package bytes remain inert data, never additions to this inventory.
PRODUCER_OWNER_PATH = "skills/release/fleet_publish_producer.py"
PRODUCER_PATHS = (
    ".github/workflows/fleet-publish.yml",
    ".github/workflows/fleet-publish-finalize.yml",
    "scripts/fleet_publish.py",
    "scripts/fleet_publish_workflow.py",
    "scripts/fleet_publish_validate.py",
    "scripts/fleet_publish_targets.py",
    "scripts/fleet_publish_archive_write.py",
    "skills/release/fleet_publish_archive.py",
    "skills/release/fleet_publish_record.py",
    "skills/release/fleet_publish_github.py",
    "skills/release/stamp-changelog.py",
    ".github/actions/skill-review/action.yml",
    ".github/actions/skill-review/review-skills.sh",
    "skills/release/smart-publish.sh",
    "skills/release/registry-baseline.sh",
    "skills/release/registry-version.sh",
    "skills/release/registry-has-version.sh",
    "skills/release/verify-moderation-cleared.sh",
    "skills/release/lib/bounded-run.sh",
    PRODUCER_OWNER_PATH,
)
# One coherent prospective protocol; the earlier seven-file sets were never
# activated and are unsupported. Review every closure member before renewal.
# The producer owner is separately anchored to the installed reader below,
# avoiding a circular literal hash or a remote source's self-attestation.
TRUSTED_PRODUCERS: tuple[dict[str, str], ...] = (
    {
        ".github/workflows/fleet-publish.yml": "12205f89e681d02f53f85d1ef55e450c579f6196",
        ".github/workflows/fleet-publish-finalize.yml": "a6dbeea9400979c59a7608d7f16eb8156e30e4e1",
        "scripts/fleet_publish.py": "b367516abfba79de05d7868d06d26deb3ce3a845",
        "scripts/fleet_publish_workflow.py": "64792b8f218dfe3c012f01691f01fc7f938faf9e",
        "scripts/fleet_publish_validate.py": "7d450d115f6a661f3a0e1d3d8ab1a85451bb7a47",
        "scripts/fleet_publish_targets.py": "541634a66c1a86dfcb9b2667878eb108216b6728",
        "scripts/fleet_publish_archive_write.py": "06a1fbad49b7f95b26e407e6040f48f373f69cd5",
        "skills/release/fleet_publish_archive.py": "2a0e8973ab596919f8753de2f4194688bd448cb4",
        "skills/release/fleet_publish_record.py": "2068ec2bd83b6095e31e34e967ad39574e021b89",
        "skills/release/fleet_publish_github.py": "ca1897cf394723a30adc6f8a49c3a8fbbb06912e",
        "skills/release/stamp-changelog.py": "9bc1ef64bc6473fcbbf14ad0873f6a64614dd0dc",
        ".github/actions/skill-review/action.yml": "bb12d1fd56feccb5ff1f844dbaefb57353b21fed",
        ".github/actions/skill-review/review-skills.sh": "9be33fad87c9b52183d22ff847197cf4852232b9",
        "skills/release/smart-publish.sh": "145178416aceea117b0997b61c1f04ccf28eeafb",
        "skills/release/registry-baseline.sh": "e6c55b47d6dd199fa5697f6cfe2531e8d5dd43b3",
        "skills/release/registry-version.sh": "a2109fc98dec02bd4c42218e6e4826596145f75e",
        "skills/release/registry-has-version.sh": "6266e89adb0c0bb40cec3bb1f0df9a8780931557",
        "skills/release/verify-moderation-cleared.sh": "36b4463cc2fb5b857e08ec15a36b05f4c218ef71",
        "skills/release/lib/bounded-run.sh": "b07e25df9c65f6ede8b39404c4ae7ecb0dc95ba0",
    },
)


class GitHubRead(Protocol):
    def json(self, route: str) -> object: ...


def _require(value: object, kind: str = "unknown_producer") -> None:
    if not value:
        raise FleetPublishLookupError(kind) from None


def local_owner_blob() -> str:
    """Bind remote code to the safe, stable installed reader, never remote input."""
    _require(Path(__file__).is_absolute())
    try:
        fd = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            _require(
                stat.S_ISREG(before.st_mode)
                and before.st_nlink == 1
                and before.st_uid in (0, os.geteuid())
                and not before.st_mode & 0o022
                and 0 < before.st_size <= 128 * 1024
            )
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(fd, remaining)
                _require(chunk)
                chunks.append(chunk)
                remaining -= len(chunk)

            def fingerprint(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_uid,
                    info.st_gid,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )

            _require(
                fingerprint(before)
                == fingerprint(os.fstat(fd))
                == fingerprint(os.stat(__file__, follow_symlinks=False))
            )
            raw = b"".join(chunks)
            return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        finally:
            os.close(fd)
    except OSError:
        raise FleetPublishLookupError("unknown_producer") from None


def verify_workflow_protocol(client: GitHubRead, workflow_sha: str) -> None:
    """Verify immutable source plus actual own-main ancestry; grants no effect."""
    _require(valid_sha(workflow_sha))
    owner_blob = local_owner_blob()
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
                _require(
                    entry.get("type") == "blob"
                    and entry.get("mode") in ("100644", "100755")
                )
                actual[path] = entry["sha"]
            else:
                _require(entry.get("type") == "tree" and entry.get("mode") == "040000")
                sha = entry["sha"]
    _require(actual.pop(PRODUCER_OWNER_PATH) == owner_blob)
    _require(
        any(
            set(pinset) == set(PRODUCER_PATHS) - {PRODUCER_OWNER_PATH}
            and actual == pinset
            for pinset in TRUSTED_PRODUCERS
        )
    )
