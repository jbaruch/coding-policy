"""Fixed central create-only evidence writer and post-completion finalizer.

Never update/delete a ref, write central main, run consumer code or publish.
Pure record/ref parsing and all read trust belong to fleet_publish_archive.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/release"))
from fleet_publish_archive import (  # noqa: E402 - trusted co-shipped owner.
    FINALIZER_WORKFLOW,
    PREFIX,
    encode_archive,
    make_intent,
    make_hold,
    parse_hold,
    make_terminal,
    parse_intent,
    ref_name,
)
from fleet_publish_github import FleetPublishLookupError  # noqa: E402
from fleet_publish_producer import verify_workflow_protocol  # noqa: E402
from fleet_publish_record import (  # noqa: E402
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    valid_run_id,
    valid_sha,
)


class ArchiveWriteRefused(ValueError):
    """Keep every original gate; unknown writes never authorize replay."""


def require(value: object) -> None:
    if not value:
        raise ArchiveWriteRefused(
            "Preserve central evidence and inspect the original run without replaying publication."
        )


def create_only(
    github: Any,
    ref: str,
    value: object,
    lookup: Callable[[], object | None],
) -> None:
    """Existing exact data is a no-op. No ref PATCH/force/delete route exists."""
    require(
        ref.startswith(PREFIX)
        and re.fullmatch(
            r"refs/tags/fleet-publish-v1/(?:sources/[a-f0-9]{64}/[a-f0-9]{40}|runs/[1-9][0-9]{0,19}/(?:1/(?:intent|terminal)|hold))",
            ref,
        )
    )
    raw = encode_archive(value)
    existing = lookup()
    if existing is not None:
        require(encode_archive(existing) == raw)
        return
    route = f"repos/{CENTRAL_REPOSITORY}/git"
    blob = github.api(
        f"{route}/blobs",
        {"content": base64.b64encode(raw).decode(), "encoding": "base64"},
    )
    require(isinstance(blob, dict) and valid_sha(blob.get("sha")))
    tree = github.api(
        f"{route}/trees",
        {
            "tree": [
                {
                    "path": "record.json",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            ]
        },
    )
    require(isinstance(tree, dict) and valid_sha(tree.get("sha")))
    commit = github.api(
        f"{route}/commits",
        {
            "message": "Retain central publication evidence",
            "tree": tree["sha"],
            "parents": [],
            "author": {
                "name": "github-actions[bot]",
                "email": "41898282+github-actions[bot]@users.noreply.github.com",
            },
        },
    )
    require(isinstance(commit, dict) and valid_sha(commit.get("sha")))
    try:
        github.api(f"{route}/refs", {"ref": ref, "sha": commit["sha"]})
    except (ValueError, OSError):
        # The native create may have committed before a transport failure, or a
        # concurrent identical owner may have won. Exact readback is required;
        # no second POST, overwrite or inference from an error is permitted.
        require(lookup() is not None)
    final = lookup()
    require(final is not None and encode_archive(final) == raw)


def persist_claim(
    github: Any, reader: Any, intent: object, bookkeeping_sha: str
) -> None:
    """Called only after actual consumer CAS and before publication."""
    envelope = make_intent(intent, bookkeeping_sha)
    record = envelope["intent"]
    repo, source, run = (
        record["consumer_repository"],
        record["source_sha"],
        record["run_id"],
    )

    def source_lookup() -> object | None:
        return reader.source_claim(repo, source)

    if record["origin_run_id"] == run:
        create_only(
            github,
            ref_name("source", repository=repo, source_sha=source),
            envelope,
            source_lookup,
        )
    else:
        original = source_lookup()
        require(original is not None)
        original = parse_intent(original)["intent"]
        require(
            original["run_id"] == record["origin_run_id"]
            and all(
                original[key] == record[key]
                for key in (
                    "consumer_repository",
                    "source_sha",
                    "source_tree_sha",
                    "source_ref",
                    "plugin_name",
                    "version",
                    "origin_run_id",
                    "origin_run_attempt",
                )
            )
        )
    create_only(
        github, ref_name("intent", run_id=run), envelope, lambda: reader.run_intent(run)
    )
    require(reader.run_intent(run) == envelope)


def finalizer_context(environment: Any) -> dict[str, Any]:
    require(
        environment.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and environment.get("GITHUB_EVENT_NAME") == "workflow_run"
        and environment.get("GITHUB_REF") == "refs/heads/main"
        and environment.get("GITHUB_WORKFLOW_REF")
        == f"{CENTRAL_REPOSITORY}/{FINALIZER_WORKFLOW}@refs/heads/main"
        and isinstance(environment.get("GITHUB_RUN_ATTEMPT"), str)
        and re.fullmatch(r"[1-9][0-9]{0,9}", environment["GITHUB_RUN_ATTEMPT"])
        and int(environment["GITHUB_RUN_ATTEMPT"]) < 2**31
        and valid_run_id(environment.get("GITHUB_RUN_ID"))
        and valid_sha(environment.get("GITHUB_SHA"))
    )
    return {
        "workflow_path": FINALIZER_WORKFLOW,
        "workflow_sha": environment["GITHUB_SHA"],
        "run_id": environment["GITHUB_RUN_ID"],
        "run_attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
    }


def finalize(github: Any, reader: Any, environment: Any, event: object) -> bool:
    """Return false only for a native run with no durable attempted intent."""
    identity = finalizer_context(environment)
    current = github.api(
        f"repos/{CENTRAL_REPOSITORY}/actions/runs/{identity['run_id']}"
    )
    require(
        isinstance(current, dict)
        and str(current.get("id")) == identity["run_id"]
        and current.get("run_attempt") == identity["run_attempt"]
        and current.get("path") == FINALIZER_WORKFLOW
        and current.get("head_sha") == identity["workflow_sha"]
        and current.get("head_branch") == "main"
        and current.get("event") == "workflow_run"
        and current.get("repository", {}).get("full_name") == CENTRAL_REPOSITORY
        and current.get("head_repository", {}).get("full_name") == CENTRAL_REPOSITORY
    )
    verify_workflow_protocol(github, identity["workflow_sha"])
    if not isinstance(event, dict) or not isinstance(event.get("workflow_run"), dict):
        raise ArchiveWriteRefused(
            "Restore the complete native workflow completion event."
        )
    run_id = str(event["workflow_run"].get("id"))
    require(valid_run_id(run_id) and run_id != identity["run_id"])
    route = f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}"
    run = github.api(route)
    require(
        isinstance(run, dict)
        and run.get("path") == CENTRAL_WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "main"
        and run.get("status") == "completed"
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1
        and str(run.get("id")) == run_id
        and valid_sha(run.get("head_sha"))
        and run.get("repository", {}).get("full_name") == CENTRAL_REPOSITORY
        and run.get("head_repository", {}).get("full_name") == CENTRAL_REPOSITORY
    )
    verify_workflow_protocol(github, run["head_sha"])
    if run["run_attempt"] > 1:
        held = reader.run_hold(run_id)
        if held is not None:
            require(parse_hold(held)["run_id"] == run_id)
            return True  # A valid permanent HOLD is never replaced or cleared.
        hold = make_hold(
            run_id, run["run_attempt"], run["head_sha"], finalizer=identity
        )
        create_only(
            github,
            ref_name("hold", run_id=run_id),
            hold,
            lambda: reader.run_hold(run_id),
        )
        return True
    require(reader.run_hold(run_id) is None)
    attempt = github.api(route + "/attempts/1")
    envelope = reader.run_intent(run_id)
    if envelope is None:
        # No archive is constructed from a caller/source hint or missing bytes.
        # New protocol requires durable intent before effect. A missing record
        # remains unknown to publication/confirmation readers, never a receipt.
        return False
    envelope = parse_intent(envelope)
    require(envelope["intent"]["run_id"] == run_id)
    record = envelope["intent"]
    original = reader.source_claim(record["consumer_repository"], record["source_sha"])
    require(original is not None)
    original = parse_intent(original)["intent"]
    require(
        original["run_id"] == record["origin_run_id"]
        and all(
            original[key] == record[key]
            for key in (
                "consumer_repository",
                "source_sha",
                "source_tree_sha",
                "plugin_name",
                "version",
                "origin_run_id",
                "origin_run_attempt",
            )
        )
    )
    artifact_intent = github.live_record(run_id, "intent")
    require(artifact_intent is None or artifact_intent == record)
    receipt = github.live_record(run_id, "receipt")
    jobs = github.pages(route + "/attempts/1/jobs", "jobs")

    def uploaded(name: str) -> bool:
        return any(
            isinstance(job, dict)
            and job.get("name") == "publish"
            and isinstance(job.get("steps"), list)
            and any(
                isinstance(step, dict)
                and step.get("name") == name
                and step.get("status") == "completed"
                and step.get("conclusion") == "success"
                for step in job["steps"]
            )
            for job in jobs
        )

    # Native success proves upload completed. Temporary artifact visibility is
    # not an honest missing result to freeze forever; retry this read-only
    # finalizer instead of storing an irreversible null/conflicting archive.
    require(
        artifact_intent is not None
        or (
            run.get("conclusion") != "success"
            and not uploaded("Persist immutable pre-effect intent")
        )
    )
    require(
        receipt is not None
        or (
            run.get("conclusion") != "success"
            and not uploaded(
                "Persist terminal evidence even after failed/uncertain publication"
            )
        )
    )
    archive = make_terminal(envelope, receipt, run, attempt, jobs, finalizer=identity)
    existing = reader.terminal(run_id)
    if existing is not None:
        # Read-only finalizer reentry may have a different native attester run
        # attempt. Preserve the first valid attestation; all original evidence
        # still must match exactly, and its provenance was verified by reader.
        require(
            {key: value for key, value in existing.items() if key != "finalizer"}
            == {key: value for key, value in archive.items() if key != "finalizer"}
        )
        return True
    create_only(
        github,
        ref_name("terminal", run_id=run_id),
        archive,
        lambda: reader.terminal(run_id),
    )
    return True


def main() -> int:
    from fleet_publish import GitHub
    from fleet_publish_archive import ArchiveReader

    require(not sys.argv[1:])
    finalizer_context(os.environ)  # Refuse before reading the native event/API.
    with Path(os.environ["GITHUB_EVENT_PATH"]).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    # Reuse the bounded duplicate-safe decoder; event bytes never become output.
    from fleet_publish import decode

    event = decode(raw)
    github = GitHub()
    archived = finalize(github, ArchiveReader(github._record_client), os.environ, event)
    print(
        json.dumps(
            {"schema_version": 1, "archived": archived, "publication_performed": False},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, FleetPublishLookupError):
        # outer-boundary-process-contract: nonzero/no positive result is HOLD;
        # fixed stderr retains private native data rather than leaking a raw
        # traceback or turning absent evidence into publication success.
        print(
            "Central evidence archival refused. Preserve the original claim/run and repair its exact missing evidence; never replay publication.",
            file=sys.stderr,
        )
        raise SystemExit(1)
