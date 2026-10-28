#!/usr/bin/env python3
"""Bounded central marker poller; dispatch one selected source per NEW run.

Polling grants no publish authority. The selected controller independently reads
enrollment, exact main tip, source tree, reviews, CI, durable history and receipts.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from fleet_publish import (
    GitHub,
    PublishRefused,
    PollBudgetExhausted,
    default_tip,
    dispatch_catalog_route,
    enrollment,
    pending_sources,
    previous,
    require,
    tree,
    gate_source,
    utc_time,
    native_object,
    STAMP,
)
from fleet_publish_record import (
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    FleetRecordError,
    valid_repository,
)

FAILED_DISPATCH_BACKOFF_SECONDS = 6 * 60 * 60


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def dispatch_deferred(github: GitHub, repo: str, source: str, merged_at: str) -> bool:
    """Native locator backpressure only; this grants no effect/retry authority."""
    cutoff, now = utc_time(merged_at), current_time()
    runs = github.pages(dispatch_catalog_route(merged_at), "workflow_runs")
    locator = f"fleet-publish-v1:{repo}:{source}"
    for run in runs:
        require(
            utc_time(run.get("created_at")) >= cutoff,
            "Native dispatch search is outside its source window.",
        )
        if run.get("display_title") != locator:
            continue
        require(
            run.get("path") == CENTRAL_WORKFLOW
            and run.get("event") == "workflow_dispatch"
            and native_object(run.get("head_repository")).get("full_name")
            == CENTRAL_REPOSITORY,
            "Matching dispatch has unsupported native identity.",
        )
        status = run.get("status")
        require(
            status
            in (
                "queued",
                "in_progress",
                "waiting",
                "requested",
                "pending",
                "completed",
            ),
            "Matching dispatch state is unavailable.",
        )
        if status != "completed":
            return True
        conclusion = run.get("conclusion")
        require(
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
            "Matching dispatch conclusion is unavailable.",
        )
        if (
            conclusion != "success"
            and (now - utc_time(run.get("updated_at"))).total_seconds()
            < FAILED_DISPATCH_BACKOFF_SECONDS
        ):
            return True
    return False


def scheduled_slot(github: GitHub) -> int:
    """Native created_at supplies the quarter-hour slot; child runs cannot skew it."""
    require(
        os.environ.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and os.environ.get("GITHUB_WORKFLOW_REF")
        == CENTRAL_REPOSITORY + "/" + CENTRAL_WORKFLOW + "@refs/heads/main",
        "Only the fixed central main schedule can select poll slots.",
    )
    from fleet_publish_record import valid_run_id, valid_sha

    run_id, sha = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_SHA")
    require(
        valid_run_id(run_id) and valid_sha(sha),
        "Native scheduled identity is incomplete.",
    )
    run = native_object(github.api(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}"))
    require(
        str(run.get("id")) == run_id
        and run.get("head_sha") == sha
        and run.get("path") == CENTRAL_WORKFLOW
        and run.get("event") == "schedule"
        and run.get("head_branch") == "main"
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] == 1
        and native_object(run.get("repository")).get("full_name") == CENTRAL_REPOSITORY
        and native_object(run.get("head_repository")).get("full_name")
        == CENTRAL_REPOSITORY
        and type(run.get("run_number")) is int
        and run["run_number"] > 0,
        "Native scheduled run identity is unavailable; no work or absence inferred.",
    )
    return int(utc_time(run.get("created_at")).timestamp()) // (15 * 60)


def targets(github: GitHub) -> tuple[list[dict[str, str]], list[str], list[str]]:
    with github.poll_immutable_reads():
        return _targets(github, scheduled_slot(github))


def _targets(
    github: GitHub, slot: int
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    repositories = github.pages("installation/repositories", "repositories")
    selected: list[dict[str, str]] = []
    blocked: list[str] = []
    deferred: list[str] = []
    enrolled: list[tuple[str, GitHub]] = []
    for metadata in repositories:
        repo = metadata.get("full_name")
        if not valid_repository(repo) or repo == CENTRAL_REPOSITORY:
            continue
        if metadata.get("archived") or metadata.get("disabled"):
            continue
        consumer = github.fresh_consumer()
        try:
            if consumer.optional_marker(repo) is not None:
                enrolled.append((repo, consumer))
        except (PublishRefused, FleetRecordError):
            blocked.append(repo)
    enrolled.sort(key=lambda row: row[0])
    if enrolled:
        start = slot % len(enrolled)
        enrolled = enrolled[start:] + enrolled[:start]
    deferred.extend(repo for repo, _ in enrolled[2:])
    for repo, consumer in enrolled[:2]:
        try:
            ref, sha = default_tip(consumer, repo)
            _, entries = tree(consumer, repo, sha)
            plugin = enrollment(consumer, repo, entries)
            prior = previous(consumer, repo, entries)
            if prior and prior["state"] == "published":
                # Moderation holds require explicit new selected recovery runs;
                # scheduled polling never repeats a publication or billing call.
                continue
            # Full previous() verifies the receipt and exact bookkeeping tree.
            # A recovery/late-claim tip may still contain queued sources: skip
            # only a native single-parent claim directly on its published source.
            if (
                prior
                and prior["state"] == "confirmed"
                and sha == prior["bookkeeping_sha"]
            ):
                tip = native_object(consumer.api(f"repos/{repo}/git/commits/{sha}"))
                require(tip.get("sha") == sha, "Native bookkeeping tip is incomplete.")
                parents = tip.get("parents")
                if (
                    isinstance(parents, list)
                    and len(parents) == 1
                    and native_object(parents[0]).get("sha") == prior["source_sha"]
                ):
                    continue
            stamp = entries.get(STAMP)
            known_stamp = (
                stamp.get("sha")
                if prior and prior["state"] == "confirmed" and stamp
                else None
            )
            pending = pending_sources(
                consumer, repo, sha, prior, plugin, verified_tip_stamp_sha=known_stamp
            )
            if not pending and prior and sha == prior["bookkeeping_sha"]:
                continue
            require(
                pending,
                "Unclassified main change cannot be silently treated as published.",
            )
            merged_at = gate_source(consumer, repo, pending[0], ref)
            if dispatch_deferred(consumer, repo, pending[0], merged_at):
                continue
            # Queue classification reuses only immutable stamp identity. Mutable
            # authority is fully refreshed after source gates and run catalog.
            fresh_ref, fresh_sha = default_tip(consumer, repo)
            require(
                (fresh_ref, fresh_sha) == (ref, sha),
                "Consumer advanced during poll proof; defer exact source until the next tick.",
            )
            _, fresh_entries = tree(consumer, repo, fresh_sha)
            require(
                enrollment(consumer, repo, fresh_entries) == plugin
                and fresh_entries.get(STAMP) == entries.get(STAMP),
                "Enrollment or exact native claim changed during poll; preserve its gate.",
            )
            require(
                previous(consumer, repo, fresh_entries) == prior,
                "Fresh native publication authority changed; preserve STOP without dispatch.",
            )
            selected.append(
                {"repository": repo, "source_sha": pending[0], "source_ref": ref}
            )
        except PollBudgetExhausted:
            deferred.append(repo)
        except (PublishRefused, FleetRecordError):
            # aggregate-reporting: one held consumer must not starve independent
            # eligible ones. Return every fixed repo identity; main exits nonzero.
            blocked.append(repo)
    return selected, blocked, deferred


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
    with github.poll_immutable_reads():
        items, blocked, deferred = targets(github)
        if sys.argv[1:]:
            dispatch(github, items)
        assert github._poll_cache is not None
        requests = github._poll_cache.requests
    print(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "poll_only",
                "selected": items,
                "blocked": blocked,
                "deferred": deferred,
                "central_requests": requests,
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
