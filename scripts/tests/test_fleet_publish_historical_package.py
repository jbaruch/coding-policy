"""Complete original regular Git tree -> maintained pack/install comparison."""

from __future__ import annotations

import io
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/release/tests"))
import fleet_publish_historical_package as packages  # noqa: E402
from fleet_publish_archive import ArchiveError  # noqa: E402
from fleet_publish_github import FleetPublishLookupError  # noqa: E402
from fleet_publish import PublishRefused, encoded, MANIFEST, STAMP  # noqa: E402
from test_fleet_publish_historical import proof  # noqa: E402


REAL_COMMAND = packages.command


class PackageGit:
    def __init__(self, fixture):
        self.fixture = fixture
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.calls = []
        original = fixture.envelope["intent"]
        claim = fixture.envelope["bookkeeping_sha"]
        parent = "1" * 40
        claimed = {
            **fixture.original,
            MANIFEST: encoded(fixture.published_manifest),
            "CHANGELOG.md": fixture.stamped_changelog,
            STAMP: encoded(original),
        }
        if fixture.change == "bookkeeping-changelog":
            claimed["CHANGELOG.md"] += b"\nUnowned drift\n"
        elif fixture.change == "bookkeeping-manifest":
            claimed[MANIFEST] = encoded({**fixture.published_manifest, "version": "9.9.9"})
        elif fixture.change == "bookkeeping-other":
            claimed["README.md"] = b"Unowned drift\n"
        elif fixture.change == "stamp-missing":
            claimed["CHANGELOG.md"] = fixture.original["CHANGELOG.md"]
        elif fixture.change == "stamp-ambiguous":
            claimed["CHANGELOG.md"] += f"\n## {fixture.version} — 2020-01-02\n".encode()
        for commit, tree, data in [
            (original["source_sha"], original["source_tree_sha"], fixture.original),
            (parent, "2" * 40, fixture.original),
            (claim, "3" * 40, claimed),
        ]:
            entries = []
            for name, raw in data.items():
                digest = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                self.blobs[digest] = raw
                entries.append({"path": name, "type": "blob", "mode": "100644", "sha": digest})
            self.trees[tree] = {"sha": tree, "truncated": False, "tree": entries}
            self.commits[commit] = {"sha": commit, "tree": {"sha": tree}, "parents": [{"sha": parent}] if commit == claim else []}

    def api(self, route):
        self.calls.append(route)
        if "/git/commits/" in route:
            return self.commits[route.rsplit("/", 1)[1]]
        if "/git/trees/" in route:
            return self.trees[route.rsplit("/", 1)[1].split("?", 1)[0]]
        raise AssertionError("Unexpected historical package read")

    def blob(self, _repo, digest):
        return self.blobs[digest]


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.envelope = proof()["intent"]
        self.plugin = self.envelope["intent"]["plugin_name"]
        self.version = self.envelope["intent"]["version"]
        self.calls = []
        self.change = None
        self.actual_pack = False
        self.configure_source()

    def configure_source(self):
        self.published_manifest = {
            "name": self.plugin,
            "version": self.version,
            "description": "Synthetic historical projection fixture.",
            "private": False,
            "rules": ["rules/fixture.md"],
        }
        self.stamped_changelog = (
            f"# Changelog\n\n## {self.version} — 2020-01-01\n\n### Added\n\n- Fixed source entry.\n"
        ).encode()
        self.files = {
            MANIFEST: json.dumps(self.published_manifest).encode(),
            "tile.json": json.dumps({
                "name": self.plugin,
                "version": self.version,
                "summary": "Synthetic historical projection fixture.",
                "private": False,
                "rules": {"fixture": {"rules": "rules/fixture.md"}},
            }).encode(),
            "README.md": b"# Complete original fixture\n",
            "rules/fixture.md": b"# Fixture\n",
        }
        self.original = {
            MANIFEST: json.dumps({**self.published_manifest, "version": "0.1.0"}).encode(),
            "README.md": self.files["README.md"],
            "rules/fixture.md": self.files["rules/fixture.md"],
            "CHANGELOG.md": b"# Changelog\n\n### Added\n\n- Fixed source entry.\n",
            "ignored.py": b"raise RuntimeError('NEVER EXECUTE CONSUMER DATA')\n",
            "extra-not-selected.txt": b"No consumer projection grant\n",
            "tessl.json": b'{"dependencies":{"untrusted/consumer-config":{"version":"*"}}}\n',
            ".tesslignore": b"ignored.py\n",
        }

    def command(self, arguments, cwd):
        self.calls.append(arguments)
        if arguments == ["--version"]:
            return (packages.TESSL_VERSION + "\n").encode()
        if arguments[:2] == ["plugin", "pack"]:
            source = Path(arguments[-1])
            self.assertEqual(
                sorted(
                    p.relative_to(source).as_posix()
                    for p in source.rglob("*")
                    if p.is_file()
                ),
                sorted([MANIFEST, "README.md", "CHANGELOG.md", ".tesslignore", "rules/fixture.md"]),
            )
            self.assertEqual(json.loads((source / MANIFEST).read_bytes()), self.published_manifest)
            self.assertEqual((source / "CHANGELOG.md").read_bytes(), self.stamped_changelog)
            if self.actual_pack:
                return REAL_COMMAND(arguments, cwd)
            with tarfile.open(arguments[3], "w:gz") as archive:
                for name, raw in self.files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            return b"pack complete"
        if arguments[0] == "install":
            self.assertEqual(
                arguments,
                ["install", "--strict", "--yes", f"{self.plugin}@{self.version}"],
            )
            config = json.loads((cwd / "tessl.json").read_bytes())
            self.assertEqual(
                config["dependencies"], {self.plugin: {"version": self.version}}
            )
            destination = cwd / ".tessl/plugins" / self.plugin
            installed = {
                **self.files,
                "tessl-package.json": json.dumps(
                    {"name": self.plugin, "version": self.version}
                ).encode(),
            }
            if self.change == "extra":
                installed["untracked-extra"] = b"unknown"
            elif self.change == "missing":
                del installed["rules/fixture.md"]
            elif self.change == "content":
                installed["README.md"] = b"changed"
            elif self.change == "duplicate-json":
                installed["tile.json"] = b'{"name":1,"name":2}'
            elif self.change == "coordinate":
                installed["tile.json"] = b'{"name":"jbaruch/wrong","version":"0.1.0"}'
            for name, raw in installed.items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            return b"installed"
        self.assertEqual(
            arguments, ["api", f"v1/tiles/{self.plugin}/versions/{self.version}"]
        )
        return json.dumps(
            {
                "data": {
                    "attributes": {
                        "version": self.version,
                        "moderationStatus": "pending"
                        if self.change == "moderation"
                        else "pass",
                        "fingerprint": "a" * 64,
                    }
                }
            }
        ).encode()

    def observe(self):
        self.github = PackageGit(self)
        with (
            patch.object(packages, "command", side_effect=self.command),
            patch.object(packages, "datetime") as clock,
        ):
            clock.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
            return packages.observe_package(self.github, self.envelope)

    def test_auto_bumped_source_with_extra_files_uses_exact_publisher_projection(self):
        self.envelope["intent"].update(registry_baseline="0.1.0", version="0.1.1")
        self.version = "0.1.1"
        self.configure_source()
        result = self.observe()
        self.assertEqual(result["source"], result["installed"])
        self.assertEqual([row["path"] for row in result["source"]], sorted(self.files))

    def test_actual_official_pack_preserves_the_four_file_construction_shape(self):
        self.actual_pack = True
        result = self.observe()
        for source, installed in zip(result["source"], result["installed"], strict=True):
            self.assertEqual(source["path"], installed["path"])
            key = "json_sha256" if source["path"] in {MANIFEST, "tile.json"} else "sha256"
            self.assertEqual(source[key], installed[key])
        self.assertEqual(len(result["source"]), 4)

    def test_native_bookkeeping_and_observed_stamp_drift_refuse_before_pack(self):
        for change in ["bookkeeping-changelog", "bookkeeping-manifest", "bookkeeping-other", "stamp-missing", "stamp-ambiguous"]:
            with self.subTest(change=change):
                self.change = change
                self.calls = []
                with self.assertRaises((ValueError, RuntimeError)):
                    self.observe()
                self.assertEqual(self.calls, [])

    def test_complete_official_pack_matches_every_installed_file_with_only_fixed_metadata_extra(
        self,
    ):
        result = self.observe()
        self.assertEqual([row["path"] for row in result["source"]], sorted(self.files))
        self.assertEqual(result["source"], result["installed"])
        self.assertEqual(
            result["tool"], {"name": "tessl", "version": packages.TESSL_VERSION}
        )
        self.assertEqual(
            [arguments[0] for arguments in self.calls],
            ["--version", "plugin", "install", "api"],
        )

    def test_partial_changed_unexpected_or_unmoderated_package_never_proves_publication(
        self,
    ):
        for change in (
            "extra",
            "missing",
            "content",
            "duplicate-json",
            "coordinate",
            "moderation",
        ):
            self.change = change
            with (
                self.subTest(change=change),
                self.assertRaises(
                    (ArchiveError, FleetPublishLookupError, PublishRefused)
                ),
            ):
                self.observe()


if __name__ == "__main__":
    unittest.main()
