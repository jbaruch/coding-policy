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
from pathlib import Path
from typing import Any
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

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
                    "skills": ["skills/fixture"],
                    "rules": ["rules/fixture.md"],
                }
            ),
            "README.md": b"# Fixture\n",
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
        self.checks = [{"status": "completed", "conclusion": "success"}]
        self.status = {"state": "pending", "statuses": []}
        self.history: list[object] = []
        self.records: dict[str, bytes] = {}
        self.run_conclusion = "success"
        self.run_status = "completed"
        self.current_attempt = 1
        self.refuse_push = False
        self.created_tree: list[dict[str, str]] = []
        self.metadata = {
            "full_name": REPO,
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

    def api(
        self,
        path: str,
        payload: object = None,
        *,
        binary: bool = False,
        method: str = "POST",
    ) -> object:
        self.calls.append((path, payload))
        if path == f"repos/{REPO}":
            return deepcopy(self.metadata)
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
        if path.startswith(central + "runs/101"):
            return {
                "id": 101,
                "run_attempt": self.current_attempt,
                "head_sha": WORKFLOW,
                "head_branch": "main",
                "path": publisher.CENTRAL_WORKFLOW,
                "event": "workflow_dispatch",
                "head_repository": {"full_name": publisher.CENTRAL_REPOSITORY},
                "status": self.run_status,
                "conclusion": self.run_conclusion,
            }
        if path.startswith(central + "artifacts/"):
            basename = path.split("/")[-2]
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(basename + ".json", self.records[basename])
            return stream.getvalue()
        raise AssertionError(f"Unexpected fake-native API {path}")

    def pages(self, route: str, key: str | None = None) -> list[Any]:
        path = route
        if path == f"repos/{REPO}/commits/{SOURCE}/pulls":
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
        raw = self.records.get(kind)
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
            return {
                "id": int(run),
                "run_attempt": 1,
                "head_sha": WORKFLOW,
                "head_branch": "main",
                "path": publisher.CENTRAL_WORKFLOW,
                "event": "workflow_dispatch",
                "head_repository": {"full_name": publisher.CENTRAL_REPOSITORY},
                "status": "completed",
                "conclusion": "success",
            }
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


if __name__ == "__main__":
    unittest.main()
