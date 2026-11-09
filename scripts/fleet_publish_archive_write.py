"""Fixed central create-only evidence writer and post-completion finalizer.

Never update/delete a ref, write central main, run consumer code or publish.
Pure record/ref parsing and all read trust belong to fleet_publish_archive.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/release"))
from fleet_publish_archive import (  # noqa: E402 - trusted co-shipped owner.
    CONCLUSIONS,
    FINALIZER_WORKFLOW,
    PREFIX,
    encode_archive,
    make_intent,
    make_hold,
    parse_hold,
    make_terminal,
    parse_intent,
    ref_name,
    identifier,
    obj,
    utc,
)
from fleet_publish_producer import verify_workflow_protocol  # noqa: E402
from fleet_publish_record import (  # noqa: E402
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    valid_run_id,
    valid_sha,
    valid_repository,
)
from fleet_publish_github import (  # noqa: E402 - trusted bounded transport.
    ResolutionGitHubClient,
    FleetPublishLookupError,
)
from fleet_publish import GitHub  # noqa: E402 - same trusted API/pagination owner.


class ArchiveWriteRefused(ValueError):
    """Keep every original gate; unknown writes never authorize replay."""


def require(value: object) -> None:
    if not value:
        raise ArchiveWriteRefused(
            "Preserve central evidence and inspect the original run without replaying publication."
        )


def owned_route(route: str, repository: str) -> bool:
    pathname = route.split("?", 1)[0]
    root = f"repos/{repository}"
    return pathname == root or pathname.startswith(root + "/")


def credential_environment(name: str) -> dict[str, str]:
    """Only the two existing workflow bindings; no gh fallback credential."""
    require(name in ("GH_TOKEN", "FLEET_CENTRAL_TOKEN"))
    credential = os.environ.get(name)
    require(isinstance(credential, str) and bool(credential))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GH_TOKEN", "GITHUB_TOKEN", "FLEET_CENTRAL_TOKEN")
    }
    environment["GH_TOKEN"] = str(credential)
    return environment


class CentralHistoricalClient(ResolutionGitHubClient):
    """The existing bounded proof-resolution profile, with central-only access."""

    def _command_environment(self) -> dict[str, str]:
        return credential_environment("FLEET_CENTRAL_TOKEN")

    def _read(self, route: str, maximum: int, *, native_status: bool = False) -> bytes:
        require(owned_route(route, CENTRAL_REPOSITORY))
        return super()._read(route, maximum, native_status=native_status)


class ConsumerHistoricalClient(ResolutionGitHubClient):
    def __init__(self, repository: str) -> None:
        require(valid_repository(repository) and repository != CENTRAL_REPOSITORY)
        super().__init__()
        self.repository = repository

    def _command_environment(self) -> dict[str, str]:
        return credential_environment("GH_TOKEN")

    def _read(self, route: str, maximum: int, *, native_status: bool = False) -> bytes:
        require(owned_route(route, self.repository))
        return super()._read(route, maximum, native_status=native_status)


class HistoricalGitHub(GitHub):
    """Reuse the publisher's Git/package owner with fixed historical credentials."""

    def __init__(self) -> None:
        super().__init__()
        self.consumer_repository: str | None = None

    def bind_consumer(self, repository: str) -> None:
        require(valid_repository(repository) and repository != CENTRAL_REPOSITORY)
        require(self.consumer_repository in (None, repository))
        self.consumer_repository = repository

    def _command_environment(
        self, path: str, payload: Any, method: str
    ) -> dict[str, str]:
        if owned_route(path, CENTRAL_REPOSITORY):
            if payload is not None:
                require(
                    method == "POST"
                    and path
                    in {
                        f"repos/{CENTRAL_REPOSITORY}/git/{kind}"
                        for kind in ("blobs", "trees", "commits", "refs")
                    }
                )
            return credential_environment("FLEET_CENTRAL_TOKEN")
        require(
            self.consumer_repository is not None
            and owned_route(path, self.consumer_repository)
            and payload is None
        )
        return credential_environment("GH_TOKEN")

    @contextmanager
    def _archive_client(self) -> Iterator[Any]:
        # Each operation retains the existing reader's bounds without changing
        # the process environment or exposing the central token to consumer gh.
        yield CentralHistoricalClient()


