"""Central producer identity: main ancestry, frozen historical graphs; no live API."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
SPEC = importlib.util.spec_from_file_location(
    "fleet_publish_producer", SOURCE / "fleet_publish_producer.py"
)
assert SPEC is not None and SPEC.loader is not None
producer: Any = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    SPEC.loader.exec_module(producer)
github_owner: Any = sys.modules["fleet_publish_github"]
FleetPublishLookupError = github_owner.FleetPublishLookupError
# The installed 0.5.23 reader's graph, an immutable fixture.
GRAPH_0_5_23 = next(
    pins for pins in producer.HISTORICAL_PRODUCERS
    if pins[producer.PRODUCER_OWNER_PATH] == "ccbaeff9b2c8a5df3b09882861105adcf7d01d54"
)

CENTRAL = "jbaruch/coding-policy"
WORKFLOW = "e" * 40
TIP = "f" * 40
ROOT = "a" * 40
GITHUB = "b" * 40
WORKFLOWS = "c" * 40
SCRIPTS = "d" * 40


def entry(name, sha, *, directory=False):
    return {
        "path": name,
        "sha": sha,
        "type": "tree" if directory else "blob",
        "mode": "040000" if directory else "100644",
    }


class Fixture:
    def __init__(self, pins: dict[str, str] | None = None):
        self.pins = (pins if pins is not None else GRAPH_0_5_23).copy()
        self.routes = {
            f"repos/{CENTRAL}": {
                "full_name": CENTRAL,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            },
            f"repos/{CENTRAL}/git/ref/heads/main": {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": TIP},
            },
            f"repos/{CENTRAL}/compare/{WORKFLOW}...{TIP}": {
                "status": "ahead",
                "base_commit": {"sha": WORKFLOW},
                "merge_base_commit": {"sha": WORKFLOW},
            },
            f"repos/{CENTRAL}/git/commits/{WORKFLOW}": {
                "sha": WORKFLOW,
                "tree": {"sha": ROOT},
            },
        }
        parents = {""}
        for name in self.pins:
            parent = name.rpartition("/")[0]
            while parent:
                parents.add(parent)
                parent = parent.rpartition("/")[0]
        directories = {
            name: ROOT if name == "" else f"{index + 2000:040x}"
            for index, name in enumerate(sorted(parents))
        }
        for directory, sha in directories.items():
            values = [
                entry(name.rsplit("/", 1)[-1], child, directory=True)
                for name, child in directories.items()
                if name and name.rpartition("/")[0] == directory
            ] + [
                entry(name.rsplit("/", 1)[-1], blob)
                for name, blob in self.pins.items()
                if name.rpartition("/")[0] == directory
            ]
            self.routes[f"repos/{CENTRAL}/git/trees/{sha}"] = {
                "sha": sha,
                "truncated": False,
                "tree": values,
            }
        self.calls = []

    def json(self, route):
        self.calls.append(route)
        return copy.deepcopy(self.routes[route])


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def test_main_ancestor_is_accepted_without_reading_source(self):
        self.assertIsNone(producer.verify_workflow_protocol(self.fixture, WORKFLOW))
        self.assertNotEqual(WORKFLOW, TIP)
        self.assertFalse(
            any("/git/trees/" in r or "/git/commits/" in r for r in self.fixture.calls)
        )

    def test_non_main_foreign_or_diverged_native_producer_refuses(self):
        f = self.fixture
        route = f"repos/{CENTRAL}"
        original = copy.deepcopy(f.routes[route])
        for changes in (
            {"default_branch": "PRIVATE"},
            {"full_name": "other/repo"},
            {"archived": True},
        ):
            f.routes[route] = {**original, **changes}
            with self.assertRaises(FleetPublishLookupError) as error:
                producer.verify_workflow_protocol(f, WORKFLOW)
            self.assertNotIn("PRIVATE", str(error.exception))
        f.routes[route] = original
        f.routes[f"repos/{CENTRAL}/compare/{WORKFLOW}...{TIP}"]["status"] = "diverged"
        with self.assertRaises(FleetPublishLookupError) as error:
            producer.verify_workflow_protocol(f, WORKFLOW)
        self.assertEqual(error.exception.kind, "workflow_ancestry")

    def test_every_frozen_historical_graph_is_recognized(self):
        for pins in producer.HISTORICAL_PRODUCERS:
            with self.subTest(owner=pins[producer.PRODUCER_OWNER_PATH]):
                self.assertEqual(producer.historical_pinset(Fixture(pins), WORKFLOW), pins)

    def test_any_mutated_historical_blob_is_unknown_producer(self):
        for name in GRAPH_0_5_23:
            with self.subTest(path=name):
                mutated = GRAPH_0_5_23.copy()
                mutated[name] = "6" * 40
                with self.assertRaises(FleetPublishLookupError) as error:
                    producer.historical_pinset(Fixture(mutated), WORKFLOW)
                self.assertEqual(error.exception.kind, "unknown_producer")

    def test_historical_graph_requires_main_ancestry_before_source_reads(self):
        f = self.fixture
        f.routes[f"repos/{CENTRAL}/compare/{WORKFLOW}...{TIP}"]["status"] = "diverged"
        with self.assertRaises(FleetPublishLookupError) as error:
            producer.historical_pinset(f, WORKFLOW)
        self.assertEqual(error.exception.kind, "workflow_ancestry")
        self.assertFalse(any("/git/trees/" in r for r in f.calls))

    def test_symlinked_producer_directory_and_truncated_tree_refuse(self):
        f = self.fixture
        route = f"repos/{CENTRAL}/git/trees/{ROOT}"
        original = copy.deepcopy(f.routes[route])
        f.routes[route]["tree"][0].update(mode="120000", type="blob")
        with self.assertRaises(FleetPublishLookupError):
            producer.historical_pinset(f, WORKFLOW)
        f.routes[route] = {**original, "truncated": True}
        with self.assertRaises(FleetPublishLookupError):
            producer.historical_pinset(f, WORKFLOW)


if __name__ == "__main__":
    unittest.main()
