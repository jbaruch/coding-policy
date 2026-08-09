#!/usr/bin/env python3
"""Tests for skills/release/stamp-changelog.py — version computation and stamping."""

import importlib.util
import subprocess
import unittest
from unittest import mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "stamp-changelog.py"

spec = importlib.util.spec_from_file_location("stamp_changelog", SCRIPT)
assert spec and spec.loader, f"cannot load stamp-changelog.py at {SCRIPT}"
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


class NeedsOwnPush(unittest.TestCase):
    """The stamp commit must be pushed here only when no bump carries it."""

    def test_first_publish_pushes(self):
        # First publish: computed == manifest, publish step pushes nothing.
        self.assertTrue(stamp_changelog.needs_own_push("0.1.0", "0.1.0", True))

    def test_manifest_ahead_pushes(self):
        # Manifest ahead of registry: published as-is, no bump commit.
        self.assertTrue(stamp_changelog.needs_own_push("0.19.0", "0.19.0", True))

    def test_normal_bump_does_not_push(self):
        # Bump case: publish step's own commit carries the stamp.
        self.assertFalse(stamp_changelog.needs_own_push("0.18.8", "0.18.7", True))

    def test_noop_never_pushes(self):
        # Nothing stamped — no commit exists to push.
        self.assertFalse(stamp_changelog.needs_own_push("0.1.0", "0.1.0", False))


class MainDecisionFile(unittest.TestCase):
    """main() writes the push decision for the calling action to read."""

    _BODY = "# Changelog\n\n### feat — x\n"

    def _run(self, tmp, manifest_version, latest, body=None):
        """Run main() with a temp changelog/manifest; return the decision text.

        `latest=None` omits --latest and stubs the registry query to a 404
        (first publish); a string passes --latest verbatim.
        """
        changelog = tmp / "CHANGELOG.md"
        changelog.write_text(body if body is not None else self._BODY)
        manifest = tmp / "plugin.json"
        manifest.write_text('{"name": "ws/p", "version": "%s"}' % manifest_version)
        decision = tmp / "decision"
        argv = [
            "stamp-changelog.py",
            "--changelog", str(changelog),
            "--manifest", str(manifest),
            "--date", "2026-08-09",
            "--decision-file", str(decision),
        ]
        if latest is not None:
            argv += ["--latest", latest]
        with mock.patch("sys.argv", argv):
            if latest is None:
                with mock.patch.object(stamp_changelog, "query_latest_version",
                                       return_value=None):
                    stamp_changelog.main()
            else:
                stamp_changelog.main()
        return decision.read_text()

    def test_first_publish_writes_true(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # No registry version → computed == manifest → nothing carries it.
            self.assertEqual(self._run(Path(d), "0.1.0", None), "true")

    def test_manifest_ahead_writes_true(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(Path(d), "0.19.0", "0.18.7"), "true")

    def test_normal_bump_writes_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(Path(d), "0.18.7", "0.18.7"), "false")

    def test_noop_writes_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # Top already headed → no stamp → no commit to push.
            already = "# Changelog\n\n## 0.1.0 — 2026-08-09\n\n### feat — x\n"
            self.assertEqual(self._run(Path(d), "0.1.0", None, body=already), "false")


class QueryLatestVersion(unittest.TestCase):
    """query_latest_version degrades gracefully when the stamp step has no auth."""

    def _run(self, returncode, stdout="", stderr=""):
        cp = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        with mock.patch.object(stamp_changelog.subprocess, "run", return_value=cp):
            return stamp_changelog.query_latest_version("jbaruch/x")

    def test_success_parses_latest(self):
        self.assertEqual(self._run(0, stdout="Name  x\nLatest Version  0.2.64\n"), "0.2.64")

    def test_404_returns_none(self):
        self.assertIsNone(self._run(1, stderr="request failed: HTTP 404"))

    def test_auth_failure_returns_none(self):
        # The regression: no auth in the stamp step must fall back, not raise (#207).
        msg = "✘ Please authenticate with Tessl to continue. Run `tessl login` to sign up or log in."
        self.assertIsNone(self._run(1, stderr=msg))

    def test_non_auth_failure_still_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(1, stderr="connection reset by peer")


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