class HistoricalEvidenceTransport:
    """Exact selected consumer GETs and central evidence use separate clients."""

    def __init__(self, central: Any, consumer: Any, repository: str) -> None:
        require(valid_repository(repository) and repository != CENTRAL_REPOSITORY)
        self.central, self.consumer, self.repository = central, consumer, repository

    def selected(self, route: str) -> Any:
        if owned_route(route, CENTRAL_REPOSITORY):
            return self.central
        require(owned_route(route, self.repository))
        return self.consumer

    def json(self, route: str) -> object:
        return self.selected(route).json(route)

    def native_json(self, route: str) -> object:
        return self.selected(route).native_json(route)

    def artifact_evidence(self, run_id: str, kind: Any) -> Any:
        return self.central.artifact_evidence(run_id, kind)


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
            r"refs/tags/fleet-publish-v1/(?:sources/[a-f0-9]{64}/[a-f0-9]{40}|runs/[1-9][0-9]{0,19}/(?:1/(?:intent|terminal|historical-source-proof)|hold))",
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


def finalizer_context(
    environment: Any, *, event_name: str = "workflow_run"
) -> dict[str, Any]:
    require(
        event_name in ("workflow_run", "workflow_dispatch")
        and environment.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and environment.get("GITHUB_EVENT_NAME") == event_name
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


def historical_selection(
    github: Any, reader: Any, environment: Any, event: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive the selected App repository solely from original central records."""
    require(environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch")
    identity = finalizer_context(environment, event_name="workflow_dispatch")
    require(identity["run_attempt"] == 1)
    current_route = f"repos/{CENTRAL_REPOSITORY}/actions/runs/{identity['run_id']}"
    for route in (current_route, current_route + "/attempts/1"):
        actual = github.api(route)
        require(
            isinstance(actual, dict)
            and str(actual.get("id")) == identity["run_id"]
            and actual.get("run_attempt") == 1
            and actual.get("path") == FINALIZER_WORKFLOW
            and actual.get("head_sha") == identity["workflow_sha"]
            and actual.get("head_branch") == "main"
            and actual.get("event") == "workflow_dispatch"
            and actual.get("repository", {}).get("full_name") == CENTRAL_REPOSITORY
            and actual.get("head_repository", {}).get("full_name") == CENTRAL_REPOSITORY
        )
    verify_workflow_protocol(github, identity["workflow_sha"])
    if not isinstance(event, dict) or not isinstance(event.get("inputs"), dict):
        raise ArchiveWriteRefused("Pass only the original native publisher run ID.")
    inputs = event["inputs"]
    require(
        set(inputs) == {"original_run_id"} and valid_run_id(inputs["original_run_id"])
    )
    run_id = inputs["original_run_id"]
    require(run_id != identity["run_id"] and reader.run_hold(run_id) is None)
    envelope = reader.run_intent(run_id)
    require(envelope is not None)
    envelope = parse_intent(envelope)
    original = envelope["intent"]
    require(original["run_id"] == original["origin_run_id"] == run_id)
    require(
        reader.source_claim(original["consumer_repository"], original["source_sha"])
        == envelope
    )

    require(
        valid_repository(original["consumer_repository"])
        and original["consumer_repository"] != CENTRAL_REPOSITORY
    )
    return identity, envelope


def historical_quiescence(
    github: Any, identity: dict[str, Any], expected_run: dict[str, Any]
) -> dict[str, Any]:
    """Stable native count/active-set observations, not an atomic API snapshot.

    The owner keeps mutation admission disabled and does not delete run metadata
    through readback. Completed history never drives pagination; the native
    total/completed complement must account for every observed active identity.
    """
    from fleet_publish_archive import native_run

    statuses = ("queued", "in_progress", "waiting", "requested", "pending")
    snapshot: list[dict[str, Any]] = []
    workflows: set[str] = set()
    seen: set[str] = set()
    attesters: list[dict[str, Any]] = []
    observations: dict[str, dict[str, Any]] = {}
    finalizer_id = ""

    def quiet(value: object) -> None:
        if not value:
            raise ArchiveWriteRefused(
                "Keep FLEET_PUBLISHER_ENABLED disabled and preserve run metadata; "
                "wait for every other central poll/publication/finalizer run to "
                "finish and restore complete stable native totals and active "
                "catalogs before attesting."
            )

    def observed(value: object, path: str, workflow_id: str) -> dict[str, Any]:
        run = obj(value)
        quiet(type(run.get("id")) is int)
        run_id = identifier(run.get("id"))
        attempt, status, conclusion = (
            run.get("run_attempt"),
            run.get("status"),
            run.get("conclusion"),
        )
        branch = run.get("head_branch")
        events = (
            {"schedule", "workflow_dispatch"}
            if path == CENTRAL_WORKFLOW
            else {"workflow_run", "workflow_dispatch"}
        )
        quiet(
            type(run.get("workflow_id")) is int
            and str(run["workflow_id"]) == workflow_id
            and run.get("path") == path
            and obj(run.get("repository")).get("full_name") == CENTRAL_REPOSITORY
            and obj(run.get("head_repository")).get("full_name") == CENTRAL_REPOSITORY
            and type(attempt) is int
            and 1 <= attempt < 2**31
            and valid_sha(run.get("head_sha"))
            and isinstance(branch, str)
            and 0 < len(branch) <= 255
            and not any(ord(char) < 32 or ord(char) == 127 for char in branch)
            and isinstance(run.get("event"), str)
            and run["event"] in events
            and isinstance(status, str)
            and status in (*statuses, "completed")
            and (
                isinstance(conclusion, str) and conclusion in CONCLUSIONS
                if status == "completed"
                else conclusion is None
            )
        )
        result = {
            "workflow_id": workflow_id,
            "path": path,
            "id": run_id,
            "run_attempt": attempt,
            "head_sha": run["head_sha"],
            "head_branch": branch,
            "event": run["event"],
            "status": status,
            "conclusion": conclusion,
            "created_at": utc(run.get("created_at")),
        }
        previous = observations.get(run_id)
        quiet(previous is None or previous == result)
        observations[run_id] = result
        return result

    for path in (CENTRAL_WORKFLOW, FINALIZER_WORKFLOW):
        route = f"repos/{CENTRAL_REPOSITORY}/actions/workflows"
        workflow = obj(github.api(f"{route}/{path.rsplit('/', 1)[1]}"))
        quiet(type(workflow.get("id")) is int)
        workflow_id = identifier(workflow.get("id"))
        quiet(
            workflow_id not in workflows
            and workflow.get("path") == path
            and workflow.get("state") == "active"
        )
        workflows.add(workflow_id)
        if path == FINALIZER_WORKFLOW:
            finalizer_id = workflow_id
        runs = f"{route}/{workflow_id}/runs"

        def summary(*, completed: bool = False) -> dict[str, Any]:
            query = "status=completed&" if completed else ""
            raw = obj(github.api(f"{runs}?{query}per_page=1&page=1"))
            total, rows = raw.get("total_count"), raw.get("workflow_runs")
            if type(total) is not int or total < 0:
                raise ArchiveWriteRefused(
                    "Keep FLEET_PUBLISHER_ENABLED disabled; restore a nonnegative "
                    "native workflow total before attesting."
                )
            quiet(isinstance(rows, list) and len(rows) == min(1, total))
            sample = observed(rows[0], path, workflow_id) if rows else None
            quiet(not completed or sample is None or sample["status"] == "completed")
            return {"total": total, "sample": sample}

        before, completed_before = summary(), summary(completed=True)
        active = []
        for status in statuses:
            for value in github.pages(f"{runs}?status={status}", "workflow_runs"):
                run = observed(value, path, workflow_id)
                quiet(run["status"] == status and run["id"] not in seen)
                seen.add(run["id"])
                quiet(
                    path == FINALIZER_WORKFLOW
                    and run["id"] == identity["run_id"]
                    and run["run_attempt"] == identity["run_attempt"]
                    and run["head_sha"] == identity["workflow_sha"]
                    and run["head_branch"] == "main"
                    and run["event"] == "workflow_dispatch"
                    and run["status"] == "in_progress"
                )
                active.append(run)
                attesters.append(run)
        completed_after, after = summary(completed=True), summary()
        quiet(before == after and completed_before == completed_after)
        quiet(before["total"] == completed_before["total"] + len(active))
        quiet(path != CENTRAL_WORKFLOW or before["total"] > 0)
        snapshot.append(
            {
                "workflow_id": workflow_id,
                "path": path,
                "all": before,
                "completed": completed_before,
                "active": active,
            }
        )

    quiet(len(attesters) == 1)
    current = observed(
        github.api(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{identity['run_id']}"),
        FINALIZER_WORKFLOW,
        finalizer_id,
    )
    quiet(current == attesters[0])
    original = native_run(
        github.api(f"repos/{CENTRAL_REPOSITORY}/actions/runs/{expected_run['id']}")
    )
    quiet(original == expected_run)
    return {"workflows": snapshot, "original": original, "attester": current}


def attest_historical(
    github: Any, reader: Any, environment: Any, event: object
) -> bool:
    """Explicit later attestation, derived entirely from the existing owner."""
    from fleet_publish_archive import native_run, native_jobs
    from fleet_publish_historical import KIND, DERIVATION, parse_historical
    from fleet_publish_historical_package import observe_package
    from fleet_publish_producer import historical_pinset
    from fleet_publish_resolver import _Evidence

    identity, envelope = historical_selection(github, reader, environment, event)
    original = envelope["intent"]
    run_id = original["run_id"]
    github.bind_consumer(original["consumer_repository"])
    transport = HistoricalEvidenceTransport(
        reader.client,
        ConsumerHistoricalClient(original["consumer_repository"]),
        original["consumer_repository"],
    )
    # Reuse complete native source, merge, ancestry and exact bookkeeping proof.
    # The existing receipt-only resolver remains unchanged for old callers.
    evidence = _Evidence(
        transport, original["consumer_repository"], original["source_sha"]
    )
    selected, claim = evidence.selected(run_id)
    require(selected == original and claim == envelope["bookkeeping_sha"])
    route = f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}"
    run = native_run(github.api(route))
    attempt = native_run(github.api(route + "/attempts/1"))
    jobs = native_jobs(github.pages(route + "/attempts/1/jobs", "jobs"), run)
    pins = historical_pinset(github, original["workflow_sha"])
    # Existing native receipt access supplies contradictions, never synthesized
    # terminal bytes. Refuse any negative/uncertain original outcome.
    receipt = reader.record(run_id, "receipt")
    terminal = reader.terminal(run_id)
    if terminal is not None:
        require(
            receipt is not None
            and terminal["native"] == {"run": run, "attempt": attempt, "jobs": jobs}
        )
    if receipt is not None:
        require(
            all(
                receipt[key] == original[key]
                for key in original
                if key
                not in {
                    "state",
                    "publish_outcome",
                    "publish_exit_code",
                    "landed_after_error",
                    "moderation",
                    "bookkeeping_sha",
                }
            )
        )
        require(
            receipt["state"] == "confirmed"
            and receipt["moderation"] == "pass"
            and receipt["publish_outcome"] == "success"
            and receipt["publish_exit_code"] == 0
            and not receipt["landed_after_error"]
            and receipt["bookkeeping_sha"] == claim
        )
    proof = parse_historical(
        {
            "schema_version": 1,
            "kind": KIND,
            "derivation": DERIVATION,
            "intent": envelope,
            "native": {"run": run, "attempt": attempt, "jobs": jobs},
            "producer_pinset": pins,
            "registry": observe_package(github, envelope),
            "attester": identity,
        }
    )
    evidence.stable()
    require(reader.run_intent(run_id) == envelope and reader.run_hold(run_id) is None)
    quiescence = historical_quiescence(github, identity, run)
    existing = reader.historical_source_proof(run_id)
    if existing is not None:
        # Retain first attester/observation. All original/source/native facts and
        # exact package content must still agree after fresh verification.
        require(
            all(
                existing[key] == proof[key]
                for key in proof
                if key not in ("attester", "registry")
            )
        )
        require(
            all(
                existing["registry"][key] == proof["registry"][key]
                for key in proof["registry"]
                if key != "observed_at"
            )
        )
        require(historical_quiescence(github, identity, run) == quiescence)
        return True
    create_only(
        github,
        ref_name("historical-source-proof", run_id=run_id),
        proof,
        lambda: reader.historical_source_proof(run_id),
    )
    require(historical_quiescence(github, identity, run) == quiescence)
    return True


def main() -> int:
    from fleet_publish import GitHub
    from fleet_publish_archive import ArchiveReader

    require(sys.argv[1:] in ([], ["select-consumer"]))
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        finalizer_context(os.environ)  # Refuse before reading native event/API.
    with Path(os.environ["GITHUB_EVENT_PATH"]).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    # Reuse the bounded duplicate-safe decoder; event bytes never become output.
    from fleet_publish import decode

    event = decode(raw)
    historical = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    github = HistoricalGitHub() if historical else GitHub()
    operation = attest_historical if historical else finalize
    reader = ArchiveReader(
        CentralHistoricalClient() if historical else github._record_client
    )
    if sys.argv[1:] == ["select-consumer"]:
        require(historical)
        _, envelope = historical_selection(github, reader, os.environ, event)
        name = envelope["intent"]["consumer_repository"].split("/", 1)[1]
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write("repository_name=" + name + "\n")
        print(json.dumps({"schema_version": 1, "repository_name": name}))
        return 0
    archived = operation(github, reader, os.environ, event)
    result: dict[str, Any] = {
        "schema_version": 1,
        "archived": archived,
        "publication_performed": False,
    }
    if historical:
        if not isinstance(event, dict):
            raise ArchiveWriteRefused("Restore the exact original run selection.")
        run_id = event["inputs"]["original_run_id"]
        saved = reader.historical_source_proof(run_id)
        require(saved is not None)
        if saved is None:
            raise ArchiveWriteRefused("Restore exact immutable proof readback.")
        result.update(
            schema_version=2,
            proof_kind="historical_source_proof",
            original_run_id=run_id,
            attester=saved["attester"],
        )
    print(json.dumps(result, separators=(",", ":")))
    return 0


def refusal_category(error: Exception) -> str:
    """Fixed process-boundary metadata, never raw provider text or kinds."""
    if isinstance(error, FleetPublishLookupError):
        categories = {
            "command_budget": "lookup_budget",
            "command_timeout": "lookup_timeout",
            "command_retirement_failed": "lookup_cleanup",
            "command_unavailable": "lookup_unavailable",
            "command_failed": "lookup_failed",
            "command_io_failed": "lookup_io",
            "stdout_budget": "lookup_output",
            "stderr_budget": "lookup_output",
            "native_http_error": "lookup_http",
            "invalid_http_response": "lookup_http",
            "native_missing": "lookup_missing",
            "native_expired": "lookup_expired",
        }
        if isinstance(error.kind, str):
            return categories.get(error.kind, "lookup_refused")
        return "lookup_refused"
    return "archive_guard" if isinstance(error, ArchiveWriteRefused) else "unexpected"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    # outer-boundary-process-contract: callers treat nonzero/no positive JSON
    # as HOLD; fixed stderr preserves that signal without raw API/tool data.
    # Propagation would leak private child output and disable this boundary.
    except Exception as error:  # noqa: BLE001 - outer-boundary-process-contract
        print(
            f"Central evidence archival refused [{refusal_category(error)}]. Preserve the original claim/run and repair its exact missing evidence; never replay publication.",
            file=sys.stderr,
        )
        raise SystemExit(1)
