"""Real protocol/ArchiveReader composition and poll-only immutable read dedup."""

from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fleet_publish as publisher  # noqa: E402
import fleet_publish_targets as poller  # noqa: E402
import fleet_publish_producer as producer  # noqa: E402
from fleet_publish_archive import make_intent, make_terminal, ref_name  # noqa: E402
import test_fleet_publish_archive_write as fixture  # noqa: E402

CENTRAL = "repos/jbaruch/coding-policy"


class NativeCentral(fixture.Git):
    def __init__(self, count):
        super().__init__()
        self.reads = Counter()
        self.artifacts = {}
        self.runs = {}
        self.pins = {
            name: f"{index + 1000:040x}"
            for index, name in enumerate(producer.PRODUCER_PATHS, 1)
        }
        self.pins[producer.PRODUCER_OWNER_PATH] = producer.local_owner_blob()
        self.protocol = {}
        parents = {""}
        for path in producer.PRODUCER_PATHS:
            parent = path.rpartition("/")[0]
            while parent:
                parents.add(parent)
                parent = parent.rpartition("/")[0]
        directories = {
            name: f"{index + 2000:040x}" for index, name in enumerate(sorted(parents))
        }
        for directory, sha in directories.items():
            entries = [
                (path.rsplit("/", 1)[-1], child, "tree", "040000")
                for path, child in directories.items()
                if path and path.rpartition("/")[0] == directory
            ]
            entries += [
                (path.rsplit("/", 1)[-1], blob, "blob", "100644")
                for path, blob in self.pins.items()
                if path.rpartition("/")[0] == directory
            ]
            self.protocol[f"{CENTRAL}/git/trees/{sha}"] = {
                "sha": sha,
                "truncated": False,
                "tree": [
                    {"path": name, "sha": child, "type": kind, "mode": mode}
                    for name, child, kind, mode in entries
                ],
            }
        for sha in (fixture.FLOW, fixture.FINAL):
            self.protocol[f"{CENTRAL}/git/commits/{sha}"] = {
                "sha": sha,
                "tree": {"sha": directories[""]},
            }
            self.protocol[f"{CENTRAL}/compare/{sha}...{fixture.FINAL}"] = {
                "status": "identical" if sha == fixture.FINAL else "ahead",
                "base_commit": {"sha": sha},
                "merge_base_commit": {"sha": sha},
            }
        self.receipts = []
        for index in range(count):
            run = str(100 + index)
            intent = {
                **fixture.RECORD,
                "consumer_repository": f"jbaruch/fixture-{index}",
                "run_id": run,
                "origin_run_id": run,
            }
            receipt = {
                **self.records["receipt"],
                **intent,
                "state": "confirmed",
                "publish_outcome": "success",
                "publish_exit_code": 0,
                "moderation": "pass",
                "bookkeeping_sha": fixture.CLAIM,
            }
            native = {
                **self.run,
                "id": int(run),
                "display_title": f"fleet-publish-v1:{intent['consumer_repository']}:{fixture.SOURCE}",
            }
            jobs = [{**job, "run_id": int(run)} for job in self.jobs]
            envelope = make_intent(intent, fixture.CLAIM)
            terminal = make_terminal(
                envelope,
                receipt,
                native,
                native,
                jobs,
                finalizer={
                    "workflow_path": ".github/workflows/fleet-publish-finalize.yml",
                    "workflow_sha": fixture.FINAL,
                    "run_id": "201",
                    "run_attempt": 1,
                },
            )
            for ref, value in (
                (
                    ref_name(
                        "source",
                        repository=intent["consumer_repository"],
                        source_sha=fixture.SOURCE,
                    ),
                    envelope,
                ),
                (ref_name("intent", run_id=run), envelope),
                (ref_name("terminal", run_id=run), terminal),
            ):
                fixture.writer.create_only(
                    self, ref, value, lambda ref=ref: self.lookup(ref)
                )
            self.artifacts[(run, "intent")] = intent
            self.artifacts[(run, "receipt")] = receipt
            self.runs[run] = (native, jobs)
            self.receipts.append(receipt)
        self.calls.clear()

    def native_json(self, route):
        self.reads[route] += 1
        if route in self.protocol:
            return deepcopy(self.protocol[route])
        if "/actions/runs/" in route:
            run = route.split("/actions/runs/")[1].split("/")[0]
            native, jobs = self.runs[run]
            return (
                {"total_count": len(jobs), "jobs": deepcopy(jobs)}
                if "/jobs?" in route
                else deepcopy(native)
            )
        return super().native_json(route)

    def json(self, route):
        return self.native_json(route)

    def artifact_evidence(self, run, kind):
        self.reads[f"ARTIFACT-LIST:{run}:{kind}"] += 1
        self.reads[f"ARTIFACT-ZIP:{run}:{kind}"] += 1
        return deepcopy(self.artifacts[(run, kind)])


