#!/usr/bin/env python3
"""Bounded central marker poller; dispatch one selected source per NEW run.

Polling grants no publish authority. The selected controller independently reads
enrollment, exact main tip, source tree, reviews, CI, durable history and receipts.
"""

from __future__ import annotations

import json
import os
import sys

from fleet_publish import (
    GitHub,
    PublishRefused,
    default_tip,
    enrollment,
    pending_sources,
    previous,
    require,
    tree,
)
from fleet_publish_record import CENTRAL_REPOSITORY, valid_repository


def targets(github: GitHub) -> tuple[list[dict[str, str]], list[str]]:
    repositories = github.pages("installation/repositories", "repositories")
    selected: list[dict[str, str]] = []
    blocked: list[str] = []
    for metadata in repositories:
        repo = metadata.get("full_name")
        if not valid_repository(repo) or repo == CENTRAL_REPOSITORY:
            continue
        if metadata.get("archived") or metadata.get("disabled"):
            continue
        try:
            if github.optional_marker(repo) is None:
                continue
            ref, sha = default_tip(github, repo)
            _, entries = tree(github, repo, sha)
            enrollment(github, repo, entries)
            prior = previous(github, repo, entries)
            if prior and prior["state"] == "published":
                # Moderation holds require explicit new selected recovery runs;
                # scheduled polling never repeats a publication or billing call.
                continue
            pending = pending_sources(
                github, repo, sha, prior, enrollment(github, repo, entries)
            )
            if not pending and prior and sha == prior["bookkeeping_sha"]:
                continue
            require(
                pending,
                "Unclassified main change cannot be silently treated as published.",
            )
            selected.append(
                {"repository": repo, "source_sha": pending[0], "source_ref": ref}
            )
        except PublishRefused:
            # aggregate-reporting: one held consumer must not starve independent
            # eligible ones. Return every fixed repo identity; main exits nonzero.
            blocked.append(repo)
    return selected, blocked


def dispatch(github: GitHub, items: list[dict[str, str]]) -> None:
    require(
        os.environ.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        "Only the central first-attempt schedule can dispatch poll targets.",
    )
    for item in items:
        # Own-repository GITHUB_TOKEN, never copied to a consumer. One source per
        # child run makes intent/receipt names immutable and independently bound.
        github.api(
            f"repos/{CENTRAL_REPOSITORY}/actions/workflows/fleet-publish.yml/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "repository": item["repository"],
                    "source_sha": item["source_sha"],
                },
            },
        )


def main() -> int:
    require(
        sys.argv[1:] in ([], ["--dispatch"]), "Use the fixed poll or dispatch mode."
    )
    github = GitHub()
    items, blocked = targets(github)
    if sys.argv[1:]:
        dispatch(github, items)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "poll_only",
                "selected": items,
                "blocked": blocked,
            }
        )
    )
    if blocked:
        print(
            "Some enrolled repositories remain held; inspect their original claims without replay.",
            file=sys.stderr,
        )
    return 1 if blocked else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublishRefused, OSError, ValueError, KeyError, TypeError):
        # outer-boundary-process-contract: a failed list is not empty work.
        # Emit fixed stderr/nonzero; raw API/marker bytes are not safe diagnostics.
        print(
            "Fleet marker poll failed; inspect enrollment and API availability. No absence inferred.",
            file=sys.stderr,
        )
        raise SystemExit(1)
