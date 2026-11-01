"""Complete original regular Git tree -> maintained pack/install comparison."""

from __future__ import annotations

import io
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
from fleet_publish import PublishRefused  # noqa: E402
from test_fleet_publish_historical import proof  # noqa: E402


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.envelope = proof()["intent"]
        self.plugin = self.envelope["intent"]["plugin_name"]
        self.version = self.envelope["intent"]["version"]
        self.files = {
            ".tessl-plugin/plugin.json": json.dumps(
                {"name": self.plugin, "version": self.version}
            ).encode(),
            "tile.json": json.dumps(
                {"name": self.plugin, "version": self.version}
            ).encode(),
            "README.md": b"# Complete original fixture\n",
            "rules/fixture.md": b"# Fixture\n",
        }
        self.original = {
            **self.files,
            "ignored.py": b"raise RuntimeError('NEVER EXECUTE CONSUMER DATA')\n",
            ".tesslignore": b"ignored.py\n",
        }
        self.entries = {
            name: {"type": "blob", "mode": "100644"} for name in self.original
        }
        self.calls = []
        self.change = None

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
                sorted(self.original),
            )
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
        with (
            patch.object(
                packages,
                "tree",
                return_value=(self.envelope["intent"]["source_tree_sha"], self.entries),
            ),
            patch.object(
                packages,
                "file",
                side_effect=lambda _github, _repo, _entries, name: self.original[name],
            ),
            patch.object(packages, "command", side_effect=self.command),
            patch.object(packages, "datetime") as clock,
        ):
            clock.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
            return packages.observe_package(object(), self.envelope)

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
