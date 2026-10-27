"""Immutable producer blob identity; no live API or source execution."""

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
    def __init__(self):
        self.pins = {name: str(index) * 40 for index, name in enumerate(producer.PRODUCER_PATHS, 1)}
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
            f"repos/{CENTRAL}/git/commits/{WORKFLOW}": {"sha": WORKFLOW, "tree": {"sha": ROOT}},
            f"repos/{CENTRAL}/git/trees/{ROOT}": {
                "sha": ROOT,
                "truncated": False,
                "tree": [
                    entry(".github", GITHUB, directory=True),
                    entry("scripts", SCRIPTS, directory=True),
                ],
            },
            f"repos/{CENTRAL}/git/trees/{GITHUB}": {
                "sha": GITHUB,
                "truncated": False,
                "tree": [entry("workflows", WORKFLOWS, directory=True)],
            },
            f"repos/{CENTRAL}/git/trees/{WORKFLOWS}": {
                "sha": WORKFLOWS,
                "truncated": False,
                "tree": [entry("fleet-publish.yml", self.pins[producer.PRODUCER_PATHS[0]])],
            },
            f"repos/{CENTRAL}/git/trees/{SCRIPTS}": {
                "sha": SCRIPTS,
                "truncated": False,
                "tree": [
                    entry(name.split("/")[-1], self.pins[name])
                    for name in producer.PRODUCER_PATHS[1:]
                ],
            },
        }
        self.calls = []

    def json(self, route):
        self.calls.append(route)
        return copy.deepcopy(self.routes[route])


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        pins = patch.object(producer, "TRUSTED_PRODUCERS", (self.fixture.pins,))
        pins.start()
        self.addCleanup(pins.stop)

    def test_exact_four_reviewed_blobs_allow_central_ancestor_not_tip(self):
        producer.verify_workflow_protocol(self.fixture, WORKFLOW)
        self.assertNotEqual(WORKFLOW, TIP)
        self.assertEqual(sum("/git/trees/" in route for route in self.fixture.calls), 4)

    def test_wrong_blob_and_mixed_pinsets_cannot_form_supported_protocol(self):
        f = self.fixture
        broken = copy.deepcopy(f.pins)
        broken[producer.PRODUCER_PATHS[0]] = "6" * 40
        another = copy.deepcopy(f.pins)
        another[producer.PRODUCER_PATHS[1]] = "7" * 40
        for pins in ((), (broken,), (broken, another)):
            with (
                patch.object(producer, "TRUSTED_PRODUCERS", pins),
                self.assertRaises(FleetPublishLookupError),
            ):
                producer.verify_workflow_protocol(f, WORKFLOW)

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
        with self.assertRaises(FleetPublishLookupError):
            producer.verify_workflow_protocol(f, WORKFLOW)

    def test_symlinked_producer_directory_and_truncated_tree_refuse(self):
        f = self.fixture
        route = f"repos/{CENTRAL}/git/trees/{ROOT}"
        original = copy.deepcopy(f.routes[route])
        f.routes[route]["tree"][0].update(mode="120000", type="blob")
        with self.assertRaises(FleetPublishLookupError):
            producer.verify_workflow_protocol(f, WORKFLOW)
        f.routes[route] = {**original, "truncated": True}
        with self.assertRaises(FleetPublishLookupError):
            producer.verify_workflow_protocol(f, WORKFLOW)


if __name__ == "__main__":
    unittest.main()