class CountedClient:
    def __init__(self, native, cache):
        self.native, self.cache = native, cache

    def json(self, route):
        self.cache.request()
        return self.native.json(route)

    def native_json(self, route):
        self.cache.request()
        return self.native.native_json(route)

    def artifact_evidence(self, run, kind):
        self.cache.request()
        self.cache.request()
        return self.native.artifact_evidence(run, kind)


class PollCacheTests(unittest.TestCase):
    def poll(self, count, *, cached):
        native = NativeCentral(count)
        receipts = {row["consumer_repository"]: row for row in native.receipts}
        selected_receipt = None

        def proof(consumer, repo, entries):
            nonlocal selected_receipt
            selected_receipt = receipts[repo]
            run = selected_receipt["run_id"]
            intent = consumer.record(run, "intent")
            consumer.attempt_evidence(intent, successful=False)
            receipt = consumer.record(run, "receipt")
            consumer.attempt_evidence(receipt, successful=True)
            return receipt

        def native_api(github, route, *args, **kwargs):
            if route == f"{CENTRAL}/actions/runs/777":
                cache = getattr(github, "_poll_cache", None)
                if cache is not None:
                    cache.request()
                native.reads[route] += 1
                return {
                    "id": 777,
                    "run_number": 19,
                    "created_at": "2019-01-02T00:00:00Z",
                    "run_attempt": 1,
                    "head_sha": fixture.FLOW,
                    "path": publisher.CENTRAL_WORKFLOW,
                    "event": "schedule",
                    "head_branch": "main",
                    "repository": {"full_name": publisher.CENTRAL_REPOSITORY},
                    "head_repository": {"full_name": publisher.CENTRAL_REPOSITORY},
                }
            return {"sha": fixture.CLAIM, "parents": [{"sha": fixture.SOURCE}]}

        with (
            patch.object(publisher, "GitHubClient", return_value=native),
            patch.object(
                publisher,
                "PollGitHubClient",
                side_effect=lambda cache: CountedClient(native, cache),
            ),
            patch.object(
                producer,
                "TRUSTED_PRODUCERS",
                (
                    {
                        name: sha
                        for name, sha in native.pins.items()
                        if name != producer.PRODUCER_OWNER_PATH
                    },
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "FLEET_CENTRAL_TOKEN": "fake-central",
                    "GITHUB_REPOSITORY": publisher.CENTRAL_REPOSITORY,
                    "GITHUB_EVENT_NAME": "schedule",
                    "GITHUB_REF": "refs/heads/main",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_RUN_ID": "777",
                    "GITHUB_SHA": fixture.FLOW,
                    "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish.yml@refs/heads/main",
                },
            ),
        ):
            root = publisher.GitHub()
            session = (
                patch.object(root, "poll_immutable_reads", side_effect=nullcontext)
                if not cached
                else nullcontext()
            )
            with (
                session,
                patch.object(publisher.GitHub, "api", native_api),
                patch.object(
                    root,
                    "pages",
                    return_value=[
                        {"full_name": row["consumer_repository"]}
                        for row in native.receipts
                    ],
                ),
                patch.object(publisher.GitHub, "optional_marker", return_value={}),
                patch.object(
                    poller,
                    "default_tip",
                    return_value=("refs/heads/main", fixture.CLAIM),
                ),
                patch.object(poller, "tree", return_value=(fixture.TREE, {})),
                patch.object(poller, "enrollment", return_value="jbaruch/fixture"),
                patch.object(poller, "previous", side_effect=proof),
                patch.object(
                    poller,
                    "pending_sources",
                    side_effect=AssertionError(
                        "An exact fully verified confirmed idle tip must not be proved twice"
                    ),
                ),
            ):
                self.assertEqual(
                    poller.targets(root),
                    (
                        [],
                        [],
                        sorted(row["consumer_repository"] for row in native.receipts)[
                            2:
                        ],
                    ),
                )
            self.assertIsNone(getattr(root, "_poll_cache", None))
        self.assertIsNotNone(selected_receipt)
        return native.reads

    def test_real_protocol_single_and_twenty_four_idle_consumers_reduce_only_immutable_reads(
        self,
    ):
        for count in (1, 24):
            with self.subTest(consumers=count):
                before = self.poll(count, cached=False)
                after = self.poll(count, cached=True)
                self.assertLess(sum(after.values()), sum(before.values()))

                def mutable(route):
                    return (
                        "/git/ref/" in route
                        or "/actions/runs/" in route
                        or route.startswith("ARTIFACT")
                        or route == CENTRAL
                    )

                self.assertEqual(
                    {k: v for k, v in before.items() if mutable(k)},
                    {k: v for k, v in after.items() if mutable(k)},
                )
                print(
                    f"poll-read-measurement consumers={count} before={sum(before.values())} after={sum(after.values())}"
                )

    def test_native_request_cap_spans_clients_binary_pages_and_two_dispatch_slots(self):
        cache = publisher.PollImmutableCache()
        calls = []

        def native_read(route, maximum, **kwargs):
            calls.append((route, maximum, kwargs))
            return b"{}"

        with patch.object(publisher.GitHubClient, "_read", side_effect=native_read):
            first, second = (
                publisher.PollGitHubClient(cache),
                publisher.PollGitHubClient(cache),
            )
            for index in range(197):
                (first if index % 2 else second).native_json(
                    f"{CENTRAL}/actions/runs/101/attempts/1/jobs?per_page=100&page={index + 1}"
                )
            second._read(f"{CENTRAL}/actions/artifacts/101/zip", 256 * 1024)
            with self.assertRaises(publisher.PollBudgetExhausted):
                first.native_json(f"{CENTRAL}/actions/runs/101")
        self.assertEqual(len(calls), 198)
        cache.request(dispatch=True)
        cache.request(dispatch=True)
        with self.assertRaises(publisher.PollBudgetExhausted):
            cache.request(dispatch=True)
        self.assertEqual(cache.requests, 200)

    def test_verified_native_schedule_binding_consumes_one_request_and_refuses_unknown_context(
        self,
    ):
        from types import SimpleNamespace

        environment = {
            **fixture.ENV,
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_RUN_ID": "101",
            "GITHUB_SHA": fixture.FLOW,
            "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish.yml@refs/heads/main",
            "FLEET_CENTRAL_TOKEN": "fake-central",
        }
        raw = {
            "id": 101,
            "run_number": 7,
            "created_at": "2019-01-02T03:00:00Z",
            "run_attempt": 1,
            "head_sha": fixture.FLOW,
            "path": publisher.CENTRAL_WORKFLOW,
            "event": "schedule",
            "head_branch": "main",
            "repository": {"full_name": publisher.CENTRAL_REPOSITORY},
            "head_repository": {"full_name": publisher.CENTRAL_REPOSITORY},
        }
        with patch.dict(os.environ, environment):
            github = publisher.GitHub()
            with github.poll_immutable_reads():
                with patch.object(
                    publisher.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout=publisher.encoded(raw), stderr=b""
                    ),
                ) as transport:
                    self.assertEqual(poller.scheduled_slot(github), 1718220)
                for number, created, expected in (
                    (10, "2019-01-02T03:00:00Z", 1718220),
                    (14, "2019-01-02T03:15:00Z", 1718221),
                ):
                    with patch.object(
                        publisher.subprocess,
                        "run",
                        return_value=SimpleNamespace(
                            returncode=0,
                            stdout=publisher.encoded(
                                {**raw, "run_number": number, "created_at": created}
                            ),
                            stderr=b"",
                        ),
                    ):
                        self.assertEqual(poller.scheduled_slot(github), expected)
                self.assertEqual(transport.call_count, 1)
                assert github._poll_cache is not None
                self.assertEqual(github._poll_cache.requests, 3)
                for key, value in (
                    ("run_attempt", 2),
                    ("event", "workflow_dispatch"),
                    ("head_sha", "a" * 40),
                    ("run_number", None),
                    ("created_at", None),
                    ("head_branch", "other"),
                ):
                    with (
                        patch.object(
                            publisher.subprocess,
                            "run",
                            return_value=SimpleNamespace(
                                returncode=0,
                                stdout=publisher.encoded({**raw, key: value}),
                                stderr=b"",
                            ),
                        ),
                        self.assertRaises(publisher.PublishRefused),
                    ):
                        poller.scheduled_slot(github)
            with (
                patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}),
                patch.object(
                    publisher.subprocess,
                    "run",
                    side_effect=AssertionError("No read after invalid native context"),
                ),
                self.assertRaises(publisher.PublishRefused),
            ):
                poller.scheduled_slot(github)

    def test_static_twenty_four_consumers_each_get_first_slot_even_when_second_exhausts_budget(
        self,
    ):
        first_slots = []
        repositories = [{"full_name": f"jbaruch/fixture-{i:02d}"} for i in range(24)]
        for run_number in range(24):
            root = publisher.GitHub()
            attempts = []

            def proof(consumer, repo, _entries):
                attempts.append(repo)
                if len(attempts) == 1:
                    first_slots.append(repo)
                for _ in range(197):
                    consumer._poll_cache.request()
                return None

            with (
                patch.object(root, "pages", return_value=repositories),
                patch.object(poller, "scheduled_slot", return_value=run_number),
                patch.object(publisher.GitHub, "optional_marker", return_value={}),
                patch.object(
                    poller,
                    "default_tip",
                    return_value=("refs/heads/main", fixture.CLAIM),
                ),
                patch.object(poller, "tree", return_value=(fixture.TREE, {})),
                patch.object(poller, "enrollment", return_value="jbaruch/fixture"),
                patch.object(poller, "previous", side_effect=proof),
                patch.object(poller, "pending_sources", return_value=[fixture.SOURCE]),
                patch.object(
                    poller, "gate_source", return_value="2026-10-27T01:00:00Z"
                ),
                patch.object(poller, "dispatch_deferred", return_value=False),
            ):
                selected, held, deferred = poller.targets(root)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(selected), 1)
            self.assertEqual(held, [])
            self.assertEqual(len(deferred), 23)
        self.assertEqual(set(first_slots), {r["full_name"] for r in repositories})
        self.assertEqual(len(set(first_slots)), 24)

    def test_cache_has_fixed_memory_bounds_and_returns_independent_values(self):
        cache = publisher.PollImmutableCache()

        class Client:
            def native_json(self, route):
                return {"route": route}

        client = Client()
        route = f"{CENTRAL}/git/trees/{'a' * 40}"
        value = cache.read(client, "native_json", route)
        self.assertIsInstance(value, dict)
        assert isinstance(value, dict)
        value["route"] = "changed"
        self.assertEqual(cache.read(client, "native_json", route), {"route": route})
        for index in range(300):
            cache.read(client, "native_json", f"{CENTRAL}/git/trees/{index:040x}")
        self.assertLessEqual(len(cache.values), 256)
        self.assertLessEqual(cache.bytes, 8 * 1024 * 1024)

    def test_failed_immutable_read_is_not_cached_and_byte_budget_evicts(self):
        cache = publisher.PollImmutableCache()

        class Client:
            def __init__(self):
                self.calls = 0

            def native_json(self, _route):
                self.calls += 1
                if self.calls == 1:
                    raise publisher.FleetPublishLookupError(
                        "native_missing", http_status=404
                    )
                return {"data": "x" * (1024 * 1024 - 100)}

        client = Client()
        route = f"{CENTRAL}/git/trees/{'a' * 40}"
        with self.assertRaises(publisher.FleetPublishLookupError):
            cache.read(client, "native_json", route)
        self.assertEqual(len(cache.values), 0)
        cache.read(client, "native_json", route)
        self.assertEqual(client.calls, 2)
        for index in range(10):
            cache.read(client, "native_json", f"{CENTRAL}/git/trees/{index:040x}")
        self.assertLessEqual(cache.bytes, 8 * 1024 * 1024)
        self.assertLess(len(cache.values), 11)

    def test_ref_mutation_and_native_rerun_are_never_cached(self):
        cache = publisher.PollImmutableCache()

        class Client:
            def __init__(self):
                self.value = 1

            def native_json(self, _route):
                return {"value": self.value}

        client = Client()
        for route in (
            f"{CENTRAL}/git/ref/tags/fleet-publish-v1/runs/101/hold",
            f"{CENTRAL}/actions/runs/101",
            f"{CENTRAL}/git/ref/heads/main",
            CENTRAL,
        ):
            self.assertEqual(cache.read(client, "native_json", route), {"value": 1})
            client.value = 2
            self.assertEqual(cache.read(client, "native_json", route), {"value": 2})
            client.value = 1
        self.assertEqual(len(cache.values), 0)


if __name__ == "__main__":
    unittest.main()
