#!/usr/bin/env python3
"""Tests for skills/release/stamp-changelog.py — version computation and stamping."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "stamp-changelog.py"

spec = importlib.util.spec_from_file_location("stamp_changelog", SCRIPT)
stamp_changelog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp_changelog)


_UNHEADED = """\
# Changelog

### feat(x) — new thing

Body of x.

### fix(y) — a bug

## 0.18.7 — 2026-06-03

### feat(prior) — already released
"""


class ComputeVersion(unittest.TestCase):
    """compute_version mirrors tesslio/patch-version-publish."""

    def test_first_publish_uses_local(self):
        self.assertEqual(stamp_changelog.compute_version("0.1.0", None), "0.1.0")

    def test_bumps_registry_patch(self):
        self.assertEqual(stamp_changelog.compute_version("0.18.7", "0.18.7"), "0.18.8")

    def test_bumps_registry_when_local_behind(self):
        # Local manifest stale vs registry — still bump the registry patch.
        self.assertEqual(stamp_changelog.compute_version("0.18.0", "0.18.7"), "0.18.8")

    def test_respects_manual_ahead_bump(self):
        # Local manifest deliberately ahead of registry — publish as-is.
        self.assertEqual(stamp_changelog.compute_version("0.19.0", "0.18.7"), "0.19.0")

    def test_rejects_malformed(self):
        with self.assertRaises(ValueError):
            stamp_changelog.compute_version("0.18", "0.18.7")
        with self.assertRaises(ValueError):
            stamp_changelog.compute_version("0.18.7", "vNext")


class StampChangelog(unittest.TestCase):
    """stamp_changelog inserts a version heading above un-headed entries."""

    def test_inserts_heading_above_unheaded_entries(self):
        out, changed = stamp_changelog.stamp_changelog(_UNHEADED, "0.18.8", "2026-06-04")
        self.assertTrue(changed)
        lines = out.splitlines()
        # Heading inserted directly above the first un-headed entry…
        h_idx = lines.index("## 0.18.8 — 2026-06-04")
        self.assertEqual(lines[h_idx + 2], "### feat(x) — new thing")
        # …and above the prior released section, which is left intact.
        self.assertLess(
            lines.index("## 0.18.8 — 2026-06-04"),
            lines.index("## 0.18.7 — 2026-06-03"),
        )
        self.assertIn("### feat(prior) — already released", out)

    def test_noop_when_top_already_headed(self):
        already = "# Changelog\n\n## 0.18.7 — 2026-06-03\n\n### feat(x) — released\n"
        out, changed = stamp_changelog.stamp_changelog(already, "0.18.8", "2026-06-04")
        self.assertFalse(changed)
        self.assertEqual(out, already)

    def test_noop_when_no_entries(self):
        empty = "# Changelog\n"
        out, changed = stamp_changelog.stamp_changelog(empty, "0.18.8", "2026-06-04")
        self.assertFalse(changed)
        self.assertEqual(out, empty)

    def test_preserves_trailing_newline(self):
        out, changed = stamp_changelog.stamp_changelog(_UNHEADED, "0.18.8", "2026-06-04")
        self.assertTrue(changed)
        self.assertTrue(out.endswith("\n"))

    def test_idempotent(self):
        once, _ = stamp_changelog.stamp_changelog(_UNHEADED, "0.18.8", "2026-06-04")
        twice, changed = stamp_changelog.stamp_changelog(once, "0.18.9", "2026-06-05")
        # Second run sees the heading already on top — no double stamp.
        self.assertFalse(changed)
        self.assertEqual(twice, once)


if __name__ == "__main__":
    unittest.main()
