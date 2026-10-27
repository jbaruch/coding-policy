"""Fixed metadata-only probe. Native successful token-mint step proves scopes.

The printed record is diagnostic data, not publication/absence/approval proof.
Consumers must inspect the source-bound workflow's preceding native mint result.
The inherited token is used only by gh; this module never reads its value.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/release"))
from fleet_publish_github import GitHubClient  # noqa: E402 - trusted sibling owner.
from fleet_publish_record import valid_run_id, valid_sha  # noqa: E402 - same owner.

CENTRAL = "jbaruch/coding-policy"
WORKFLOW = ".github/workflows/fleet-publish-grant-probe.yml"
TARGET = "jbaruch/nanoclaw-construction"


class GrantProbeRefused(ValueError):
    """Native metadata or fixed runtime is unavailable; do not enable publishing."""


def require(value: object) -> None:
    if not value:
        raise GrantProbeRefused(
            "Inspect the fixed probe's native metadata and mint steps before enabling publication."
        )


def context(environment: Mapping[str, str]) -> dict[str, Any]:
    require(
        environment.get("GITHUB_REPOSITORY") == CENTRAL
        and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and environment.get("GITHUB_REF") == "refs/heads/main"
        and environment.get("GITHUB_WORKFLOW_REF")
        == f"{CENTRAL}/{WORKFLOW}@refs/heads/main"
        and environment.get("GITHUB_RUN_ATTEMPT") == "1"
        and valid_run_id(environment.get("GITHUB_RUN_ID"))
        and valid_sha(environment.get("GITHUB_SHA"))
    )
    return {
        "workflow_repository": CENTRAL,
        "workflow_path": WORKFLOW,
        "workflow_sha": environment["GITHUB_SHA"],
        "run_id": environment["GITHUB_RUN_ID"],
        "run_attempt": 1,
    }


def obj(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GrantProbeRefused(
            "Inspect the probe's complete native metadata before enabling publication."
        )
    return value


def probe(client: GitHubClient, identity: dict[str, Any]) -> dict[str, Any]:
    repository = obj(client.json(f"repos/{TARGET}"))
    require(
        repository.get("full_name") == TARGET
        and repository.get("default_branch") == "main"
        and repository.get("archived") is False
        and repository.get("disabled") is False
    )
    branch = obj(client.json(f"repos/{TARGET}/branches/main"))
    sha = obj(branch.get("commit")).get("sha")
    require(branch.get("name") == "main" and valid_sha(sha))
    # One bounded page per endpoint. No assertion of complete PR/check coverage,
    # CI success, absence, or permission to perform an actual protected write.
    pulls = client.json(f"repos/{TARGET}/pulls?state=all&per_page=1&page=1")
    require(isinstance(pulls, list) and len(pulls) <= 1)
    checks = obj(
        client.json(f"repos/{TARGET}/commits/{sha}/check-runs?per_page=1&page=1")
    )
    statuses = obj(
        client.json(f"repos/{TARGET}/commits/{sha}/status?per_page=1&page=1")
    )
    for envelope, key in ((checks, "check_runs"), (statuses, "statuses")):
        rows = envelope.get(key)
        if not isinstance(rows, list):
            raise GrantProbeRefused(
                "Inspect the complete native metadata envelope before enabling publication."
            )
        require(len(rows) <= 1)
        require(
            type(envelope.get("total_count")) is int
            and envelope["total_count"] >= len(rows)
        )
    return {
        "schema_version": 1,
        **identity,
        "consumer_repository": TARGET,
        "consumer_ref": "refs/heads/main",
        "consumer_sha": sha,
        "requested_permissions": {
            "contents": "write",
            "pull_requests": "read",
            "checks": "read",
            "statuses": "read",
        },
        "permission_evidence": "preceding_successful_native_token_mint",
        "metadata_reads": "pass",
        "metadata_method": "GET",
        "revocation_evidence": "requires_successful_action_post_job",
        "publication_authorized": False,
        "protected_write_tested": False,
        "coverage_complete": False,
    }


def main() -> int:
    require(sys.argv[1:] in (["validate"], ["verify"]))
    identity = context(os.environ)
    if sys.argv[1] == "verify":
        print(
            json.dumps(
                probe(GitHubClient(), identity), sort_keys=True, separators=(",", ":")
            )
        )
    return 0


def entrypoint() -> int:
    # outer-boundary-process-contract: any failed probe must have no positive
    # stdout; fixed stderr/nonzero avoids leaking native metadata or credentials
    # through a raw traceback at this diagnostic process boundary.
    try:
        return main()
    except Exception:  # noqa: BLE001 - fixed outer boundary described above.
        print(
            "Grant probe refused. Inspect this run's mint/access steps; leave publication disabled and repair the existing prerequisite.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
