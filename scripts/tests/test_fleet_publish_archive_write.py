"""Create-only actual Git-shaped evidence and native finalizer fixtures."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fleet_publish_archive_write as writer  # noqa: E402 - actual sibling under test.
from fleet_publish_archive import (  # noqa: E402 - same co-shipped owner.
    ArchiveReader,
    decode_archive,
    make_intent,
    positive_native,
    ref_name,
)

REPO = "jbaruch/nanoclaw-fixture"
SOURCE, TREE, FLOW, CLAIM, FINAL = (c * 40 for c in "abcde")
RECORD = {
    "schema_version": 1,
    "consumer_repository": REPO,
    "source_sha": SOURCE,
    "source_ref": "refs/heads/main",
    "source_tree_sha": TREE,
    "workflow_repository": "jbaruch/coding-policy",
    "workflow_sha": FLOW,
    "run_id": "101",
    "run_attempt": 1,
    "plugin_name": "jbaruch/fixture",
    "state": "claimed",
    "registry_baseline": "0.1.0",
    "version": "0.1.1",
    "publish_outcome": "not_started",
    "publish_exit_code": None,
    "landed_after_error": False,
    "moderation": "not_checked",
    "origin_run_id": "101",
    "origin_run_attempt": 1,
    "bookkeeping_sha": None,
}
ENV = {
    "GITHUB_REPOSITORY": "jbaruch/coding-policy",
    "GITHUB_EVENT_NAME": "workflow_run",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_RUN_ID": "201",
    "GITHUB_SHA": FINAL,
    "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish-finalize.yml@refs/heads/main",
}


def present(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


class Git:
    def __init__(self):
        self.calls = []
        self.refs, self.commits, self.trees, self.blobs = {}, {}, {}, {}
        self.after_create_error = False
        self.expired = False
        self.fail_write = False
        self.run = {
            "id": 101,
            "run_attempt": 1,
            "workflow_id": 71,
            "path": ".github/workflows/fleet-publish.yml",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": FLOW,
            "repository": {"full_name": "jbaruch/coding-policy"},
            "head_repository": {"full_name": "jbaruch/coding-policy"},
            "display_title": f"fleet-publish-v1:{REPO}:{SOURCE}",
            "created_at": "2026-10-27T01:00:00Z",
            "status": "completed",
            "conclusion": "success",
        }
        self.current = {
            **self.run,
            "id": 201,
            "path": writer.FINALIZER_WORKFLOW,
            "event": "workflow_run",
            "head_sha": FINAL,
            "status": "in_progress",
            "conclusion": None,
        }
        self.records = {
            "intent": deepcopy(RECORD),
            "receipt": {
                **RECORD,
                "state": "confirmed",
                "publish_outcome": "success",
                "publish_exit_code": 0,
                "moderation": "pass",
                "bookkeeping_sha": CLAIM,
            },
        }
        self.jobs = [
            {
                "id": 301,
                "name": "publish",
                "run_id": 101,
                "run_attempt": 1,
                "head_sha": FLOW,
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {
                        "number": 1,
                        "name": "Claim source atomically before publication",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 2,
                        "name": "Publish once or perform moderation readback only",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            }
        ]

    def api(self, route, payload=None):
        self.calls.append((route, deepcopy(payload)))
        prefix = "repos/jbaruch/coding-policy/git/"
        if payload is not None:
            if self.fail_write:
                raise writer.ArchiveWriteRefused("native write failed")
            if route == prefix + "blobs":
                import base64

                raw = base64.b64decode(payload["content"])
                sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                self.blobs[sha] = raw
            elif route == prefix + "trees":
                sha = hashlib.sha1(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                self.trees[sha] = payload["tree"]
            elif route == prefix + "commits":
                sha = hashlib.sha1(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                self.commits[sha] = payload
            elif route == prefix + "refs":
                if payload["ref"] in self.refs:
                    raise writer.ArchiveWriteRefused("create-only conflict")
                self.refs[payload["ref"]] = payload["sha"]
                if self.after_create_error:
                    raise OSError("native ACK lost")
                return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
            else:
                raise AssertionError(route)
            return {"sha": sha}
        if route == "repos/jbaruch/coding-policy/actions/runs/201":
            return deepcopy(self.current)
        if route in (
            "repos/jbaruch/coding-policy/actions/runs/101",
            "repos/jbaruch/coding-policy/actions/runs/101/attempts/1",
        ):
            return deepcopy(self.run)
        raise AssertionError(route)

    def json(self, route):
        return self.native_json(route)

    def native_json(self, route):
        from fleet_publish_github import FleetPublishLookupError

        prefix = "repos/jbaruch/coding-policy/git/"
        if route == "repos/jbaruch/coding-policy":
            return {
                "full_name": "jbaruch/coding-policy",
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
        if route == prefix + "ref/heads/main":
            return {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": FINAL},
            }
        if route.startswith(prefix + "ref/"):
            ref = "refs/" + route.removeprefix(prefix + "ref/")
            if ref not in self.refs:
                raise FleetPublishLookupError("native_missing", http_status=404)
            return {"ref": ref, "object": {"type": "commit", "sha": self.refs[ref]}}
        if route.startswith(prefix + "commits/"):
            sha = route.rsplit("/", 1)[1]
            commit = self.commits[sha]
            return {"sha": sha, "tree": {"sha": commit["tree"]}, "parents": []}
        if route.startswith(prefix + "trees/"):
            sha = route.rsplit("/", 1)[1]
            return {"sha": sha, "truncated": False, "tree": self.trees[sha]}
        if route.startswith(prefix + "blobs/"):
            import base64

            sha = route.rsplit("/", 1)[1]
            raw = self.blobs[sha]
            return {
                "sha": sha,
                "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
                "size": len(raw),
            }
        if "/actions/runs/" in route:
            if self.expired:
                raise FleetPublishLookupError("native_missing", http_status=404)
            if "/jobs?" in route:
                return {"total_count": len(self.jobs), "jobs": deepcopy(self.jobs)}
            return self.api(route)
        raise AssertionError(route)

    def artifact_evidence(self, run, kind):
        from fleet_publish_github import FleetPublishLookupError

        if self.expired:
            raise FleetPublishLookupError("native_missing", http_status=404)
        return self.live_record(run, kind)

    def pages(self, route, key):
        assert (
            route == "repos/jbaruch/coding-policy/actions/runs/101/attempts/1/jobs"
            and key == "jobs"
        )
        return deepcopy(self.jobs)

    def live_record(self, run, kind):
        assert run == "101"
        return deepcopy(self.records.get(kind))

    def lookup(self, ref):
        if ref not in self.refs:
            return None
        tree = self.trees[self.commits[self.refs[ref]]["tree"]]
        assert (
            len(tree) == 1
            and tree[0]["path"] == "record.json"
            and tree[0]["mode"] == "100644"
        )
        return decode_archive(self.blobs[tree[0]["sha"]])

    def source_claim(self, repo, source):
        return self.lookup(ref_name("source", repository=repo, source_sha=source))

    def run_intent(self, run):
        return self.lookup(ref_name("intent", run_id=run))

    def terminal(self, run):
        return self.lookup(ref_name("terminal", run_id=run))

    def run_hold(self, run):
        return self.lookup(ref_name("hold", run_id=run))


class ArchiveWriterTests(unittest.TestCase):
    def setUp(self):
        self.git = Git()
        self.protocol = patch.object(writer, "verify_workflow_protocol")
        self.protocol.start()
        self.addCleanup(self.protocol.stop)

    def claim(self):
        writer.persist_claim(self.git, self.git, deepcopy(RECORD), CLAIM)

    def test_source_and_run_refs_retain_same_exact_envelope_without_updates(self):
        self.claim()
        original = deepcopy(self.git.refs)
        expected = make_intent(RECORD, CLAIM)
        self.assertEqual(self.git.source_claim(REPO, SOURCE), expected)
        self.assertEqual(self.git.run_intent("101"), expected)
        count = len(self.git.calls)
        self.claim()
        self.assertEqual(self.git.refs, original)
        self.assertEqual(len(self.git.calls), count)
        self.assertTrue(
            all(
                route.startswith("repos/jbaruch/coding-policy/git/")
                for route, _ in self.git.calls
            )
        )
        self.assertTrue(all(not route.endswith("/main") for route, _ in self.git.calls))

    def test_different_original_is_refused_without_overwriting_any_record(self):
        self.claim()
        before, count = deepcopy(self.git.refs), len(self.git.calls)
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.persist_claim(
                self.git,
                self.git,
                {**RECORD, "run_id": "102", "origin_run_id": "102"},
                "f" * 40,
            )
        self.assertEqual(self.git.refs, before)
        self.assertEqual(len(self.git.calls), count)

    def test_recovery_creates_only_own_intent_and_preserves_original_index(self):
        self.claim()
        original = self.git.source_claim(REPO, SOURCE)
        writer.persist_claim(self.git, self.git, {**RECORD, "run_id": "102"}, "f" * 40)
        self.assertEqual(self.git.source_claim(REPO, SOURCE), original)
        self.assertEqual(
            present(self.git.run_intent("102"))["intent"]["origin_run_id"], "101"
        )

    def test_failed_write_or_missing_readback_never_infers_durable_claim(self):
        self.git.fail_write = True
        with self.assertRaises(writer.ArchiveWriteRefused):
            self.claim()
        self.assertEqual(self.git.refs, {})
        self.git.fail_write = False
        with (
            patch.object(self.git, "lookup", return_value=None),
            self.assertRaises(writer.ArchiveWriteRefused),
        ):
            self.claim()
        self.assertIsNone(self.git.run_intent("101"))

    def test_lost_create_ack_requires_exact_readback_without_retry(self):
        self.git.after_create_error = True
        self.claim()
        creates = [
            payload for route, payload in self.git.calls if route.endswith("/refs")
        ]
        self.assertEqual(len(creates), 2)
        self.assertEqual(len(self.git.refs), 2)

    def test_terminal_archive_has_native_completed_proof_and_exact_receipt(self):
        self.claim()
        result = writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertTrue(result)
        stored = present(self.git.terminal("101"))
        self.assertEqual(stored["receipt"], self.git.records["receipt"])
        self.assertTrue(positive_native(stored["native"]))
        self.assertEqual(stored["finalizer"]["run_id"], "201")

    def test_same_finalizer_rerun_reuses_first_attestation_without_replacing_provenance(
        self,
    ):
        self.claim()
        writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        original = deepcopy(present(self.git.terminal("101")))
        self.git.current["run_attempt"] = 2
        before = len(self.git.calls)
        self.assertTrue(
            writer.finalize(
                self.git,
                self.git,
                {**ENV, "GITHUB_RUN_ATTEMPT": "2"},
                {"workflow_run": {"id": 101}},
            )
        )
        self.assertEqual(self.git.terminal("101"), original)
        self.assertEqual(original["finalizer"]["run_attempt"], 1)
        self.assertTrue(
            all(payload is None for _route, payload in self.git.calls[before:])
        )
        self.git.records["receipt"]["moderation"] = "fail"
        self.git.records["receipt"]["state"] = "published"
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(
                self.git,
                self.git,
                {**ENV, "GITHUB_RUN_ATTEMPT": "2"},
                {"workflow_run": {"id": 101}},
            )
        self.assertEqual(self.git.terminal("101"), original)

    def test_transient_failed_finalization_can_be_retried_without_publish_or_replacing_claim(
        self,
    ):
        self.claim()
        self.git.fail_write = True
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertIsNone(self.git.terminal("101"))
        self.git.fail_write = False
        self.git.current["run_attempt"] = 2
        self.assertTrue(
            writer.finalize(
                self.git,
                self.git,
                {**ENV, "GITHUB_RUN_ATTEMPT": "2"},
                {"workflow_run": {"id": 101}},
            )
        )
        self.assertEqual(
            present(self.git.terminal("101"))["finalizer"]["run_attempt"], 2
        )
        self.assertEqual(
            self.git.source_claim(REPO, SOURCE), make_intent(RECORD, CLAIM)
        )

    def test_failed_whole_run_preserves_confirmed_bytes_but_is_not_positive(self):
        self.claim()
        self.git.run["conclusion"] = "failure"
        self.assertTrue(
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        )
        stored = present(self.git.terminal("101"))
        self.assertEqual(stored["receipt"]["state"], "confirmed")
        self.assertFalse(positive_native(stored["native"]))

    def test_observed_unsupported_rerun_retains_permanent_hold_without_changing_terminal(
        self,
    ):
        self.claim()
        writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        original = deepcopy(present(self.git.terminal("101")))
        self.git.run["run_attempt"] = 2
        writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        held = deepcopy(present(self.git.run_hold("101")))
        self.assertEqual(held["observed_attempt"], 2)
        self.git.run["run_attempt"] = 3
        before = len(self.git.calls)
        writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertEqual(self.git.run_hold("101"), held)
        self.assertEqual(self.git.terminal("101"), original)
        self.assertTrue(
            all(payload is None for _route, payload in self.git.calls[before:])
        )
        self.git.run["run_attempt"] = 1
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})

    def test_missing_receipt_archives_unknown_without_fabricating_result(self):
        self.claim()
        self.git.records.pop("receipt")
        self.git.run["conclusion"] = "cancelled"
        self.assertTrue(
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        )
        self.assertIsNone(present(self.git.terminal("101"))["receipt"])

    def test_successful_native_upload_with_temporarily_missing_receipt_waits_for_backfill(
        self,
    ):
        self.claim()
        original = self.git.records.pop("receipt")
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertIsNone(self.git.terminal("101"))
        self.git.run["conclusion"] = "failure"
        self.git.jobs[0]["steps"].append(
            {
                "number": 3,
                "name": "Persist terminal evidence even after failed/uncertain publication",
                "status": "completed",
                "conclusion": "success",
            }
        )
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertIsNone(self.git.terminal("101"))
        self.git.records["receipt"] = original
        self.git.current["run_attempt"] = 2
        self.assertTrue(
            writer.finalize(
                self.git,
                self.git,
                {**ENV, "GITHUB_RUN_ATTEMPT": "2"},
                {"workflow_run": {"id": 101}},
            )
        )
        saved = present(self.git.terminal("101"))
        self.assertEqual(saved["receipt"], original)
        self.assertFalse(positive_native(saved["native"]))

    def test_missing_intent_never_reconstructs_a_claim_from_source_or_event(self):
        self.assertFalse(
            writer.finalize(
                self.git, self.git, ENV, {"workflow_run": {"id": 101, "intent": RECORD}}
            )
        )
        self.assertEqual(self.git.refs, {})

    def test_live_artifact_conflict_and_nonterminal_native_run_refuse(self):
        self.claim()
        self.git.records["intent"]["source_sha"] = "f" * 40
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertIsNone(self.git.terminal("101"))
        self.git.run["status"] = "in_progress"
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})

    def test_wrong_runtime_and_wrong_current_native_identity_refuse_before_write(self):
        for changes in (
            {"GITHUB_RUN_ATTEMPT": "0"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REPOSITORY": "other/private"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(writer.ArchiveWriteRefused),
            ):
                writer.finalize(
                    self.git,
                    self.git,
                    {**ENV, **changes},
                    {"workflow_run": {"id": 101}},
                )
        self.assertEqual(self.git.calls, [])
        self.git.current["head_sha"] = "f" * 40
        with self.assertRaises(writer.ArchiveWriteRefused):
            writer.finalize(self.git, self.git, ENV, {"workflow_run": {"id": 101}})
        self.assertEqual(self.git.refs, {})

    def test_composed_writer_sharedreader_survives_native_expiry_but_preserves_rerun_hold(
        self,
    ):
        from fleet_publish_archive import ArchiveError

        with patch("fleet_publish_producer.verify_workflow_protocol"):
            reader = ArchiveReader(self.git)
            writer.persist_claim(self.git, reader, RECORD, CLAIM)
            self.assertTrue(
                writer.finalize(self.git, reader, ENV, {"workflow_run": {"id": 101}})
            )
            self.git.expired = True
            saved = ArchiveReader(self.git)
            receipt = saved.record("101", "receipt")
            self.assertEqual(receipt, self.git.records["receipt"])
            proof = saved.verify_record_attempt(receipt, successful=True)
            self.assertTrue(proof["archived"])
            self.assertTrue(positive_native(proof))
            self.git.expired = False
            self.git.run["run_attempt"] = 2
            writer.finalize(
                self.git, ArchiveReader(self.git), ENV, {"workflow_run": {"id": 101}}
            )
            self.git.expired = True
            with self.assertRaises(ArchiveError):
                ArchiveReader(self.git).record("101", "receipt")
            with self.assertRaises(ArchiveError):
                ArchiveReader(self.git).verify_record_attempt(receipt, successful=True)

    def test_actual_cli_refuses_runtime_and_redacts_native_gh_failure(self):
        with tempfile.TemporaryDirectory(prefix="archive-cli-") as temporary:
            root = Path(temporary)
            event = root / "event.json"
            event.write_text('{"workflow_run":{"id":101}}')
            calls = root / "calls"
            gh = root / "gh"
            gh.write_text(
                f"#!{sys.executable}\n"
                + "import os,sys\nfrom pathlib import Path\nPath(os.environ['FIXTURE_CALLS']).write_text(repr(sys.argv))\nprint('PRIVATE-native-payload',file=sys.stderr)\nsys.exit(1)\n"
            )
            gh.chmod(0o700)
            env = {
                **ENV,
                "PATH": str(root),
                "GITHUB_EVENT_PATH": str(event),
                "FIXTURE_CALLS": str(calls),
                "FLEET_CENTRAL_TOKEN": "fixture-not-a-real-credential",
            }
            for changes, native_called in (
                ({"GITHUB_REPOSITORY": "other/PRIVATE"}, False),
                ({}, True),
            ):
                with self.subTest(changes=changes):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "scripts/fleet_publish_archive_write.py"),
                        ],
                        env={**env, **changes},
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b"")
                    self.assertIn(b"never replay publication", result.stderr)
                    self.assertNotIn(b"PRIVATE", result.stderr)
                    self.assertEqual(calls.exists(), native_called)
            self.assertNotIn("fixture-not-a-real-credential", calls.read_text())

    def test_finalizer_workflow_is_separate_no_publish_credentials_or_checkout(self):
        text = (ROOT / ".github/workflows/fleet-publish-finalize.yml").read_text()
        self.assertIn("workflow_run:", text)
        self.assertIn("workflows: ['Central fleet publish']", text)
        self.assertNotIn("name: Central fleet publish\n", text)
        for forbidden in (
            "TESSL_TOKEN",
            "FLEET_APP",
            "workflow_run.head_sha",
            "workflow_run.head_repository",
            "fleet_publish_workflow.py",
            "upload-artifact",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("actions: read", text)
        self.assertIn("contents: write", text)
        self.assertNotIn("conclusion == 'success'", text)


if __name__ == "__main__":
    unittest.main()
