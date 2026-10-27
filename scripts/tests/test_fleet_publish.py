#!/usr/bin/env python3
"""Composed fake-native publication transport; never calls registry or GitHub."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import zipfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

# Standalone runner discovers this file outside a package; load trusted scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fleet_publish as publisher  # noqa: E402
import fleet_publish_targets as poller  # noqa: E402
from fleet_publish_validate import validate  # noqa: E402

REPO = "jbaruch/nanoclaw-fixture"
SOURCE, TREE, HEAD, WORKFLOW, CLAIM = (character * 40 for character in "abcde")
ENV = {
    "GITHUB_REPOSITORY": "jbaruch/coding-policy",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": WORKFLOW,
    "GITHUB_RUN_ID": "101",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish.yml@refs/heads/main",
    "FLEET_PUBLISH_DATE": "2026-10-27",
    "GH_TOKEN": "fake-app",
    "FLEET_CENTRAL_TOKEN": "fake-central",
}


class NativeGitHub(publisher.GitHub):
    def __init__(self) -> None:
        self.tip = SOURCE
        self.calls: list[tuple[str, object]] = []
        self.blobs: dict[str, bytes] = {}
        self.entries: dict[str, dict[str, object]] = {}
        self.files = {
            publisher.MARKER: publisher.encoded(
                {"schema_version": 1, "plugin_name": "jbaruch/nanoclaw-fixture"}
            ),
            publisher.MANIFEST: publisher.encoded(
                {
                    "name": "jbaruch/nanoclaw-fixture",
                    "version": "0.1.0",
                    "description": "Synthetic central packaging fixture.",
                    "skills": ["skills/fixture"],
                    "rules": ["rules/fixture.md"],
                }
            ),
            "README.md": b"# Fixture\n",
            ".tesslignore": b".github/\n**/tests/\nskills/fixture/dormant/\n",
            "CHANGELOG.md": b"# Changelog\n\n### Added\n\n- Fixture.\n",
            "skills/fixture/SKILL.md": b"# Fixture skill\n",
            "rules/fixture.md": b"# Fixture rule\n",
            "skills/fixture/scripts/data.py": b"raise RuntimeError('NEVER EXECUTE CONSUMER CODE')\n",
            "tessl.json": b'{"mcpServers":{"malicious":{}}}',
        }
        for path, raw in self.files.items():
            self.put(path, raw)
        self.entries["skills/fixture"] = {
            "path": "skills/fixture",
            "type": "tree",
            "mode": "040000",
            "sha": "f" * 40,
        }
        self.native_reviews = [
            {
                "user": {"login": "coding-policy-fleet-reviewer[bot]"},
                "commit_id": HEAD,
                "state": "APPROVED",
                "submitted_at": "2026-10-27T01:00:00Z",
                "body": "Policy loaded.",
            },
            {
                "user": {"login": "jbaruch-claude-reviewer[bot]"},
                "commit_id": HEAD,
                "state": "APPROVED",
                "submitted_at": "2026-10-27T01:01:00Z",
                "body": "<!-- coding-policy:claude-quality-review:v1 -->\nApproved.",
            },
        ]
        self.checks: list[dict[str, Any]] = [
            {"status": "completed", "conclusion": "success"}
        ]
        self.status = {"state": "pending", "statuses": []}
        self.history: list[object] = []
        self.records: dict[str, bytes] = {}
        self.run_conclusion = "success"
        self.run_status = "completed"
        self.current_attempt = 1
        self.dispatch_sources: dict[str, str] = {"101": SOURCE}
        self.other_runs: dict[str, dict[str, Any]] = {}
        self.other_records: dict[str, dict[str, bytes]] = {}
        self.native_jobs: dict[str, list[dict[str, Any]]] = {}
        self.source_merged_at = "2026-10-27T01:02:00Z"
        self.current_pull_source = SOURCE
        self.required_branch: dict[str, Any] = {
            "name": "main",
            "protected": False,
            "commit": {"sha": SOURCE},
            "protection": {
                "enabled": False,
                "required_status_checks": {
                    "contexts": [],
                    "checks": [],
                    "enforcement_level": "off",
                },
            },
        }
        self.rules: list[dict[str, Any]] = []
        self.refuse_push = False
        self.reset_before_graphql: str | None = None
        self.created_tree: list[dict[str, str]] = []
        self.metadata = {
            "full_name": REPO,
            "node_id": "R_fixture_selected_repository",
            "archived": False,
            "disabled": False,
            "default_branch": "main",
        }

    def put(self, path: str, raw: bytes) -> None:
        sha = hashlib.sha1(raw).hexdigest()
        self.blobs[sha] = raw
        self.entries[path] = {
            "path": path,
            "type": "blob",
            "mode": "100644",
            "sha": sha,
        }

    def native_run(self, run_id: str) -> dict[str, Any]:
        value = {
            "id": int(run_id),
            "workflow_id": 71,
            "run_attempt": self.current_attempt,
            "head_sha": WORKFLOW,
            "head_branch": "main",
            "path": publisher.CENTRAL_WORKFLOW,
            "event": "workflow_dispatch",
            "head_repository": {"full_name": publisher.CENTRAL_REPOSITORY},
            "status": self.run_status,
            "conclusion": self.run_conclusion,
            "created_at": "2026-10-27T01:03:00Z",
            "updated_at": "2026-10-27T01:03:00Z",
            "display_title": f"fleet-publish-v1:{REPO}:{self.dispatch_sources.get(run_id, SOURCE)}",
        }
        value.update(self.other_runs.get(run_id, {}))
        return value

    def fresh_consumer(self) -> publisher.GitHub:
        return self  # One synthetic consumer; fleet isolation has its own fixture.

    def api(
        self,
        path: str,
        payload: object = None,
        *,
        binary: bool = False,
        method: str = "POST",
    ) -> object:
        self.calls.append((path, payload))
        if path == "graphql" and isinstance(payload, dict):
            update = payload["variables"]["input"]
            ref = update["refUpdates"][0]
            assert update["repositoryId"] == self.metadata["node_id"]
            assert ref["name"] == "refs/heads/main" and ref["force"] is False
            if self.reset_before_graphql is not None:
                self.tip = self.reset_before_graphql
                self.reset_before_graphql = None
            if self.refuse_push or self.tip != ref["beforeOid"]:
                return {
                    "errors": [{"message": "Expected OID differs"}],
                    "data": {"updateRefs": None},
                }
            for item in self.created_tree:
                self.entries[item["path"]] = dict(item)
            self.tip = ref["afterOid"]
            self.history = [{"sha": self.tip}]
            return {
                "data": {"updateRefs": {"clientMutationId": update["clientMutationId"]}}
            }
        if path == f"repos/{REPO}":
            return deepcopy(self.metadata)
        if path == f"repos/{REPO}/pulls/7":
            return {
                "number": 7,
                "merged": True,
                "state": "closed",
                "merge_commit_sha": self.current_pull_source,
                "merged_at": self.source_merged_at,
                "head": {"sha": HEAD, "repo": {"full_name": REPO}},
                "base": {"ref": "main", "repo": {"full_name": REPO}},
            }
        if path == f"repos/{REPO}/branches/main":
            return deepcopy(self.required_branch)
        if path == f"repos/{REPO}/git/ref/heads/main":
            return {"object": {"sha": self.tip, "type": "commit"}}
        if path.startswith(f"repos/{REPO}/git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return {
                "sha": sha,
                "tree": {"sha": TREE},
                "parents": [{"sha": SOURCE}] if sha == CLAIM else [],
            }
        if path == f"repos/{REPO}/git/trees/{TREE}?recursive=1":
            return {
                "sha": TREE,
                "truncated": False,
                "tree": deepcopy(list(self.entries.values())),
            }
        if path.startswith(f"repos/{REPO}/git/blobs/"):
            raw = self.blobs[path.rsplit("/", 1)[1]]
            return {
                "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
                "size": len(raw),
            }
        if path == f"repos/{REPO}/commits/{HEAD}/status":
            return self.status
        if path == f"repos/{REPO}/git/blobs" and isinstance(payload, dict):
            raw = base64.b64decode(payload["content"])
            sha = hashlib.sha1(raw).hexdigest()
            self.blobs[sha] = raw
            return {"sha": sha}
        if path == f"repos/{REPO}/git/trees" and isinstance(payload, dict):
            self.created_tree = payload["tree"]
            return {"sha": TREE}
        if path == f"repos/{REPO}/git/commits" and isinstance(payload, dict):
            return {"sha": CLAIM}
        if path == f"repos/{REPO}/git/refs/heads/main" and isinstance(payload, dict):
            publisher.require(not self.refuse_push, "Protected ref refused.")
            assert method == "PATCH" and payload["force"] is False
            for item in self.created_tree:
                self.entries[item["path"]] = dict(item)
            self.tip = payload["sha"]
            self.history = [{"sha": CLAIM}]
            return {"object": {"sha": CLAIM}}
        if path.startswith(f"repos/{REPO}/compare/"):
            return {"status": "identical" if self.tip == CLAIM else "ahead"}
        central = "repos/jbaruch/coding-policy/actions/"
        if path == central + "workflows/fleet-publish.yml":
            return {"id": 71, "path": publisher.CENTRAL_WORKFLOW}
        match = re.fullmatch(central + r"runs/(\d+)(?:/attempts/\d+)?", path)
        if match:
            return self.native_run(match[1])
        if path.startswith(central + "artifacts/"):
            basename = path.split("/")[-2]
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(basename + ".json", self.records[basename])
            return stream.getvalue()
        raise AssertionError(f"Unexpected fake-native API {path}")

    def pages(self, route: str, key: str | None = None) -> list[Any]:
        path = route
        if path.startswith(
            "repos/jbaruch/coding-policy/actions/workflows/fleet-publish.yml/runs?event=workflow_dispatch"
        ):
            ids = (
                set(self.dispatch_sources)
                | set(self.other_runs)
                | {getattr(self, "current_run", "101")}
            )
            runs = [self.native_run(run_id) for run_id in sorted(ids)]
            created = parse_qs(urlsplit(path).query).get("created")
            if created:
                cutoff = publisher.utc_time(created[0].removeprefix(">="))
                runs = [
                    run
                    for run in runs
                    if publisher.utc_time(run["created_at"]) >= cutoff
                ]
            return runs
        match = re.fullmatch(
            r"repos/jbaruch/coding-policy/actions/runs/(\d+)/attempts/\d+/jobs", path
        )
        if match:
            return self.native_jobs.get(match[1], [])
        if path == f"repos/{REPO}/commits/{SOURCE}/pulls":
            self.current_pull_source = SOURCE
            return [
                {
                    "merge_commit_sha": SOURCE,
                    "merged_at": "2026-10-27T01:02:00Z",
                    "number": 7,
                    "base": {"ref": "main"},
                    "head": {"sha": HEAD, "repo": {"full_name": REPO}},
                }
            ]
        if path == f"repos/{REPO}/pulls/7/reviews":
            return self.native_reviews
        if path == f"repos/{REPO}/commits/{HEAD}/check-runs":
            return self.checks
        if path == f"repos/{REPO}/commits/{HEAD}/status":
            return self.status["statuses"]
        if path == f"repos/{REPO}/rules/branches/main":
            return self.rules
        if path.startswith(f"repos/{REPO}/commits?path="):
            if "fleet-publish-enabled" in path:
                return [{"sha": SOURCE}]
            return self.history
        if path == "repos/jbaruch/coding-policy/actions/runs/101/artifacts":
            return [
                {"name": "fleet-publish-" + name + "-v1", "id": name, "expired": False}
                for name in self.records
            ]
        if path == "installation/repositories":
            return [self.metadata]
        raise AssertionError(f"Unexpected fake-native pages {path}")

    def optional_marker(self, repo: str) -> object:
        return {"sha": SOURCE}

    def record(self, run_id: str, kind: str) -> publisher.FleetPublishRecord | None:
        raw = (
            self.records if run_id == "101" else self.other_records.get(run_id, {})
        ).get(kind)
        return publisher.decode_record(raw) if raw else None


class PublisherFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.github = NativeGitHub()
        self.helpers: list[str] = []
        self.smart_exit = 0
        self.native_publish_exit = 0
        self.landed_error = False
        self.moderation = "pass"
        self.baseline = "0.1.0"
        self.helper_patch = patch.object(publisher, "helper", side_effect=self.helper)
        self.helper_patch.start()
        self.addCleanup(self.helper_patch.stop)
        self.cli_patch = patch.object(
            publisher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        )
        self.cli = self.cli_patch.start()
        self.addCleanup(self.cli_patch.stop)
        self.protocol_patch = patch.object(
            publisher, "verify_workflow_protocol", return_value=None
        )
        self.protocol = self.protocol_patch.start()
        self.addCleanup(self.protocol_patch.stop)

    def helper(
        self, name: str, *args: str, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        self.helpers.append(name)
        rc = 0
        data: object
        if name == "registry-baseline.sh":
            return subprocess.CompletedProcess([], 0, (self.baseline + "\n").encode())
        if name == "smart-publish.sh":
            self.assertEqual(args[0], "as-is")
            self.assertEqual(args[-1], "true")
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            rc = self.smart_exit
            data = {
                "outcome": "success" if not rc else "failure",
                "publish_exit_code": self.native_publish_exit,
                "landed_after_error": self.landed_error,
                "version": json.loads(
                    (Path(args[1]) / publisher.MANIFEST).read_bytes()
                )["version"],
            }
        elif name == "registry-has-version.sh":
            data = {"exists": True}
        elif name == "verify-moderation-cleared.sh":
            rc = 0 if self.moderation == "pass" else 1
            data = {"ok": not rc, "moderation_status": self.moderation}
        else:
            raise AssertionError(name)
        return subprocess.CompletedProcess([], rc, publisher.encoded(data))

    def prepare(self) -> None:
        publisher.prepare(self.github, REPO, SOURCE, self.work, ENV)
        self.github.records["intent"] = (self.work / "intent.json").read_bytes()

    def claimed(self) -> None:
        self.prepare()
        publisher.claim(self.github, self.work)

    def receipt(self) -> dict[str, object]:
        return json.loads((self.work / "receipt.json").read_bytes())


class PublisherTests(PublisherFixture):
    def test_composed_claim_once_publish_and_confirm(self) -> None:
        self.claimed()
        self.assertTrue(publisher.execute(self.github, self.work))
        receipt = self.receipt()
        self.assertEqual(receipt["state"], "confirmed")
        self.assertEqual(receipt["bookkeeping_sha"], CLAIM)
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)
        stamp = publisher.file(self.github, REPO, self.github.entries, publisher.STAMP)
        self.assertEqual(stamp, self.github.records["intent"])
        self.assertEqual(json.loads(stamp)["state"], "claimed")
        self.assertNotIn(
            "tessl.json",
            [
                p.relative_to(self.work / "package").as_posix()
                for p in (self.work / "package").rglob("*")
                if p.is_file()
            ],
        )
        self.assertTrue((self.work / "package/skills/fixture/scripts/data.py").exists())
        self.assertEqual(
            len(self.cli.call_args_list), 2
        )  # lint + trusted reviewer only
        self.assertNotIn("GH_TOKEN", self.cli.call_args_list[0].kwargs["env"])
        self.assertNotIn(
            "FLEET_CENTRAL_TOKEN", self.cli.call_args_list[1].kwargs["env"]
        )

    def test_failed_intent_upload_retains_durable_stop_and_never_publishes(
        self,
    ) -> None:
        self.claimed()
        self.github.records.clear()
        with self.assertRaises(publisher.PublishRefused):
            publisher.execute(self.github, self.work)
        self.assertIn(publisher.STAMP, self.github.entries)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_atomic_claim_push_failure_never_publishes(self) -> None:
        self.prepare()
        self.github.refuse_push = True
        with self.assertRaises(publisher.PublishRefused):
            publisher.claim(self.github, self.work)
        self.assertNotIn("smart-publish.sh", self.helpers)
        self.assertFalse((self.work / "claim.json").exists())

    def test_divergent_main_after_claim_never_publishes(self) -> None:
        self.claimed()
        self.github.tip = "f" * 40
        original = self.github.api
        with patch.object(
            self.github,
            "api",
            side_effect=lambda route, *args, **kwargs: {"status": "diverged"}
            if "/compare/" in route
            else original(route, *args, **kwargs),
        ):
            with self.assertRaises(publisher.PublishRefused):
                publisher.execute(self.github, self.work)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_changed_claim_never_publishes(self) -> None:
        self.claimed()
        self.github.put(publisher.STAMP, b"{}")
        with self.assertRaises(ValueError):
            publisher.execute(self.github, self.work)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_failed_publish_landed_after_error_never_confirms(self) -> None:
        self.claimed()
        self.native_publish_exit = 7
        self.landed_error = True
        self.assertFalse(publisher.execute(self.github, self.work))
        self.assertEqual(self.receipt()["state"], "uncertain")
        self.assertEqual(self.receipt()["publish_exit_code"], 7)
        self.assertNotIn("verify-moderation-cleared.sh", self.helpers)

    def test_moderation_hold_retains_known_publication(self) -> None:
        self.claimed()
        self.moderation = "pending"
        self.assertFalse(publisher.execute(self.github, self.work))
        self.assertEqual(self.receipt()["state"], "published")
        self.assertEqual(self.receipt()["moderation"], "pending")

    def test_publish_exception_leaves_conservative_uncertain_receipt(self) -> None:
        self.claimed()
        with patch.object(
            publisher, "helper", side_effect=OSError("synthetic transport")
        ):
            with self.assertRaises(OSError):
                publisher.execute(self.github, self.work)
        self.assertEqual(self.receipt()["state"], "uncertain")
        self.assertEqual(self.receipt()["publish_outcome"], "unknown")

    def test_confirmed_bookkeeping_noop_uses_actual_artifacts_and_attempt(self) -> None:
        self.claimed()
        self.assertTrue(publisher.execute(self.github, self.work))
        self.github.records["receipt"] = (self.work / "receipt.json").read_bytes()
        fresh = self.work / "fresh"
        fresh.mkdir()
        publisher.prepare(self.github, REPO, CLAIM, fresh, ENV)
        self.assertEqual(
            json.loads((fresh / "selection.json").read_bytes())["mode"],
            "already_confirmed",
        )
        self.assertFalse((fresh / "intent.json").exists())

    def test_confirmed_record_with_failed_actual_attempt_is_held(self) -> None:
        self.claimed()
        publisher.execute(self.github, self.work)
        self.github.records["receipt"] = (self.work / "receipt.json").read_bytes()
        self.github.run_conclusion = "failure"
        with self.assertRaises(publisher.PublishRefused):
            publisher.previous(self.github, REPO, self.github.entries)

    def test_same_run_rerun_never_admitted(self) -> None:
        environment = {**ENV, "GITHUB_RUN_ATTEMPT": "2"}
        with self.assertRaises(publisher.PublishRefused):
            publisher.initial_record(
                REPO,
                SOURCE,
                "refs/heads/main",
                TREE,
                "jbaruch/nanoclaw-fixture",
                "unpublished",
                "0.1.0",
                environment,
            )

    def test_missing_or_forged_receipt_never_authorizes_replay(self) -> None:
        self.claimed()
        with self.assertRaises(publisher.PublishRefused):
            publisher.previous(self.github, REPO, self.github.entries)
        forged = json.loads(self.github.records["intent"])
        forged["consumer_repository"] = "jbaruch/other"
        self.github.put(publisher.STAMP, publisher.encoded(forged))
        with self.assertRaises(publisher.PublishRefused):
            publisher.previous(self.github, REPO, self.github.entries)

    def test_deleted_historical_stamp_cannot_reset_gate(self) -> None:
        self.github.history = [{"sha": CLAIM}]
        with self.assertRaises(publisher.PublishRefused):
            publisher.previous(self.github, REPO, self.github.entries)

    def test_native_rerun_invalidates_prior_confirmation(self) -> None:
        self.claimed()
        publisher.execute(self.github, self.work)
        self.github.records["receipt"] = (self.work / "receipt.json").read_bytes()
        self.github.current_attempt = 2
        with self.assertRaises(publisher.PublishRefused):
            publisher.previous(self.github, REPO, self.github.entries)

    def test_reviews_must_be_current_independent_and_approved(self) -> None:
        for mutate in (
            lambda r: r[0].update(commit_id="f" * 40),
            lambda r: r[1].update(state="CHANGES_REQUESTED"),
            lambda r: r[1]["user"].update(login="untrusted[bot]"),
        ):
            with self.subTest(mutate=mutate):
                self.github.native_reviews = NativeGitHub().native_reviews
                mutate(self.github.native_reviews)
                with self.assertRaises(publisher.PublishRefused):
                    publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_failed_or_pending_ci_is_held(self) -> None:
        for status, conclusion in (
            ("in_progress", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
        ):
            self.github.checks = [{"status": status, "conclusion": conclusion}]
            with self.assertRaises(publisher.PublishRefused):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_payload_symlink_gitmodule_and_cli_config_refused(self) -> None:
        for path, mode in (
            ("skills/fixture/scripts/data.py", "120000"),
            ("skills/fixture/scripts/data.py", "160000"),
            ("skills/fixture/.tessl/config.json", "100644"),
        ):
            github = NativeGitHub()
            github.put(path, b"malicious")
            github.entries[path]["mode"] = mode
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(publisher.PublishRefused):
                    publisher.projection(
                        github,
                        REPO,
                        github.entries,
                        Path(directory) / "package",
                        "jbaruch/nanoclaw-fixture",
                    )

    def test_marker_has_exact_schema_identity(self) -> None:
        for marker in (
            {"schema_version": True, "plugin_name": "jbaruch/nanoclaw-fixture"},
            {"schema_version": 1, "plugin_name": "other/workspace"},
            {
                "schema_version": 1,
                "plugin_name": "jbaruch/nanoclaw-fixture",
                "skip_review": True,
            },
        ):
            self.github.put(publisher.MARKER, publisher.encoded(marker))
            with self.assertRaises(publisher.PublishRefused):
                publisher.enrollment(self.github, REPO, self.github.entries)

    def test_dispatch_coordinates_cannot_expand_token_scope(self) -> None:
        self.assertEqual(validate(REPO, SOURCE), "nanoclaw-fixture")
        for repo in (
            "other/repo",
            "jbaruch/repo\nother",
            "jbaruch/repo,other",
            "jbaruch/coding-policy",
            "../repo",
        ):
            with self.assertRaises(publisher.PublishRefused):
                validate(repo, SOURCE)

    def test_poll_holds_claimed_repo_without_dispatch_or_false_absence(self) -> None:
        self.claimed()
        selected, blocked = poller.targets(self.github)
        self.assertEqual(selected, [])
        self.assertEqual(blocked, [REPO])

    def test_poll_skips_confirmed_and_pending_moderation_without_publish(self) -> None:
        self.claimed()
        self.moderation = "pending"
        publisher.execute(self.github, self.work)
        self.github.records["receipt"] = (self.work / "receipt.json").read_bytes()
        self.assertEqual(poller.targets(self.github), ([], []))
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)

    def test_workflow_scopes_selected_repo_and_keeps_artifacts_immutable(self) -> None:
        workflow = (
            publisher.POLICY / ".github/workflows/fleet-publish.yml"
        ).read_text()
        self.assertIn("id: coordinates", workflow)
        self.assertIn(
            "repositories: ${{ steps.coordinates.outputs.repository_name }}", workflow
        )
        self.assertEqual(workflow.count("overwrite: false"), 2)
        self.assertIn("permission-contents: write", workflow)
        self.assertNotIn("pre-publish-script", workflow)
        self.assertNotIn("secrets: inherit", workflow)
        self.assertLess(
            workflow.index("name: Claim source atomically"),
            workflow.index("name: Persist immutable pre-effect intent"),
        )
        self.assertLess(
            workflow.index("name: Persist immutable pre-effect intent"),
            workflow.index("name: Publish once"),
        )

    def test_duplicate_json_and_nonfinite_decode_refused(self) -> None:
        for raw in (b'{"a":1,"a":2}', b'{"value":NaN}', b'"\xff"', b"[" * 2000):
            with self.assertRaises(publisher.PublishRefused):
                publisher.decode(raw)

    def test_execute_cannot_replay_same_run(self) -> None:
        self.claimed()
        publisher.execute(self.github, self.work)
        with self.assertRaises(FileExistsError):
            publisher.execute(self.github, self.work)
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)

    def test_shared_artifact_owner_scopes_and_restores_auth_on_success_and_failure(
        self,
    ) -> None:
        native = publisher.GitHub()
        observed: list[str | None] = []

        def fetch(_run: str, _kind: str) -> None:
            observed.append(os.environ.get("GH_TOKEN"))

        with patch.dict(
            os.environ,
            {
                "GH_TOKEN": "fake-selected-app",
                "FLEET_CENTRAL_TOKEN": "fake-central-only",
            },
        ):
            with patch.object(native._record_client, "artifact", side_effect=fetch):
                self.assertIsNone(native.record("101", "intent"))
            self.assertEqual(os.environ["GH_TOKEN"], "fake-selected-app")
            with patch.object(
                native._record_client,
                "artifact",
                side_effect=publisher.FleetPublishLookupError("synthetic"),
            ):
                with self.assertRaises(publisher.PublishRefused):
                    native.record("101", "receipt")
            self.assertEqual(os.environ["GH_TOKEN"], "fake-selected-app")
        self.assertEqual(observed, ["fake-central-only"])

    def test_rule_only_bootstrap_reviews_empty_skill_set_without_consumer_hooks(
        self,
    ) -> None:
        manifest = {
            "name": "jbaruch/nanoclaw-fixture",
            "version": "0.1.0",
            "rules": ["rules/fixture.md"],
        }
        self.github.put(publisher.MANIFEST, publisher.encoded(manifest))
        publisher.prepare(self.github, REPO, SOURCE, self.work, ENV)
        self.assertTrue((self.work / "package/skills").is_dir())
        self.assertFalse(any((self.work / "package/skills").iterdir()))
        self.assertEqual(len(self.cli.call_args_list), 2)

    def test_lifecycle_hooks_fail_before_any_credentialed_cli(self) -> None:
        manifest = json.loads(self.github.files[publisher.MANIFEST])
        manifest["hooks"] = {"SessionStart": [{"command": "malicious-consumer"}]}
        self.github.put(publisher.MANIFEST, publisher.encoded(manifest))
        with self.assertRaises(publisher.PublishRefused):
            publisher.prepare(self.github, REPO, SOURCE, self.work, ENV)
        self.assertFalse(self.cli.called)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_ignore_file_is_exact_inert_data_and_not_rewritten(self) -> None:
        raw = b"# Preserve source-owned package exclusions\r\n**/tests/\r\nskills/fixture/dormant/\r\n!.github/\r\n"
        self.github.put(".tesslignore", raw)
        publisher.projection(
            self.github,
            REPO,
            self.github.entries,
            self.work / "inert-package",
            "jbaruch/nanoclaw-fixture",
        )
        self.assertEqual((self.work / "inert-package/.tesslignore").read_bytes(), raw)
        self.assertFalse((self.work / "inert-package/.github").exists())
        self.assertFalse((self.work / "inert-package/.git").exists())

    def test_missing_linked_malformed_or_oversized_ignore_refuses(self) -> None:
        for raw, mode in (
            (b"rules/", "120000"),
            (b"\xff", "100644"),
            (b"a\x00b", "100644"),
            (b"a" * (publisher.MAX_IGNORE_BYTES + 1), "100644"),
        ):
            github = NativeGitHub()
            github.put(".tesslignore", raw)
            github.entries[".tesslignore"]["mode"] = mode
            with (
                self.subTest(mode=mode, size=len(raw)),
                self.assertRaises(publisher.PublishRefused),
            ):
                publisher.projection(
                    github,
                    REPO,
                    github.entries,
                    self.work / "bad-ignore",
                    "jbaruch/nanoclaw-fixture",
                )
        github = NativeGitHub()
        del github.entries[".tesslignore"]
        with self.assertRaises(publisher.PublishRefused):
            publisher.projection(
                github,
                REPO,
                github.entries,
                self.work / "missing-ignore",
                "jbaruch/nanoclaw-fixture",
            )

    def test_publish_helper_cannot_confirm_different_source_version(self) -> None:
        self.claimed()
        original = self.helper

        def helper(
            name: str, *args: str, **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            result = original(name, *args, **kwargs)
            if name == "smart-publish.sh":
                value = json.loads(result.stdout)
                value["version"] = "9.9.9"
                return subprocess.CompletedProcess([], 0, publisher.encoded(value))
            return result

        with patch.object(publisher, "helper", side_effect=helper):
            with self.assertRaises(publisher.PublishRefused):
                publisher.execute(self.github, self.work)
        self.assertEqual(self.receipt()["state"], "uncertain")
        self.assertNotIn("verify-moderation-cleared.sh", self.helpers)

    def test_native_attempt_must_match_actual_id_main_branch_and_source_identity(
        self,
    ) -> None:
        record = publisher.initial_record(
            REPO,
            SOURCE,
            "refs/heads/main",
            TREE,
            "jbaruch/nanoclaw-fixture",
            "0.1.0",
            "0.1.1",
            ENV,
        )
        original = self.github.api
        for fragment, field, wrong in (
            ("/actions/runs/101", "head_branch", "feature/not-main"),
            ("/attempts/1", "id", 102),
            (f"/git/commits/{SOURCE}", "sha", "f" * 40),
        ):

            def api(route: str, *args: Any, **kwargs: Any) -> Any:
                value = deepcopy(original(route, *args, **kwargs))
                if not isinstance(value, dict):
                    raise AssertionError("Expected fake native JSON object.")
                if fragment in route:
                    value[field] = wrong
                return value

            with (
                self.subTest(field=field),
                patch.object(self.github, "api", side_effect=api),
            ):
                with self.assertRaises(publisher.PublishRefused):
                    publisher.verify_attempt(self.github, record, successful=True)

    def test_combined_commit_status_contexts_must_all_succeed(self) -> None:
        for state in ("pending", "failure", "error", None):
            self.github.status["statuses"] = [{"id": 7, "state": state}]
            with self.subTest(state=state), self.assertRaises(publisher.PublishRefused):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_native_page_envelopes_require_exact_totals_not_short_page_absence(
        self,
    ) -> None:
        native = publisher.GitHub()
        for response in (
            {"total_count": 2, "check_runs": [{"id": 1}]},
            {"total_count": True, "check_runs": []},
            {"check_runs": []},
            {"total_count": 1, "check_runs": []},
            {"total_count": 1, "check_runs": [{"id": 1}, {"id": 2}]},
        ):
            with (
                self.subTest(response=response),
                patch.object(native, "api", return_value=response),
            ):
                with self.assertRaises(publisher.PublishRefused):
                    native.pages(
                        f"repos/{REPO}/commits/{HEAD}/check-runs", "check_runs"
                    )

    def test_native_multi_page_totals_and_ids_are_complete_and_stable(self) -> None:
        native = publisher.GitHub()
        first = {"total_count": 101, "check_runs": [{"id": i} for i in range(1, 101)]}
        second = {"total_count": 101, "check_runs": [{"id": 101}]}
        with patch.object(native, "api", side_effect=[first, second]) as api:
            self.assertEqual(
                len(
                    native.pages(
                        f"repos/{REPO}/commits/{HEAD}/check-runs", "check_runs"
                    )
                ),
                101,
            )
            self.assertEqual(api.call_count, 2)
        for bad in (
            {"total_count": 102, "check_runs": [{"id": 101}, {"id": 102}]},
            {"total_count": 101, "check_runs": [{"id": 1}]},
        ):
            with (
                self.subTest(bad=bad),
                patch.object(native, "api", side_effect=[first, bad]),
            ):
                with self.assertRaises(publisher.PublishRefused):
                    native.pages(
                        f"repos/{REPO}/commits/{HEAD}/check-runs", "check_runs"
                    )

    def test_native_empty_contexts_need_total_zero(self) -> None:
        native = publisher.GitHub()
        with patch.object(
            native, "api", return_value={"total_count": 0, "statuses": []}
        ):
            self.assertEqual(
                native.pages(f"repos/{REPO}/commits/{HEAD}/status", "statuses"), []
            )
        with patch.object(
            native, "api", return_value={"total_count": 1, "statuses": []}
        ):
            with self.assertRaises(publisher.PublishRefused):
                native.pages(f"repos/{REPO}/commits/{HEAD}/status", "statuses")

    def test_graphql_claim_uses_selected_app_token_and_structured_stdin(self) -> None:
        native = publisher.GitHub()
        body = {
            "query": "mutation Fixture($input: UpdateRefsInput!) { updateRefs(input: $input) { clientMutationId } }",
            "variables": {
                "input": {
                    "repositoryId": "R_fixture",
                    "clientMutationId": "fixture",
                    "refUpdates": [
                        {
                            "name": "refs/heads/main",
                            "beforeOid": SOURCE,
                            "afterOid": CLAIM,
                            "force": False,
                        }
                    ],
                }
            },
        }
        with patch.dict(
            os.environ,
            {
                "GH_TOKEN": "fake-selected-app",
                "FLEET_CENTRAL_TOKEN": "fake-central-read-only",
            },
        ):
            with patch.object(
                publisher.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, b'{"data":{}}'),
            ) as command:
                native.api("graphql", body)
        self.assertEqual(
            command.call_args.kwargs["env"]["GH_TOKEN"], "fake-selected-app"
        )
        self.assertEqual(json.loads(command.call_args.kwargs["input"]), body)
        self.assertEqual(
            command.call_args.args[0],
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "graphql",
                "--method",
                "POST",
                "--input",
                "-",
            ],
        )

    def test_graphql_response_error_or_wrong_mutation_identity_never_dispatches_publish(
        self,
    ) -> None:
        publisher.prepare(self.github, REPO, SOURCE, self.work, ENV)
        original = self.github.api

        def api(route: str, *args: Any, **kwargs: Any) -> Any:
            result = original(route, *args, **kwargs)
            if route == "graphql":
                return {
                    "data": {"updateRefs": {"clientMutationId": "some-other-operation"}}
                }
            return result

        with patch.object(self.github, "api", side_effect=api):
            with self.assertRaises(publisher.PublishRefused):
                publisher.claim(self.github, self.work)
        self.assertIn(publisher.STAMP, self.github.entries)
        self.assertFalse((self.work / "claim.json").exists())
        self.assertFalse(self.github.records)
        self.assertNotIn("smart-publish.sh", self.helpers)


class HistoryGitHub(NativeGitHub):
    """Actual immutable native commit/tree graph, including rapid merge backlog."""

    SECOND, THIRD, BEFORE = "f" * 40, "9" * 40, "0" * 40

    def __init__(self) -> None:
        super().__init__()
        self.commits: dict[str, dict[str, Any]] = {}
        self.trees: dict[str, dict[str, dict[str, Any]]] = {}
        self.current_run = "101"
        self.archived_records: dict[str, dict[str, bytes]] = {}
        self.next_commit = 1
        before = deepcopy(self.entries)
        del before[publisher.MARKER]
        self.snapshot(self.BEFORE, [], before)
        text = "# Changelog\n"
        previous_sha = self.BEFORE
        for sha, name in (
            (SOURCE, "First"),
            (self.SECOND, "Second"),
            (self.THIRD, "Third"),
        ):
            text = (
                "# Changelog\n\n### "
                + name
                + "\n\n- Entry.\n"
                + publisher.changelog_body(text)
            )
            self.put("CHANGELOG.md", text.encode())
            self.snapshot(sha, [previous_sha], deepcopy(self.entries))
            previous_sha = sha
        self.tip = self.THIRD

    def snapshot(
        self, sha: str, parents: list[str], entries: dict[str, dict[str, Any]]
    ) -> str:
        tree_sha = hashlib.sha1(publisher.encoded(entries)).hexdigest()
        self.trees[tree_sha] = deepcopy(entries)
        self.commits[sha] = {
            "sha": sha,
            "tree": {"sha": tree_sha},
            "parents": [{"sha": p} for p in parents],
        }
        return tree_sha

    def run_records(self, run: str) -> dict[str, bytes]:
        return self.records if run == self.current_run else self.archived_records[run]

    def record(self, run_id: str, kind: str) -> publisher.FleetPublishRecord | None:
        raw = self.run_records(run_id).get(kind)
        return publisher.decode_record(raw) if raw else None

    def api(
        self,
        path: str,
        payload: Any = None,
        *,
        binary: bool = False,
        method: str = "POST",
    ) -> Any:
        if path == "graphql" and isinstance(payload, dict):
            self.calls.append((path, payload))
            update = payload["variables"]["input"]
            ref = update["refUpdates"][0]
            assert update["repositoryId"] == self.metadata["node_id"]
            assert ref["name"] == "refs/heads/main" and ref["force"] is False
            if self.reset_before_graphql is not None:
                self.tip = self.reset_before_graphql
                self.reset_before_graphql = None
            if self.refuse_push or self.tip != ref["beforeOid"]:
                return {
                    "errors": [{"message": "Expected OID differs"}],
                    "data": {"updateRefs": None},
                }
            self.tip = ref["afterOid"]
            self.entries = deepcopy(self.trees[self.commits[self.tip]["tree"]["sha"]])
            self.history.append({"sha": self.tip})
            return {
                "data": {"updateRefs": {"clientMutationId": update["clientMutationId"]}}
            }
        if path.startswith(f"repos/{REPO}/git/commits/"):
            return deepcopy(self.commits[path.rsplit("/", 1)[1]])
        match = re.fullmatch(
            f"repos/{REPO}/git/trees/([a-f0-9]{{40}})\\?recursive=1", path
        )
        if match:
            tree_sha = match[1]
            return {
                "sha": tree_sha,
                "truncated": False,
                "tree": deepcopy(list(self.trees[tree_sha].values())),
            }
        if path == f"repos/{REPO}/git/trees" and isinstance(payload, dict):
            entries = deepcopy(self.trees[payload["base_tree"]])
            for item in payload["tree"]:
                entries[item["path"]] = dict(item)
            tree_sha = hashlib.sha1(publisher.encoded(entries)).hexdigest()
            self.trees[tree_sha] = entries
            return {"sha": tree_sha}
        if path == f"repos/{REPO}/git/commits" and isinstance(payload, dict):
            sha = f"e{self.next_commit:039x}"
            self.next_commit += 1
            self.commits[sha] = {
                "sha": sha,
                "tree": {"sha": payload["tree"]},
                "parents": [{"sha": p} for p in payload["parents"]],
            }
            return {"sha": sha}
        if path == f"repos/{REPO}/git/refs/heads/main" and isinstance(payload, dict):
            publisher.require(not self.refuse_push, "Synthetic protected ref refusal.")
            assert method == "PATCH" and payload["force"] is False
            self.tip = payload["sha"]
            self.entries = deepcopy(self.trees[self.commits[self.tip]["tree"]["sha"]])
            self.history.append({"sha": self.tip})
            return {"object": {"sha": self.tip}}
        if path.startswith("repos/jbaruch/coding-policy/actions/runs/"):
            run = path.split("/")[5]
            return self.native_run(run)
        if path.startswith("repos/jbaruch/coding-policy/actions/artifacts/"):
            run, name = path.split("/")[-2].split("-")
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(name + ".json", self.run_records(run)[name])
            return stream.getvalue()
        return super().api(path, payload, binary=binary, method=method)

    def pages(self, route: str, key: str | None = None) -> list[Any]:
        match = re.fullmatch(f"repos/{REPO}/commits/([a-f0-9]{{40}})/pulls", route)
        if match and match[1] in (SOURCE, self.SECOND, self.THIRD):
            self.current_pull_source = match[1]
            return [
                {
                    "merge_commit_sha": match[1],
                    "merged_at": "2026-10-27T01:02:00Z",
                    "number": 7,
                    "base": {"ref": "main"},
                    "head": {"sha": HEAD, "repo": {"full_name": REPO}},
                }
            ]
        if route.startswith("repos/jbaruch/coding-policy/actions/runs/"):
            run = route.split("/")[5]
            return [
                {
                    "name": "fleet-publish-" + name + "-v1",
                    "id": run + "-" + name,
                    "expired": False,
                }
                for name in self.run_records(run)
            ]
        return super().pages(route, key)


class QueueTests(PublisherFixture):
    def finish_first(self, *, moderation: str = "pass") -> HistoryGitHub:
        github = HistoryGitHub()
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        publisher.claim(github, self.work)
        github.records["intent"] = (self.work / "intent.json").read_bytes()
        self.moderation = moderation
        publisher.execute(github, self.work)
        github.records["receipt"] = (self.work / "receipt.json").read_bytes()
        return github

    def test_three_rapid_merges_publish_each_exact_source_and_preserve_future_entries(
        self,
    ) -> None:
        github = HistoryGitHub()
        for offset, (sha, version, label) in enumerate(
            (
                (SOURCE, "0.1.1", "First"),
                (github.SECOND, "0.1.2", "Second"),
                (github.THIRD, "0.1.3", "Third"),
            )
        ):
            work = self.work / str(offset)
            work.mkdir()
            run = str(101 + offset)
            if offset:
                github.archived_records[github.current_run] = dict(github.records)
                github.records = {}
                github.current_run = run
            github.dispatch_sources[run] = sha
            publisher.prepare(github, REPO, sha, work, {**ENV, "GITHUB_RUN_ID": run})
            intent = json.loads((work / "intent.json").read_bytes())
            self.assertEqual(intent["source_sha"], sha)
            self.assertEqual(intent["version"], version)
            package_text = (work / "package/CHANGELOG.md").read_text()
            self.assertIn("## " + version, package_text)
            if label != "Third":
                self.assertNotIn("### Third", package_text)
            publisher.claim(github, work)
            github.records["intent"] = (work / "intent.json").read_bytes()
            self.assertTrue(publisher.execute(github, work))
            github.records["receipt"] = (work / "receipt.json").read_bytes()
            main_text = publisher.file(
                github, REPO, github.entries, "CHANGELOG.md"
            ).decode()
            for remaining in ("First", "Second", "Third"):
                self.assertEqual(main_text.count("### " + remaining), 1)
            if offset == 0:
                self.assertTrue(
                    main_text.index("### Third")
                    < main_text.index("### Second")
                    < main_text.index("## 0.1.1")
                )
                self.assertNotIn("## 0.1.2", main_text)
            self.baseline = version
        self.assertEqual(self.helpers.count("smart-publish.sh"), 3)
        self.assertEqual(
            json.loads(
                publisher.file(github, REPO, github.entries, publisher.MANIFEST)
            )["version"],
            "0.1.3",
        )
        self.assertEqual(poller.targets(github), ([], []))

    def test_later_tip_cannot_skip_earliest_pending_merge(self) -> None:
        github = HistoryGitHub()
        with self.assertRaises(publisher.PublishRefused):
            publisher.prepare(github, REPO, github.THIRD, self.work, ENV)
        self.assertNotIn("smart-publish.sh", self.helpers)
        self.assertFalse((self.work / "intent.json").exists())

    def test_cas_race_keeps_newer_code_and_sends_zero(self) -> None:
        github = HistoryGitHub()
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        github.tip = "8" * 40
        with self.assertRaises(publisher.PublishRefused):
            publisher.claim(github, self.work)
        self.assertEqual(github.tip, "8" * 40)
        self.assertFalse((self.work / "claim.json").exists())
        self.assertFalse(github.records)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_atomic_before_oid_rejects_concurrent_ancestor_reset(self) -> None:
        github = HistoryGitHub()
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        prepared_tip = github.tip
        github.reset_before_graphql = github.BEFORE
        with self.assertRaises(publisher.PublishRefused):
            publisher.claim(github, self.work)
        self.assertEqual(github.tip, github.BEFORE)
        self.assertFalse(github.records)
        self.assertFalse((self.work / "claim.json").exists())
        self.assertNotIn("smart-publish.sh", self.helpers)
        mutation = (
            next(payload for route, payload in github.calls if route == "graphql")
            if any(route == "graphql" for route, _ in github.calls)
            else None
        )
        # The immutable prepared parent cannot silently turn into the reset OID.
        self.assertNotEqual(prepared_tip, github.BEFORE)
        if not isinstance(mutation, dict):
            raise AssertionError(
                "Expected the actual structured GraphQL claim mutation."
            )
        self.assertEqual(
            mutation["variables"]["input"]["refUpdates"][0]["beforeOid"], prepared_tip
        )

    def test_non_suffix_changelog_refuses_without_claim(self) -> None:
        github = HistoryGitHub()
        github.put("CHANGELOG.md", b"# Changelog\n\n### Reordered unrelated text\n")
        github.snapshot(github.THIRD, [github.SECOND], github.entries)
        with self.assertRaises(publisher.PublishRefused):
            publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        self.assertNotIn("smart-publish.sh", self.helpers)
        self.assertFalse(any(payload for _, payload in github.calls))

    def test_incomplete_first_parent_and_reenrollment_are_held(self) -> None:
        github = HistoryGitHub()
        del github.commits[SOURCE]["parents"]
        with self.assertRaises(publisher.PublishRefused):
            publisher.pending_sources(
                github, REPO, github.tip, None, "jbaruch/nanoclaw-fixture"
            )
        github = HistoryGitHub()
        original = github.pages
        with patch.object(
            github,
            "pages",
            side_effect=lambda route, key=None: [{"sha": SOURCE}, {"sha": HEAD}]
            if "fleet-publish-enabled" in route
            else original(route, key),
        ):
            with self.assertRaises(publisher.PublishRefused):
                publisher.pending_sources(
                    github, REPO, github.tip, None, "jbaruch/nanoclaw-fixture"
                )

    def test_unverified_direct_commit_cannot_become_selected_source(self) -> None:
        github = HistoryGitHub()
        github.commits[SOURCE]["sha"] = "8" * 40
        with self.assertRaises(publisher.PublishRefused):
            publisher.pending_sources(
                github, REPO, github.tip, None, "jbaruch/nanoclaw-fixture"
            )

    def test_newer_manual_manifest_version_and_metadata_are_preserved(self) -> None:
        github = HistoryGitHub()
        raw = json.loads(
            publisher.file(github, REPO, github.entries, publisher.MANIFEST)
        )
        raw.update(version="2.0.0", description="Future reviewed source metadata")
        github.put(publisher.MANIFEST, publisher.encoded(raw))
        github.snapshot(github.THIRD, [github.SECOND], github.entries)
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        self.assertEqual(
            json.loads((self.work / "package" / publisher.MANIFEST).read_bytes())[
                "version"
            ],
            "0.1.1",
        )
        self.assertEqual(
            json.loads((self.work / "bookkeeping/manifest.json").read_bytes()), raw
        )

    def test_moderation_recovery_new_run_never_republishes_or_loses_later_queue(
        self,
    ) -> None:
        github = self.finish_first(moderation="pending")
        github.archived_records["101"] = dict(github.records)
        github.records = {}
        github.current_run = "102"
        self.moderation = "pass"
        recovery = self.work / "recovery"
        recovery.mkdir()
        publisher.prepare(
            github, REPO, SOURCE, recovery, {**ENV, "GITHUB_RUN_ID": "102"}
        )
        self.assertEqual(
            json.loads((recovery / "selection.json").read_bytes())["mode"],
            "moderation_only",
        )
        self.assertFalse((recovery / "package").exists())
        publisher.claim(github, recovery)
        github.records["intent"] = (recovery / "intent.json").read_bytes()
        self.assertTrue(publisher.execute(github, recovery))
        github.records["receipt"] = (recovery / "receipt.json").read_bytes()
        result = json.loads(github.records["receipt"])
        self.assertEqual(result["origin_run_id"], "101")
        self.assertEqual(result["run_id"], "102")
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)
        selected, held = poller.targets(github)
        self.assertEqual(held, [])
        self.assertEqual(selected[0]["source_sha"], github.SECOND)

    def test_paused_old_gate_blocks_newer_queued_source(self) -> None:
        github = self.finish_first(moderation="pending")
        later = self.work / "later"
        later.mkdir()
        with self.assertRaises(publisher.PublishRefused):
            publisher.prepare(
                github, REPO, github.SECOND, later, {**ENV, "GITHUB_RUN_ID": "102"}
            )
        self.assertEqual(poller.targets(github), ([], []))
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)

    def test_direct_commit_without_native_merged_pr_cannot_be_published(self) -> None:
        github = HistoryGitHub()
        original = github.pages
        with patch.object(
            github,
            "pages",
            side_effect=lambda route, key=None: []
            if route == f"repos/{REPO}/commits/{SOURCE}/pulls"
            else original(route, key),
        ):
            with self.assertRaises(publisher.PublishRefused):
                publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_source_controlled_fake_bookkeeping_cannot_skip_review(self) -> None:
        github = HistoryGitHub()
        fake_claim = publisher.initial_record(
            REPO,
            SOURCE,
            "refs/heads/main",
            TREE,
            "jbaruch/nanoclaw-fixture",
            "0.1.0",
            "0.1.1",
            ENV,
        )
        github.put(publisher.STAMP, publisher.encoded(fake_claim))
        github.snapshot(github.THIRD, [github.SECOND], github.entries)
        github.history = [{"sha": github.THIRD}]
        with self.assertRaises(publisher.PublishRefused):
            publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_later_main_merge_retaining_exact_claim_can_publish_only_frozen_source(
        self,
    ) -> None:
        github = HistoryGitHub()
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        publisher.claim(github, self.work)
        claim_sha = github.tip
        github.records["intent"] = (self.work / "intent.json").read_bytes()
        later = "8" * 40
        github.put("README.md", b"# Later reviewed source\n")
        github.snapshot(later, [claim_sha], github.entries)
        github.tip = later
        self.assertTrue(publisher.execute(github, self.work))
        self.assertEqual(
            json.loads((self.work / "receipt.json").read_bytes())["source_sha"], SOURCE
        )
        self.assertNotIn(
            "Later reviewed source", (self.work / "package/README.md").read_text()
        )

    def test_removed_then_reintroduced_claim_after_cas_is_still_refused(self) -> None:
        github = HistoryGitHub()
        publisher.prepare(github, REPO, SOURCE, self.work, ENV)
        publisher.claim(github, self.work)
        claim_sha = github.tip
        github.records["intent"] = (self.work / "intent.json").read_bytes()
        retained = deepcopy(github.entries)
        removed = deepcopy(retained)
        del removed[publisher.STAMP]
        middle, final = "7" * 40, "8" * 40
        github.snapshot(middle, [claim_sha], removed)
        github.snapshot(final, [middle], retained)
        github.tip = final
        with self.assertRaises(publisher.PublishRefused):
            publisher.execute(github, self.work)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_pending_queue_history_bound_is_not_completion(self) -> None:
        github = HistoryGitHub()
        github.commits[SOURCE]["parents"] = [{"sha": SOURCE}]
        with self.assertRaises(publisher.PublishRefused):
            publisher.pending_sources(
                github, REPO, github.tip, None, "jbaruch/nanoclaw-fixture"
            )

    def test_moderation_recovery_after_later_merge_keeps_newer_code_and_source_queue(
        self,
    ) -> None:
        github = self.finish_first(moderation="pending")
        old_claim = github.tip
        github.put("README.md", b"# Later source must survive recovery\n")
        later = "8" * 40
        github.snapshot(later, [old_claim], github.entries)
        github.tip = later
        before_changelog = publisher.file(github, REPO, github.entries, "CHANGELOG.md")
        github.archived_records["101"] = dict(github.records)
        github.current_run = "102"
        github.records = {}
        self.moderation = "pass"
        recovery = self.work / "recovery-later"
        recovery.mkdir()
        publisher.prepare(
            github, REPO, SOURCE, recovery, {**ENV, "GITHUB_RUN_ID": "102"}
        )
        publisher.claim(github, recovery)
        github.records["intent"] = (recovery / "intent.json").read_bytes()
        self.assertTrue(publisher.execute(github, recovery))
        github.records["receipt"] = (recovery / "receipt.json").read_bytes()
        self.assertEqual(
            publisher.file(github, REPO, github.entries, "README.md"),
            b"# Later source must survive recovery\n",
        )
        self.assertEqual(
            publisher.file(github, REPO, github.entries, "CHANGELOG.md"),
            before_changelog,
        )
        self.assertEqual(self.helpers.count("smart-publish.sh"), 1)
        selected, held = poller.targets(github)
        self.assertEqual(held, [])
        self.assertEqual(selected[0]["source_sha"], github.SECOND)


class IndependentClaimsTests(PublisherFixture):
    def add_prior(self, *, claim: str = "skipped", effect: str = "skipped") -> None:
        self.github.other_runs["100"] = {"status": "completed"}
        self.github.native_jobs["100"] = [
            {
                "id": 400,
                "name": "publish",
                "run_id": 100,
                "head_sha": WORKFLOW,
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {
                        "name": "Prepare verified data-only package and intent",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "name": "Claim source atomically before publication",
                        "status": "completed",
                        "conclusion": claim,
                    },
                    {
                        "name": "Publish once or perform moderation readback only",
                        "status": "completed",
                        "conclusion": effect,
                    },
                ],
            }
        ]

    def old_intent(self) -> publisher.FleetPublishRecord:
        return publisher.initial_record(
            REPO,
            SOURCE,
            "refs/heads/main",
            TREE,
            "jbaruch/nanoclaw-fixture",
            "0.1.0",
            "0.1.1",
            {**ENV, "GITHUB_RUN_ID": "100"},
        )

    def test_erased_consumer_history_cannot_hide_prior_central_original(self) -> None:
        self.add_prior()
        self.github.other_records["100"] = {
            "intent": publisher.encoded(self.old_intent())
        }
        self.assertEqual(self.github.history, [])
        self.assertNotIn(publisher.STAMP, self.github.entries)
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()
        self.assertFalse((self.work / "intent.json").exists())
        self.assertFalse(self.cli.called)

    def test_absent_intent_can_only_be_excluded_with_skipped_native_claim_and_effect(
        self,
    ) -> None:
        self.add_prior()
        self.prepare()
        self.assertTrue((self.work / "intent.json").is_file())
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_claim_failure_or_effect_attempt_is_not_no_effect_evidence(self) -> None:
        for claim, effect in (
            ("failure", "skipped"),
            ("success", "skipped"),
            ("skipped", "failure"),
            ("skipped", "success"),
            ("cancelled", "skipped"),
        ):
            self.add_prior(claim=claim, effect=effect)
            with (
                self.subTest(claim=claim, effect=effect),
                self.assertRaises(publisher.PublishRefused),
            ):
                publisher.prepare(self.github, REPO, SOURCE, self.work, ENV)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_expired_artifact_never_uses_skipped_job_as_absence_fallback(self) -> None:
        self.add_prior()
        original = self.github.record
        with patch.object(
            self.github,
            "record",
            side_effect=lambda run, kind: (_ for _ in ()).throw(
                publisher.PublishRefused("expired")
            )
            if run == "100"
            else original(run, kind),
        ):
            with self.assertRaises(publisher.PublishRefused):
                self.prepare()
        self.assertFalse((self.work / "intent.json").exists())

    def test_current_context_native_binding_cannot_exclude_some_other_run(self) -> None:
        self.github.other_runs["101"] = {"head_sha": "f" * 40}
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()

    def test_other_locator_is_ignored_only_after_reviewed_protocol_verification(
        self,
    ) -> None:
        self.add_prior()
        self.github.other_runs["100"].update(
            display_title=f"fleet-publish-v1:jbaruch/other:{SOURCE}", head_sha="f" * 40
        )
        with patch.object(
            publisher,
            "verify_workflow_protocol",
            side_effect=lambda _client, sha: (_ for _ in ()).throw(
                publisher.FleetPublishLookupError("unknown_producer")
            )
            if sha == "f" * 40
            else None,
        ):
            with self.assertRaises(publisher.PublishRefused):
                self.prepare()

    def test_matching_locator_cannot_relabel_a_different_actual_intent(self) -> None:
        self.add_prior()
        intent = self.old_intent()
        intent["consumer_repository"] = "jbaruch/other"
        self.github.other_records["100"] = {"intent": publisher.encoded(intent)}
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()

    def test_dispatch_list_must_be_complete_and_include_actual_current_run(
        self,
    ) -> None:
        original = self.github.pages
        with patch.object(
            self.github,
            "pages",
            side_effect=lambda route, key=None: []
            if "/workflows/fleet-publish.yml/runs" in route
            else original(route, key),
        ):
            with self.assertRaises(publisher.PublishRefused):
                self.prepare()

    def test_skipped_rerun_cannot_clear_unknown_previous_attempt(self) -> None:
        self.add_prior()
        self.github.other_runs["100"]["run_attempt"] = 2
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()

    def test_malformed_locator_requires_actual_skipped_claim_proof(self) -> None:
        self.add_prior()
        self.github.other_runs["100"]["display_title"] = (
            "fleet-publish-v1:invalid-input"
        )
        self.prepare()
        self.assertTrue((self.work / "intent.json").is_file())

    def test_native_run_template_cannot_claim_an_unexecuted_foreign_workflow(
        self,
    ) -> None:
        self.add_prior()
        self.github.other_runs["100"]["head_branch"] = "unreviewed-feature"
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()

    def test_current_locator_must_bind_actual_prepared_input(self) -> None:
        self.github.other_runs["101"] = {
            "display_title": "fleet-publish-v1:jbaruch/other:" + SOURCE
        }
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()


class PackagingCliTests(unittest.TestCase):
    def test_actual_cli_archive_preserves_source_test_and_dormant_exclusions(
        self,
    ) -> None:
        cli = shutil.which("tessl")
        if cli is None:
            self.fail(
                "Install the canonical Tessl CLI before running the mandatory local packaging regression."
            )
        github = NativeGitHub()
        github.put(
            "skills/fixture/SKILL.md",
            b"---\nname: fixture\ndescription: Inspect fixed synthetic fixture data.\n---\n# Fixture\n\nRead the synthetic fixture.\n",
        )
        github.put(
            "skills/fixture/tests/test_private.py",
            b"This synthetic test payload must never publish.\n",
        )
        github.put(
            "skills/fixture/dormant/held.py",
            b"This synthetic held payload must never publish.\n",
        )
        with tempfile.TemporaryDirectory(prefix="fleet-pack-proof-") as directory:
            package = Path(directory) / "package"
            publisher.projection(
                github, REPO, github.entries, package, "jbaruch/nanoclaw-fixture"
            )
            self.assertTrue(
                (package / "skills/fixture/tests/test_private.py").is_file()
            )
            self.assertTrue((package / "skills/fixture/dormant/held.py").is_file())
            archive = Path(directory) / "artifact.tgz"
            environment = {
                key: value
                for key, value in os.environ.items()
                if key
                not in (
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                    "FLEET_CENTRAL_TOKEN",
                    "TESSL_TOKEN",
                )
            }
            result = subprocess.run(
                [cli, "plugin", "pack", "--output", str(archive), str(package)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                (result.stdout + result.stderr).decode("utf-8", "replace"),
            )
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as packed:
                names = packed.getnames()
                self.assertIn("skills/fixture/scripts/data.py", names)
                self.assertIn("skills/fixture/SKILL.md", names)
                self.assertNotIn("skills/fixture/tests/test_private.py", names)
                self.assertNotIn("skills/fixture/dormant/held.py", names)
                self.assertFalse(
                    any(name.startswith((".git/", ".github/")) for name in names)
                )
                active = packed.extractfile("skills/fixture/scripts/data.py")
                if active is None:
                    self.fail("The built artifact lost its active declared helper.")
                self.assertEqual(
                    active.read(), github.files["skills/fixture/scripts/data.py"]
                )
            # RED control: the former synthesized ignore really would ship the
            # dormant bytes with this SAME native CLI and source projection.
            (package / ".tesslignore").write_bytes(b".git/\n.github/\n")
            old_archive = Path(directory) / "old-fixed-ignore.tgz"
            old_result = subprocess.run(
                [cli, "plugin", "pack", "--output", str(old_archive), str(package)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                old_result.returncode,
                0,
                (old_result.stdout + old_result.stderr).decode("utf-8", "replace"),
            )
            with tarfile.open(old_archive, "r:gz") as old_packed:
                self.assertIn("skills/fixture/dormant/held.py", old_packed.getnames())


class RequiredContextTests(PublisherFixture):
    def legacy(self, context: str, app_id: int | None) -> None:
        self.github.required_branch["protected"] = True
        self.github.required_branch["protection"] = {
            "enabled": True,
            "required_status_checks": {
                "contexts": [context],
                "checks": [{"context": context, "app_id": app_id}],
                "enforcement_level": "everyone",
            },
        }

    def ruleset(self, context: str, app_id: int | None) -> None:
        self.github.rules = [
            {
                "type": "required_status_checks",
                "ruleset_id": 1,
                "ruleset_source_type": "Repository",
                "ruleset_source": REPO,
                "parameters": {
                    "required_status_checks": [
                        {"context": context, "integration_id": app_id}
                    ]
                },
            }
        ]

    def test_actual_unprotected_native_shape_proves_no_configured_contexts(
        self,
    ) -> None:
        # Sanitized construction branch response had this complete shape;
        # independent active rules returned HTTP200 [] (never a403 fallback).
        self.github.checks = []
        self.assertEqual(
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main"),
            self.github.source_merged_at,
        )

    def test_empty_native_contexts_do_not_replace_both_current_review_lanes(
        self,
    ) -> None:
        self.github.checks = []
        for lane in range(2):
            reviews = self.github.native_reviews
            self.github.native_reviews = [
                review for i, review in enumerate(reviews) if i != lane
            ]
            with (
                self.subTest(missing_lane=lane),
                self.assertRaises(publisher.PublishRefused),
            ):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")
            self.github.native_reviews = reviews

    def test_missing_required_context_is_not_replaced_by_unrelated_success(
        self,
    ) -> None:
        self.legacy("required-build", None)
        self.github.checks = [
            {"name": "unrelated", "status": "completed", "conclusion": "success"}
        ]
        with self.assertRaises(publisher.PublishRefused):
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_required_check_must_be_green_and_from_its_bound_app(self) -> None:
        self.legacy("required-build", 42)
        for status, conclusion, app_id in (
            ("in_progress", None, 42),
            ("completed", "failure", 42),
            ("completed", "success", 43),
            ("completed", "skipped", 42),
        ):
            self.github.checks = [
                {
                    "name": "required-build",
                    "status": status,
                    "conclusion": conclusion,
                    "app": {"id": app_id},
                }
            ]
            with (
                self.subTest(status=status, conclusion=conclusion, app=app_id),
                self.assertRaises(publisher.PublishRefused),
            ):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")
        self.github.checks = [
            {
                "name": "required-build",
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 42},
            }
        ]
        self.assertEqual(
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main"),
            self.github.source_merged_at,
        )

    def test_legacy_and_ruleset_requirements_are_both_required(self) -> None:
        self.legacy("legacy-build", None)
        self.ruleset("ruleset-build", 42)
        self.github.checks = [
            {"name": "legacy-build", "status": "completed", "conclusion": "success"}
        ]
        with self.assertRaises(publisher.PublishRefused):
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")
        self.github.checks.append(
            {
                "name": "ruleset-build",
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 42},
            }
        )
        self.assertEqual(
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main"),
            self.github.source_merged_at,
        )

    def test_unbound_required_status_can_be_satisfied_by_native_status(self) -> None:
        self.legacy("external-ci", None)
        self.github.checks = []
        self.github.status["statuses"] = [
            {"id": 9, "context": "external-ci", "state": "success"}
        ]
        self.assertEqual(
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main"),
            self.github.source_merged_at,
        )
        self.legacy("external-ci", 42)
        with self.assertRaises(publisher.PublishRefused):
            publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_unknown_required_workflow_or_app_binding_holds(self) -> None:
        for rule in (
            {
                "type": "workflows",
                "parameters": {
                    "workflows": [{"path": ".github/workflows/required.yml"}]
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "build"}]},
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "build", "integration_id": "unknown"}
                    ]
                },
            },
        ):
            self.github.rules = [rule]
            with self.subTest(rule=rule), self.assertRaises(publisher.PublishRefused):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_partial_or_unavailable_required_metadata_never_means_none(self) -> None:
        for missing in ("protection", "protected"):
            original = deepcopy(self.github.required_branch)
            del self.github.required_branch[missing]
            with (
                self.subTest(missing=missing),
                self.assertRaises(publisher.PublishRefused),
            ):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")
            self.github.required_branch = original
        original_pages = self.github.pages
        with patch.object(
            self.github,
            "pages",
            side_effect=lambda route, key=None: (_ for _ in ()).throw(
                publisher.PublishRefused("HTTP403")
            )
            if "/rules/branches/" in route
            else original_pages(route, key),
        ):
            with self.assertRaises(publisher.PublishRefused):
                publisher.gate_source(self.github, REPO, SOURCE, "refs/heads/main")

    def test_native_rules_use_complete_ruleset_type_identity(self) -> None:
        client = publisher.GitHub()
        rule = {
            "type": "required_status_checks",
            "ruleset_id": 17,
            "ruleset_source_type": "Repository",
            "ruleset_source": REPO,
            "parameters": {
                "required_status_checks": [{"context": "build", "integration_id": None}]
            },
        }
        with patch.object(client, "api", return_value=[rule]):
            self.assertEqual(client.pages(f"repos/{REPO}/rules/branches/main"), [rule])


class PollDispatchTests(PublisherFixture):
    def test_twenty_four_consumers_do_not_share_a_lifetime_command_allowance(
        self,
    ) -> None:
        clients = []

        class BudgetClient:
            def __init__(self) -> None:
                self.calls = 0
                clients.append(self)

            def artifact(self, _run: str, _kind: str) -> None:
                self.calls += 2  # Native artifact list + bounded ZIP read.
                if self.calls > 128:
                    raise publisher.FleetPublishLookupError("command_budget")

        def previous_reads(client: publisher.GitHub, _repo: str, _entries: Any) -> None:
            # Exercise the genuine facade/auth scope, with a synthetic native
            # command counter. Source authority is covered by the other suites.
            for _ in range(4):
                client.record("101", "intent")

        with (
            patch.object(publisher, "GitHubClient", BudgetClient),
            patch.dict(os.environ, {"FLEET_CENTRAL_TOKEN": "fake-central"}),
        ):
            root = publisher.GitHub()
            metadata = [
                {
                    "full_name": f"jbaruch/fixture-{i}",
                    "archived": False,
                    "disabled": False,
                }
                for i in range(24)
            ]
            with (
                patch.object(root, "pages", return_value=metadata),
                patch.object(publisher.GitHub, "optional_marker", return_value={}),
                patch.object(
                    poller, "default_tip", return_value=("refs/heads/main", SOURCE)
                ),
                patch.object(poller, "tree", return_value=(TREE, {})),
                patch.object(poller, "enrollment", return_value="jbaruch/fixture"),
                patch.object(poller, "previous", side_effect=previous_reads),
                patch.object(poller, "pending_sources", return_value=[SOURCE]),
                patch.object(
                    poller, "gate_source", return_value=self.github.source_merged_at
                ),
                patch.object(poller, "dispatch_deferred", return_value=False),
            ):
                selected, held = poller.targets(root)
        self.assertEqual(len(selected), 24)
        self.assertEqual(held, [])
        self.assertEqual(len(clients), 25)
        self.assertEqual([client.calls for client in clients], [0] + [8] * 24)

    def test_poll_checks_cheap_source_gates_before_dispatching(self) -> None:
        self.github.native_reviews[0]["state"] = "CHANGES_REQUESTED"
        selected, held = poller.targets(self.github)
        self.assertEqual(selected, [])
        self.assertEqual(held, [REPO])
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_unmerged_native_source_is_held_before_dispatch(self) -> None:
        original = self.github.api

        def native(path: str, *args: Any, **kwargs: Any) -> Any:
            value = original(path, *args, **kwargs)
            if path == f"repos/{REPO}/pulls/7":
                assert isinstance(value, dict)
                value["merged"] = False
            return value

        with patch.object(self.github, "api", side_effect=native):
            selected, held = poller.targets(self.github)
        self.assertEqual(selected, [])
        self.assertEqual(held, [REPO])
        self.assertFalse(self.cli.called)
        self.assertFalse(any("/dispatches" in route for route, _ in self.github.calls))

    def test_all_inflight_states_suppress_duplicate_source_dispatch(self) -> None:
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            self.github.other_runs["101"] = {"status": status, "conclusion": None}
            with self.subTest(status=status):
                self.assertEqual(poller.targets(self.github), ([], []))

    def test_recent_failed_source_is_backed_off_then_only_reconsidered(self) -> None:
        self.github.other_runs["101"] = {"conclusion": "failure"}
        with patch.object(
            poller,
            "current_time",
            return_value=datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc),
        ):
            self.assertEqual(poller.targets(self.github), ([], []))
        with patch.object(
            poller,
            "current_time",
            return_value=datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc),
        ):
            selected, held = poller.targets(self.github)
            self.assertEqual(held, [])
            self.assertEqual(selected[0]["source_sha"], SOURCE)
        self.assertNotIn("smart-publish.sh", self.helpers)

    def test_each_consumer_gets_a_new_bounded_record_client(self) -> None:
        root = publisher.GitHub()
        root._record_client._commands = 128
        first, second = root.fresh_consumer(), root.fresh_consumer()
        self.assertIsNot(first._record_client, root._record_client)
        self.assertIsNot(first._record_client, second._record_client)
        self.assertEqual(first._record_client._commands, 0)
        self.assertEqual(second._record_client._commands, 0)

    def test_dispatch_lookup_uses_native_merge_time_window(self) -> None:
        original = self.github.pages
        with patch.object(self.github, "pages", side_effect=original) as pages:
            selected, held = poller.targets(self.github)
        self.assertEqual(held, [])
        self.assertEqual(selected[0]["source_sha"], SOURCE)
        catalogs = [
            call.args[0]
            for call in pages.call_args_list
            if "/workflows/fleet-publish.yml/runs?" in call.args[0]
        ]
        self.assertTrue(catalogs)
        self.assertTrue(
            all(
                parse_qs(urlsplit(route).query)["created"]
                == [">=" + self.github.source_merged_at]
                for route in catalogs
            )
        )

    def test_dispatch_queued_before_source_merge_cannot_publish(self) -> None:
        self.github.other_runs["101"] = {"created_at": "2026-10-27T01:01:59Z"}
        with self.assertRaises(publisher.PublishRefused):
            self.prepare()
        self.assertFalse((self.work / "intent.json").exists())
        self.assertFalse(self.cli.called)

    def test_same_native_second_boundary_remains_included(self) -> None:
        self.github.other_runs["101"] = {"created_at": self.github.source_merged_at}
        self.prepare()
        self.assertTrue((self.work / "intent.json").is_file())


if __name__ == "__main__":
    unittest.main()
