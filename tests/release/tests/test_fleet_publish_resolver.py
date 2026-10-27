"""Native-shaped immutable Git/run fixtures; no API, credentials or publication."""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
SPEC = importlib.util.spec_from_file_location(
    "fleet_publish_resolver", SOURCE / "fleet_publish_resolver.py"
)
assert SPEC is not None and SPEC.loader is not None
resolver: Any = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    SPEC.loader.exec_module(resolver)
producer: Any = sys.modules["fleet_publish_producer"]
github_owner: Any = sys.modules["fleet_publish_github"]
FleetPublishLookupError = github_owner.FleetPublishLookupError

REPO = "jbaruch/nanoclaw-construction"
CENTRAL = "jbaruch/coding-policy"
RUN = "123456789"
RECOVERY = "234567890"
OTHER = "345678901"


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def git_hash(kind, raw):
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()


class Fixture:
    """Construct true immutable blob/tree/commit hashes from fixed source bytes."""

    def __init__(self):
        self.routes: dict[str, Any] = {}
        self.records = {}
        self.calls = []
        self.files: dict[tuple[str, str], dict[str, bytes]] = {}
        self.pinset = {}
        definitions = {
            name: ("fixed reviewed fixture " + name).encode() for name in producer.PRODUCER_PATHS
        }
        self.workflow = self.commit(CENTRAL, [], definitions)
        for name, raw in definitions.items():
            self.pinset[name] = git_hash("blob", raw)
        self.central_tip = self.commit(
            CENTRAL, [self.workflow], {**definitions, "README.md": b"later central metadata"}
        )
        self.routes[f"repos/{CENTRAL}"] = {
            "full_name": CENTRAL,
            "default_branch": "main",
            "archived": False,
            "disabled": False,
        }
        self.routes[f"repos/{CENTRAL}/git/ref/heads/main"] = {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": self.central_tip},
        }
        self.routes[f"repos/{CENTRAL}/compare/{self.workflow}...{self.central_tip}"] = {
            "status": "ahead",
            "base_commit": {"sha": self.workflow},
            "merge_base_commit": {"sha": self.workflow},
        }
        workflow_metadata = {
            "id": 77,
            "path": ".github/workflows/fleet-publish.yml",
            "state": "active",
        }
        self.routes[f"repos/{CENTRAL}/actions/workflows/fleet-publish.yml"] = workflow_metadata
        initial = {
            ".github/fleet-publish-enabled": encoded({"schema_version": 1, "plugin_name": REPO}),
            ".tessl-plugin/plugin.json": encoded({"name": REPO, "version": "0.1.0"}),
            "CHANGELOG.md": b"# Changelog\n",
            "code.py": b"fixed source\n",
        }
        base = self.commit(REPO, [], {"README.md": b"base"})
        self.source = self.commit(REPO, [base], initial)
        self.intent = self.new_intent(RUN)
        self.claim = self.claim_commit(self.source, self.intent)
        self.tip = self.commit(
            REPO,
            [self.claim],
            {**self.files[REPO, self.claim], "future.py": b"later reviewed code"},
        )
        self.default(self.tip)
        self.routes[f"repos/{REPO}/commits/{self.source}/pulls?per_page=100&page=1"] = [
            {"number": 7, "merge_commit_sha": self.source, "merged_at": "2024-10-20T10:00:00Z"}
        ]
        self.routes[f"repos/{REPO}/pulls/7"] = {
            "number": 7,
            "merged": True,
            "merged_at": "2024-10-20T10:00:00Z",
            "state": "closed",
            "merge_commit_sha": self.source,
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "head": {"sha": "f" * 40, "repo": {"full_name": REPO}},
        }
        self.add_run(RUN, self.intent)
        self.records[RUN, "receipt"] = self.receipt(self.intent, self.claim)

    def tree(self, repo, entries):
        rows = []
        raw = bytearray()
        for name, (mode, kind, sha) in sorted(
            entries.items(), key=lambda pair: pair[0] + ("/" if pair[1][1] == "tree" else "")
        ):
            raw.extend((mode.lstrip("0") + " " + name + "\0").encode() + bytes.fromhex(sha))
            rows.append({"path": name, "mode": mode, "type": kind, "sha": sha})
        digest = git_hash("tree", bytes(raw))
        self.routes[f"repos/{repo}/git/trees/{digest}"] = {
            "sha": digest,
            "truncated": False,
            "tree": rows,
        }
        return digest

    def commit(self, repo, parents, files):
        def build(paths):
            entries = {}
            folders = {}
            for name, raw in paths.items():
                head, sep, tail = name.partition("/")
                if sep:
                    folders.setdefault(head, {})[tail] = raw
                else:
                    digest = git_hash("blob", raw)
                    self.routes[f"repos/{repo}/git/blobs/{digest}"] = {
                        "sha": digest,
                        "size": len(raw),
                        "encoding": "base64",
                        "content": base64.b64encode(raw).decode() + "\n",
                    }
                    entries[head] = ("100644", "blob", digest)
            for head, values in folders.items():
                entries[head] = ("040000", "tree", build(values))
            return self.tree(repo, entries)

        tree = build(files)
        raw = (
            "tree "
            + tree
            + "\n"
            + "".join("parent " + parent + "\n" for parent in parents)
            + "author Fixture <fixture@example.test> 1729418400 +0000\ncommitter Fixture <fixture@example.test> 1729418400 +0000\n\nfixed fixture\n"
        ).encode()
        digest = git_hash("commit", raw)
        self.routes[f"repos/{repo}/git/commits/{digest}"] = {
            "sha": digest,
            "tree": {"sha": tree},
            "parents": [{"sha": parent} for parent in parents],
        }
        self.files[repo, digest] = copy.deepcopy(files)
        return digest

    def new_intent(self, run, *, origin=None):
        tree = self.routes[f"repos/{REPO}/git/commits/{self.source}"]["tree"]["sha"]
        return {
            "schema_version": 1,
            "consumer_repository": REPO,
            "source_sha": self.source,
            "source_ref": "refs/heads/main",
            "source_tree_sha": tree,
            "workflow_repository": CENTRAL,
            "workflow_sha": self.workflow,
            "run_id": run,
            "run_attempt": 1,
            "plugin_name": REPO,
            "state": "claimed",
            "registry_baseline": "0.1.0",
            "version": "0.1.1",
            "publish_outcome": "not_started",
            "publish_exit_code": None,
            "landed_after_error": False,
            "moderation": "not_checked",
            "origin_run_id": origin or run,
            "origin_run_attempt": 1,
            "bookkeeping_sha": None,
        }

    def claim_commit(self, parent, intent):
        return self.commit(
            REPO, [parent], {**self.files[REPO, parent], resolver.STAMP: encoded(intent)}
        )

    def default(self, tip):
        self.tip = tip
        self.routes[f"repos/{REPO}"] = {
            "full_name": REPO,
            "default_branch": "main",
            "archived": False,
            "disabled": False,
        }
        self.routes[f"repos/{REPO}/git/ref/heads/main"] = {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": tip},
        }

    def add_run(self, run, intent=None, *, title=None, status="completed", conclusion="success"):
        native = {
            "id": int(run),
            "run_attempt": 1,
            "workflow_id": 77,
            "path": ".github/workflows/fleet-publish.yml",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": self.workflow,
            "repository": {"full_name": CENTRAL},
            "head_repository": {"full_name": CENTRAL},
            "status": status,
            "conclusion": conclusion,
            "display_title": title or f"fleet-publish-v1:{REPO}:{self.source}",
        }
        self.routes[f"repos/{CENTRAL}/actions/runs/{run}"] = copy.deepcopy(native)
        self.routes[f"repos/{CENTRAL}/actions/runs/{run}/attempts/1"] = copy.deepcopy(native)
        self.records[run, "intent"] = copy.deepcopy(intent)
        self.catalog()

    def catalog(self):
        rows = [
            copy.deepcopy(value)
            for route, value in self.routes.items()
            if route.startswith(f"repos/{CENTRAL}/actions/runs/")
            and "/attempts/" not in route
            and isinstance(value, dict)
            and "display_title" in value
        ]
        self.routes[
            f"repos/{CENTRAL}/actions/workflows/77/runs?event=workflow_dispatch&per_page=100&page=1"
        ] = {"total_count": len(rows), "workflow_runs": rows}

    def receipt(self, intent, sha, **changes):
        return {
            **intent,
            "state": "confirmed",
            "publish_outcome": "success",
            "publish_exit_code": 0,
            "moderation": "pass",
            "bookkeeping_sha": sha,
            **changes,
        }

    def json(self, route):
        self.calls.append(("json", route))
        if route not in self.routes:
            raise FleetPublishLookupError("fixture_unknown_route")
        value = self.routes[route]
        return copy.deepcopy(value() if callable(value) else value)

    def artifact(self, run, kind):
        self.calls.append(("artifact", run, kind))
        value = self.records.get((run, kind))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        pins = patch.object(producer, "TRUSTED_PRODUCERS", (self.fixture.pinset,))
        pins.start()
        self.addCleanup(pins.stop)

    def hold(self, fn, kind=None):
        with self.assertRaises(resolver.CentralPublishHold) as error:
            fn()
        if kind is not None:
            self.assertEqual(error.exception.kind, kind)
        self.assertNotIn("PRIVATE", str(error.exception))
        return error.exception

    def test_pending_selection_is_positive_identity_never_confirmation(self):
        f = self.fixture
        for suffix in ("", "/attempts/1"):
            f.routes[f"repos/{CENTRAL}/actions/runs/{RUN}{suffix}"].update(
                status="in_progress", conclusion=None
            )
        f.catalog()
        f.records[RUN, "receipt"] = None
        selected = resolver.select(f, REPO, f.source)
        self.assertEqual(
            selected,
            {"schema_version": 1, "database_id": int(RUN), "confirmed": False, "record": f.intent},
        )
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "pending")

    def test_confirmed_source_and_bookkeeping_are_ancestors_not_current_tip(self):
        f = self.fixture
        self.assertNotEqual(f.tip, f.source)
        self.assertNotEqual(f.tip, f.claim)
        self.assertEqual(resolver.resolve(f, REPO, f.source), f.records[RUN, "receipt"])

    def test_explicit_recovery_requires_original_native_publish_success(self):
        f = self.fixture
        f.records[RUN, "receipt"].update(state="published", moderation="pending")
        for suffix in ("", "/attempts/1"):
            f.routes[f"repos/{CENTRAL}/actions/runs/{RUN}{suffix}"]["conclusion"] = "failure"
        recovery = f.new_intent(RECOVERY, origin=RUN)
        claim = f.claim_commit(f.tip, recovery)
        f.default(claim)
        f.add_run(RECOVERY, recovery)
        f.records[RECOVERY, "receipt"] = f.receipt(recovery, claim)
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "unconfirmed")
        self.assertEqual(
            resolver.resolve(f, REPO, f.source, run_id=RECOVERY), f.records[RECOVERY, "receipt"]
        )
        f.records[RUN, "receipt"]["publish_exit_code"] = 1
        self.hold(lambda: resolver.resolve(f, REPO, f.source, run_id=RECOVERY))

    def test_default_never_selects_recovery_as_the_original(self):
        f = self.fixture
        recovery = f.new_intent(RECOVERY, origin=RUN)
        claim = f.claim_commit(f.tip, recovery)
        f.default(claim)
        f.add_run(RECOVERY, recovery)
        selected = resolver.select(f, REPO, f.source)
        self.assertEqual(selected["database_id"], int(RUN))

    def test_independent_central_original_detects_erased_claim(self):
        f = self.fixture
        duplicate = f.new_intent(OTHER)
        f.add_run(OTHER, duplicate)
        self.hold(lambda: resolver.select(f, REPO, f.source), "ambiguous")

    def test_duplicate_reintroduced_claim_birth_is_not_a_winner(self):
        f = self.fixture
        erased = dict(f.files[REPO, f.tip])
        del erased[resolver.STAMP]
        gone = f.commit(REPO, [f.tip], erased)
        reintroduced = f.claim_commit(gone, f.intent)
        f.default(reintroduced)
        self.hold(lambda: resolver.select(f, REPO, f.source), "claim_binding")

    def test_second_parent_source_is_not_default_first_parent(self):
        f = self.fixture
        base = f.commit(REPO, [], {"README.md": b"foreign first-parent"})
        tip = f.commit(REPO, [base, f.tip], f.files[REPO, f.tip])
        f.default(tip)
        self.hold(lambda: resolver.select(f, REPO, f.source), "source_binding")

    def test_wrong_tree_ref_plugin_merged_pr_and_bookkeeping_hold(self):
        f = self.fixture
        original = copy.deepcopy(f.records[RUN, "receipt"])
        for key, value in [
            ("source_tree_sha", "e" * 40),
            ("source_ref", "refs/heads/other"),
            ("plugin_name", "jbaruch/other"),
            ("bookkeeping_sha", f.tip),
            ("landed_after_error", True),
        ]:
            with self.subTest(key=key):
                f.records[RUN, "receipt"] = {**original, key: value}
                self.hold(lambda: resolver.resolve(f, REPO, f.source))
        f.records[RUN, "receipt"] = original
        f.routes[f"repos/{REPO}/pulls/7"]["merged"] = False
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "source_binding")

    def test_rerun_foreign_repo_event_branch_and_workflow_hold(self):
        f = self.fixture
        route = f"repos/{CENTRAL}/actions/runs/{RUN}"
        original = copy.deepcopy(f.routes[route])
        for key, value in [
            ("run_attempt", 2),
            ("event", "push"),
            ("head_branch", "PRIVATE"),
            ("workflow_id", 99),
            ("path", ".github/workflows/PRIVATE.yml"),
            ("head_repository", {"full_name": "other/repo"}),
        ]:
            with self.subTest(key=key):
                f.routes[route] = {**original, key: value}
                self.hold(lambda: resolver.select(f, REPO, f.source), "workflow_binding")
        f.routes[route] = original

    def test_receipt_missing_expired_unknown_and_native_failure_never_confirm(self):
        f = self.fixture
        original = copy.deepcopy(f.records[RUN, "receipt"])
        for value in (
            None,
            FleetPublishLookupError("expired_artifact"),
            {**original, "schema_version": 2},
            {**original, "state": "uncertain"},
            {**original, "moderation": "fail"},
        ):
            f.records[RUN, "receipt"] = value
            self.hold(lambda: resolver.resolve(f, REPO, f.source))
        f.records[RUN, "receipt"] = original
        f.routes[f"repos/{CENTRAL}/actions/runs/{RUN}/attempts/1"]["conclusion"] = "failure"
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "unconfirmed")

    def test_missing_intent_only_positive_skipped_claim_and_effect_excludes(self):
        f = self.fixture
        f.add_run(OTHER, None)
        jobsroute = f"repos/{CENTRAL}/actions/runs/{OTHER}/attempts/1/jobs?per_page=100&page=1"
        job = {
            "id": 9001,
            "name": "publish",
            "run_id": int(OTHER),
            "run_attempt": 1,
            "head_sha": f.workflow,
            "status": "completed",
            "conclusion": "failure",
            "steps": [
                {"name": resolver.CLAIM_STEP, "status": "completed", "conclusion": "skipped"},
                {"name": resolver.EFFECT_STEP, "status": "completed", "conclusion": "skipped"},
            ],
        }
        f.routes[jobsroute] = {"total_count": 1, "jobs": [job]}
        self.assertEqual(resolver.resolve(f, REPO, f.source), f.records[RUN, "receipt"])
        job["steps"][0]["conclusion"] = "failure"
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "claim_binding")

    def test_other_locator_requires_actual_trusted_protocol_before_exclusion(self):
        f = self.fixture
        f.add_run(OTHER, None, title="fleet-publish-v1:jbaruch/other:" + "e" * 40)
        self.assertEqual(resolver.resolve(f, REPO, f.source), f.records[RUN, "receipt"])
        self.assertNotIn(("artifact", OTHER, "intent"), f.calls)
        f.routes[f"repos/{CENTRAL}/actions/runs/{OTHER}"]["display_title"] = (
            "PRIVATE missing locator"
        )
        f.catalog()
        self.hold(lambda: resolver.resolve(f, REPO, f.source), "workflow_binding")

    def test_changed_duplicate_or_truncated_native_catalog_never_proves_uniqueness(self):
        f = self.fixture
        route = (
            f"repos/{CENTRAL}/actions/workflows/77/runs?event=workflow_dispatch&per_page=100&page=1"
        )
        page = copy.deepcopy(f.routes[route])
        f.routes[route] = {"total_count": 2, "workflow_runs": page["workflow_runs"]}
        self.hold(lambda: resolver.select(f, REPO, f.source), "unavailable")
        f.routes[route] = {"total_count": 2, "workflow_runs": page["workflow_runs"] * 2}
        self.hold(lambda: resolver.select(f, REPO, f.source), "unavailable")
        count = [0]

        def changing():
            count[0] += 1
            return page if count[0] == 1 else {"total_count": 0, "workflow_runs": []}

        f.routes[route] = changing
        self.hold(lambda: resolver.select(f, REPO, f.source), "unavailable")

    def test_unknown_producer_and_missing_claim_are_never_not_found(self):
        f = self.fixture
        with patch.object(producer, "TRUSTED_PRODUCERS", ()):
            self.hold(lambda: resolver.select(f, REPO, f.source), "unknown_producer")
        f.default(f.source)
        self.hold(lambda: resolver.select(f, REPO, f.source), "claim_binding")

    def test_stable_empty_verified_catalog_can_return_not_found_no_runid(self):
        f = self.fixture
        f.default(f.source)
        route = (
            f"repos/{CENTRAL}/actions/workflows/77/runs?event=workflow_dispatch&per_page=100&page=1"
        )
        f.routes[route] = {"total_count": 0, "workflow_runs": []}
        self.hold(lambda: resolver.select(f, REPO, f.source), "not_found")

    def test_cli_unknown_flags_and_bad_coordinates_never_echo_private_input(self):
        for args in (
            ["PRIVATE"],
            [REPO, self.fixture.source, "--PRIVATE-secret"],
            ["PRIVATE", "bad-sha"],
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = resolver.main(args)
            self.assertEqual(result, 1)
            self.assertNotIn("PRIVATE", stdout.getvalue() + stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["status"], "hold")

    def test_many_unrelated_runs_use_trusted_list_rows_not_per_run_downloads(self):
        f = self.fixture
        for identifier in range(70):
            f.add_run(
                str(400000000 + identifier),
                None,
                title="fleet-publish-v1:jbaruch/other:" + "e" * 40,
            )
        self.assertEqual(resolver.resolve(f, REPO, f.source), f.records[RUN, "receipt"])
        self.assertLess(len(f.calls), 100)
        self.assertFalse(any(call[0] == "artifact" and call[1] != RUN for call in f.calls))
        self.assertFalse(
            any(call[0] == "json" and "/actions/runs/4" in call[1] for call in f.calls)
        )

    def test_unrelated_latest_rerun_cannot_block_a_different_bound_source(self):
        f = self.fixture
        f.add_run(OTHER, None, title="fleet-publish-v1:jbaruch/other:" + "e" * 40)
        f.routes[f"repos/{CENTRAL}/actions/runs/{OTHER}"]["run_attempt"] = 2
        f.catalog()
        self.assertEqual(resolver.resolve(f, REPO, f.source), f.records[RUN, "receipt"])

    def test_empty_catalog_with_retained_claim_is_unknown_not_fresh_absence(self):
        f = self.fixture
        route = (
            f"repos/{CENTRAL}/actions/workflows/77/runs?event=workflow_dispatch&per_page=100&page=1"
        )
        f.routes[route] = {"total_count": 0, "workflow_runs": []}
        self.hold(lambda: resolver.select(f, REPO, f.source), "unavailable")

    def test_cli_confirmed_wrapper_contains_exact_trusted_record(self):
        f = self.fixture
        stdout = io.StringIO()
        with (
            patch.object(resolver, "GitHubClient", return_value=f),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(resolver.main([REPO, f.source, "--confirmed"]), 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "schema_version": 1,
                "database_id": int(RUN),
                "confirmed": True,
                "record": f.records[RUN, "receipt"],
            },
        )


if __name__ == "__main__":
    unittest.main()
