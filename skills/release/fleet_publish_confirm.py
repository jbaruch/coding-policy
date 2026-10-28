#!/usr/bin/env python3
"""Confirm a central source publication against its receipt and live registry.

Usage: fleet_publish_confirm.py REPOSITORY SOURCE_SHA PRE_BASELINE RUN_ID
Success emits one JSON object containing confirmed/version/source/run identity.
Exit 1 is a definitive failed publication or registry/moderation refusal; exit 2
is unsupported input or unavailable/uncertain evidence. Neither emits stdout.
This reader never publishes, dispatches, writes a source claim or retries work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

from fleet_publish_github import ResolutionGitHubClient as GitHubClient
from fleet_publish_record import CENTRAL_REPOSITORY, FleetPublishRecord, valid_run_id
from fleet_publish_resolver import CentralPublishHold, resolve

ROOT = Path(__file__).resolve().parent
MAX_HELPER_BYTES = 64 * 1024
Helper = Callable[..., subprocess.CompletedProcess[bytes]]


class ConfirmationHold(ValueError):
    """No confirmation; native evidence must be inspected without replay."""


class ConfirmationFailed(ConfirmationHold):
    """A canonical reader returned a definitive negative verdict."""


def numeric(version: object) -> tuple[int, int, int]:
    # Canonical registry readers support released numeric versions only.
    if not isinstance(version, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version
    ):
        raise ConfirmationHold("Unsupported registry version; inspect its owner.")
    parts = tuple(int(part) for part in version.split("."))
    return parts[0], parts[1], parts[2]


def helper(name: str, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["GH_REPO"] = CENTRAL_REPOSITORY
    # Each canonical helper owns its own polling budget and diagnostics. Keep
    # captured tool output private; never echo an API response on failure.
    return subprocess.run(
        ["bash", str(ROOT / name), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )


def response(result: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    if result.returncode == 1:
        raise ConfirmationFailed("Canonical publication evidence is negative.")
    if result.returncode != 0 or len(result.stdout) > MAX_HELPER_BYTES:
        raise ConfirmationHold("Registry read is unconfirmed; inspect without replay.")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ConfirmationHold("Registry response is unsupported; inspect its owner.")
    return value


def confirm_registry(
    record: FleetPublishRecord, baseline: str, run_helper: Helper = helper
) -> dict[str, Any]:
    """Require exact receipt version plus fresh canonical registry evidence."""
    if (
        record["state"] != "confirmed"
        or record["publish_outcome"] != "success"
        or record["publish_exit_code"] != 0
        or record["landed_after_error"]
        or record["moderation"] != "pass"
    ):
        raise ConfirmationHold("Native publication receipt is unconfirmed.")
    version = record["version"]
    if version is None:
        raise ConfirmationHold("Publication version is unknown; do not retry.")
    attempted = numeric(version)
    claimed_baseline = record["registry_baseline"]
    if claimed_baseline != "unpublished" and attempted <= numeric(claimed_baseline):
        raise ConfirmationHold("The attempted version did not advance its baseline.")
    if baseline != "unpublished":
        before = numeric(baseline)
        if (
            claimed_baseline == "unpublished"
            or numeric(claimed_baseline) < before
            or attempted <= before
        ):
            raise ConfirmationHold(
                "Captured baseline does not precede this publication."
            )
    workspace, plugin = record["plugin_name"].split("/")
    landing = response(
        run_helper(
            "verify-publish-landed.sh", workspace, plugin, baseline, record["run_id"]
        )
    )
    current = landing.get("current")
    if (
        landing.get("ok") is not True
        or landing.get("run_conclusion") != "success"
        or landing.get("pre") != baseline
        or numeric(current) < attempted
    ):
        raise ConfirmationHold("Registry advance is unconfirmed; do not replay.")
    exact = response(run_helper("registry-has-version.sh", workspace, plugin, version))
    if exact.get("exists") is not True or exact.get("version") != version:
        raise ConfirmationFailed("Exact publication is unavailable; do not replay.")
    moderation = response(
        run_helper("verify-moderation-cleared.sh", workspace, plugin, version)
    )
    if moderation.get("ok") is not True or moderation.get("version") != version:
        raise ConfirmationHold("Published version moderation is unconfirmed.")
    return {
        "schema_version": 1,
        "confirmed": True,
        "database_id": int(record["run_id"]),
        "run_attempt": record["run_attempt"],
        "workflow_repository": CENTRAL_REPOSITORY,
        "consumer_repository": record["consumer_repository"],
        "source_sha": record["source_sha"],
        "version": version,
        "registry_latest": current,
    }


def main(arguments: list[str]) -> int:
    if len(arguments) != 4 or not valid_run_id(arguments[3]):
        raise ConfirmationHold(
            "Pass repository, source SHA, captured baseline and run ID."
        )
    repository, source, baseline, run_id = arguments
    if baseline != "unpublished":
        numeric(baseline)
    record = resolve(GitHubClient(), repository, source, run_id=run_id)
    print(json.dumps(confirm_registry(record, baseline)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except ConfirmationFailed:
        print(
            "Central publication confirmation failed; preserve the original intent and inspect the registry without replay.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except (
        CentralPublishHold,
        ConfirmationHold,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ):
        # outer-boundary-process-contract: nonzero and empty stdout are the
        # caller's hold signal; fixed stderr prevents credential/API leakage.
        print(
            "Central publication is unconfirmed; inspect native intent, receipt and registry evidence without replay.",
            file=sys.stderr,
        )
        raise SystemExit(2)
