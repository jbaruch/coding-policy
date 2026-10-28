"""Immutable producer blob identity; no live API or source execution."""

from __future__ import annotations

import copy
import importlib.util
import hashlib
import os
import subprocess
import sys
import tempfile
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
REVIEWED_PRODUCERS = copy.deepcopy(producer.TRUSTED_PRODUCERS)

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
        self.pins = {
            name: f"{index + 1000:040x}"
            for index, name in enumerate(producer.PRODUCER_PATHS, 1)
        }
        self.pins[producer.PRODUCER_OWNER_PATH] = producer.local_owner_blob()
        self.trusted = {
            name: sha
            for name, sha in self.pins.items()
            if name != producer.PRODUCER_OWNER_PATH
        }
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
        pins = patch.object(producer, "TRUSTED_PRODUCERS", (self.fixture.trusted,))
        pins.start()
        self.addCleanup(pins.stop)

    def test_complete_reviewed_closure_allows_central_ancestor_not_tip(self):
        producer.verify_workflow_protocol(self.fixture, WORKFLOW)
        self.assertNotEqual(WORKFLOW, TIP)
        self.assertEqual(sum("/git/trees/" in route for route in self.fixture.calls), 9)

    def test_wrong_blob_and_mixed_pinsets_cannot_form_supported_protocol(self):
        f = self.fixture
        broken = copy.deepcopy(f.trusted)
        broken[producer.PRODUCER_PATHS[0]] = "6" * 40
        another = copy.deepcopy(f.trusted)
        another[producer.PRODUCER_PATHS[1]] = "7" * 40
        for pins in ((), (broken,), (broken, another)):
            with (
                patch.object(producer, "TRUSTED_PRODUCERS", pins),
                self.assertRaises(FleetPublishLookupError),
            ):
                producer.verify_workflow_protocol(f, WORKFLOW)

    def test_every_executable_dependency_mutation_is_unknown_producer(self):
        for name, expected in self.fixture.pins.items():
            with self.subTest(path=name):
                fixture = Fixture()
                for response in fixture.routes.values():
                    if isinstance(response, dict) and isinstance(
                        response.get("tree"), list
                    ):
                        for leaf in response["tree"]:
                            if leaf["sha"] == expected:
                                leaf["sha"] = "f" * 40
                with self.assertRaises(FleetPublishLookupError) as error:
                    producer.verify_workflow_protocol(fixture, WORKFLOW)
                self.assertEqual(error.exception.kind, "unknown_producer")

    def test_reviewed_literals_match_all_current_dependency_bytes(self):
        root = SOURCE.parents[1]
        actual = {}
        for name in producer.PRODUCER_PATHS:
            if name == producer.PRODUCER_OWNER_PATH:
                continue
            raw = (root / name).read_bytes()
            actual[name] = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        self.assertEqual(actual, REVIEWED_PRODUCERS[0])

    def test_local_reader_owner_uses_its_actual_git_blob_identity(self):
        expected = subprocess.check_output(
            ["git", "hash-object", str(SOURCE / "fleet_publish_producer.py")], text=True
        ).strip()
        self.assertEqual(producer.local_owner_blob(), expected)

    def test_unsafe_or_unavailable_local_owner_refuses_before_native_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for kind in (
                "missing",
                "directory",
                "symlink",
                "hardlink",
                "writable",
                "oversized",
            ):
                with self.subTest(kind=kind):
                    local = base / kind
                    if kind == "directory":
                        local.mkdir()
                    elif kind == "symlink":
                        local.symlink_to(SOURCE / "fleet_publish_producer.py")
                    elif kind != "missing":
                        local.write_bytes(
                            b"fixed trusted source\n"
                            if kind != "oversized"
                            else b"x" * (128 * 1024 + 1)
                        )
                        local.chmod(0o666 if kind == "writable" else 0o644)
                        if kind == "hardlink":
                            os.link(local, base / "other-name")
                    fixture = Fixture()
                    with (
                        patch.object(producer, "__file__", str(local)),
                        self.assertRaises(FleetPublishLookupError) as error,
                    ):
                        producer.verify_workflow_protocol(fixture, WORKFLOW)
                    self.assertEqual(error.exception.kind, "unknown_producer")
                    self.assertEqual(fixture.calls, [])

    def test_replaced_local_owner_is_unknown_without_remote_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "owner.py"
            replacement = Path(directory) / "replacement.py"
            local.write_bytes(b"fixed trusted source\n")
            replacement.write_bytes(b"different source\n")
            native_read = os.read

            def replace_after_read(fd, count):
                result = native_read(fd, count)
                os.replace(replacement, local)
                return result

            fixture = Fixture()
            with (
                patch.object(producer, "__file__", str(local)),
                patch.object(producer.os, "read", side_effect=replace_after_read),
                self.assertRaises(FleetPublishLookupError) as error,
            ):
                producer.verify_workflow_protocol(fixture, WORKFLOW)
            self.assertEqual(error.exception.kind, "unknown_producer")
            self.assertEqual(fixture.calls, [])

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
