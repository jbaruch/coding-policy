#!/usr/bin/env python3
"""Tests for skills/release/stamp-changelog.py — version computation and stamping."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[3] / "skills/release") / "stamp-changelog.py"

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
    """compute_version mirrors the publish step's --bump patch semantics."""

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


class Main(unittest.TestCase):
    """main() stamps the changelog with the version the publish step will assign."""

    _BODY = "# Changelog\n\n### feat — x\n"

    def _run(self, tmp, manifest_version, latest, body=None):
        """Run main() against a temp changelog/manifest; return the changelog text.

        `latest=None` omits --latest and stubs the registry query to a 404
        (first publish); a string passes --latest verbatim.
        """
        changelog = tmp / "CHANGELOG.md"
        changelog.write_text(body if body is not None else self._BODY)
        manifest = tmp / "plugin.json"
        manifest.write_text('{"name": "ws/p", "version": "%s"}' % manifest_version)
        argv = [
            "stamp-changelog.py",
            "--changelog", str(changelog),
            "--manifest", str(manifest),
            "--date", "2026-08-09",
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
        return changelog.read_text()

    def test_first_publish_stamps_manifest_version(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # No registry version → stamp the manifest version verbatim.
            self.assertIn("## 0.1.0 — 2026-08-09", self._run(Path(d), "0.1.0", None))

    def test_manifest_ahead_stamps_manifest_version(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("## 0.19.0 — 2026-08-09", self._run(Path(d), "0.19.0", "0.18.7"))

    def test_normal_bump_stamps_bumped_version(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # Manifest matches registry → stamp the bumped patch.
            self.assertIn("## 0.18.8 — 2026-08-09", self._run(Path(d), "0.18.7", "0.18.7"))



class QueryLatestVersion(unittest.TestCase):
    """query_latest_version maps registry-version.sh's contract; failures raise."""

    def _run(self, returncode, stdout="", stderr=""):
        cp = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        with mock.patch.object(stamp_changelog.subprocess, "run", return_value=cp):
            return stamp_changelog.query_latest_version("jbaruch/x")

    def test_success_parses_latest(self):
        self.assertEqual(self._run(0, stdout='{"version":"0.2.64"}\n'), "0.2.64")

    def test_never_published_returns_none(self):
        self.assertIsNone(self._run(0, stdout='{"version":null}\n'))

    def test_404_raises(self):
        # Never-published is {"version": null} on exit 0; a 404 is a tool failure.
        with self.assertRaises(RuntimeError):
            self._run(2, stderr="request failed: HTTP 404")

    def test_auth_failure_raises(self):
        msg = "✘ Please authenticate with Tessl to continue. Run `tessl login` to sign up or log in."
        with self.assertRaises(RuntimeError):
            self._run(2, stderr=msg)

    def test_network_failure_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(2, stderr="connection reset by peer")


_LAGGING_TESSL = """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "api" ]]; then
  printf '%s' '{"data":[{"attributes":{"version":"0.3.269"}},{"attributes":{"version":"0.3.268"}}]}'
  exit 0
fi
if [[ "$1" == "plugin" && "$2" == "info" ]]; then
  printf 'Name  ws/p\\nLatest Version  0.3.268\\n'
  exit 0
fi
echo "unexpected tessl call: $*" >&2
exit 1
"""


class RegistryLag(unittest.TestCase):
    """The stamp reads the registry source smart-publish.sh publishes from (#606)."""

    def test_stamps_from_versions_api_not_lagging_plugin_info(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # Stub tessl: the plugin-info listing still shows the previous
            # version while the versions API already has the just-published one.
            stub = tmp / "tessl"
            stub.write_text(_LAGGING_TESSL)
            stub.chmod(0o755)
            changelog = tmp / "CHANGELOG.md"
            changelog.write_text("# Changelog\n\n### fix — y\n")
            manifest = tmp / "plugin.json"
            manifest.write_text('{"name": "ws/p", "version": "0.3.268"}')
            env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
            proc = subprocess.run(
                ["python3", str(SCRIPT), "--changelog", str(changelog),
                 "--manifest", str(manifest), "--date", "2026-09-25"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # smart-publish.sh publishes the versions-API latest plus one patch.
            self.assertIn("## 0.3.270 — 2026-09-25", changelog.read_text())


class StampChangelog(unittest.TestCase):
    def test_missing_observed_date_never_fabricates_a_needed_stamp(self):
        with self.assertRaises(ValueError):
            stamp_changelog.stamp_changelog(_UNHEADED, "0.1.1", None)

    def test_missing_date_is_unused_for_an_already_stamped_artifact(self):
        observed = "# Changelog\n\n## 0.1.0 — 2020-01-01\n\n### Entry\n"
        self.assertEqual(
            stamp_changelog.stamp_changelog(observed, "0.1.1", None),
            (observed, False),
        )

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

    def test_writes_change_lines_above_the_newest_heading(self):
        headed = "# Changelog\n\n## 0.18.7 — 2026-06-03\n\n- feat: Old (#1)\n"
        out, changed = stamp_changelog.stamp_changelog(
            headed, "0.18.8", "2026-06-04", ["feat: New (#2)", "fix: Bug (#3)"])
        self.assertTrue(changed)
        self.assertEqual(out, (
            "# Changelog\n\n## 0.18.8 — 2026-06-04\n\n- feat: New (#2)\n"
            "- fix: Bug (#3)\n\n## 0.18.7 — 2026-06-03\n\n- feat: Old (#1)\n"))

    def test_unheaded_entries_take_the_heading_without_change_lines(self):
        out, changed = stamp_changelog.stamp_changelog(
            _UNHEADED, "0.18.8", "2026-06-04", ["feat: New (#2)"])
        self.assertTrue(changed)
        self.assertNotIn("feat: New (#2)", out)
        self.assertIn("## 0.18.8 — 2026-06-04\n\n### feat(x) — new thing", out)

    def test_first_section_follows_the_title(self):
        out, changed = stamp_changelog.stamp_changelog(
            "# Changelog\n", "0.1.0", "2026-06-04", ["feat: Start (#1)"])
        self.assertTrue(changed)
        self.assertEqual(out, "# Changelog\n\n## 0.1.0 — 2026-06-04\n\n- feat: Start (#1)\n")

    def test_missing_date_never_fabricates_a_change_stamp(self):
        with self.assertRaises(ValueError):
            stamp_changelog.stamp_changelog("# Changelog\n", "0.1.0", None, ["feat: x (#1)"])


BOT = "github-actions[bot]"
HEADED = "# Changelog\n\n## 0.1.0 — 2026-01-01\n\n- feat: Start (#1)\n"


class MergedChanges(unittest.TestCase):
    """merged_changes reads the PRs merged since the last publish from git."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q", "-b", "main")
        self.commit("CHANGELOG.md", HEADED, "feat: Start")

    def git(self, *args, author="Dev"):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="a@example.com",
                   GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="a@example.com")
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True, env=env).stdout

    def commit(self, path, content, message, author="Dev"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", path)
        self.git("commit", "-q", "-m", message, author=author)

    def merge_pr(self, number, title):
        self.git("checkout", "-q", "-b", f"pr{number}")
        self.commit(f"src/{number}.txt", "work\n", f"Work for {number}")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", f"pr{number}",
                 "-m", f"Merge pull request #{number} from jbaruch/pr{number}", "-m", title)

    def stamp(self):
        text = (self.root / "CHANGELOG.md").read_text()
        self.commit("CHANGELOG.md", text + "\n- stamped\n", "Stamp CHANGELOG [skip ci]", BOT)

    def changes(self, root=None):
        return stamp_changelog.merged_changes((root or self.root) / "CHANGELOG.md", None)

    def test_lists_prs_merged_since_the_last_stamp(self):
        self.merge_pr(2, "feat: Old")
        self.stamp()
        self.merge_pr(3, "feat(x): Add x")
        self.commit("y.txt", "y\n", "fix(y): Repair y (#4)")  # a squash merge
        self.assertEqual(self.changes(), ["feat(x): Add x (#3)", "fix(y): Repair y (#4)"])

    def test_a_bot_version_bump_is_bookkeeping(self):
        self.stamp()
        self.commit(".tessl-plugin/plugin.json", "{}\n", "chore: bump [skip ci]", BOT)
        self.merge_pr(5, "feat: Five")
        self.assertEqual(self.changes(), ["feat: Five (#5)"])

    def test_a_bot_commit_touching_source_is_a_change(self):
        self.stamp()
        self.commit("src/gen.txt", "gen\n", "chore: Regenerate", BOT)
        self.merge_pr(6, "feat: Six")
        self.assertEqual(self.changes(), ["chore: Regenerate", "feat: Six (#6)"])

    def test_without_a_stamp_only_head_counts(self):
        self.merge_pr(7, "feat: Seven")
        self.merge_pr(8, "feat: Eight")
        self.assertEqual(self.changes(), ["feat: Eight (#8)"])

    def test_a_merge_without_a_title_keeps_its_subject(self):
        self.stamp()
        self.git("checkout", "-q", "-b", "bare")
        self.commit("src/bare.txt", "bare\n", "Bare work")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "bare", "-m", "Merge pull request #9 from jbaruch/bare")
        self.assertEqual(self.changes(), ["Merge pull request #9 from jbaruch/bare"])

    def test_a_shallow_checkout_is_refused(self):
        self.merge_pr(2, "feat: Two")
        clone = self.root / "clone"
        self.git("clone", "-q", "--depth", "1", f"file://{self.root}", str(clone))
        with self.assertRaises(RuntimeError):
            self.changes(clone)

    def run_main(self):
        (self.root / "plugin.json").write_text('{"name": "ws/p", "version": "0.1.0"}')
        proc = subprocess.run(
            ["python3", str(SCRIPT), "--changelog", "CHANGELOG.md", "--manifest",
             "plugin.json", "--latest", "0.1.0", "--date", "2026-11-11"],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return (self.root / "CHANGELOG.md").read_text()

    def test_main_stamps_the_merged_pr_titles(self):
        self.stamp()
        self.merge_pr(10, "feat: Ten")
        self.assertTrue(self.run_main().startswith(
            "# Changelog\n\n## 0.1.1 — 2026-11-11\n\n- feat: Ten (#10)\n\n## 0.1.0"))

    def test_main_is_a_noop_with_nothing_since_the_stamp(self):
        self.merge_pr(11, "feat: Eleven")
        self.stamp()
        before = (self.root / "CHANGELOG.md").read_text()
        self.assertEqual(self.run_main(), before)


if __name__ == "__main__":
    unittest.main()
