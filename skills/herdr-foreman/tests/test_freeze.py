"""A dispatched brief is frozen to a content-addressed copy nothing rewrites (#460)."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foreman.assign import FROZEN_DIR, freeze_decision, freeze_paths
from foreman.errors import UsageError


class FreezeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="foreman-freeze-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.common = self.root / "COMMON.md"
        self.brief = self.root / "reviewer.md"
        self.common.write_text("common rules\n")
        self.brief.write_text("review this\n")
        self.paths = {"common": str(self.common), "reviewer": str(self.brief)}

    def test_freezes_every_brief_beside_its_source(self):
        frozen = freeze_paths(self.paths)
        for key, source in self.paths.items():
            with self.subTest(key=key):
                copy = Path(frozen[key])
                self.assertEqual(copy.parent, Path(source).parent.resolve() / FROZEN_DIR)
                self.assertEqual(copy.read_bytes(), Path(source).read_bytes())

    def test_the_same_content_freezes_to_the_same_file(self):
        self.assertEqual(freeze_paths(self.paths), freeze_paths(self.paths))

    def test_changed_content_freezes_to_a_new_file_and_keeps_the_old(self):
        first = freeze_paths(self.paths)["reviewer"]
        self.brief.write_text("review something else\n")
        second = freeze_paths(self.paths)["reviewer"]
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), "review this\n")

    def test_a_frozen_file_with_other_content_is_never_rewritten(self):
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.write_text("tampered\n")
        with self.assertRaisesRegex(UsageError, "never rewritten"):
            freeze_paths(self.paths)
        self.assertEqual(target.read_text(), "tampered\n")

    def test_an_unreadable_source_names_the_repair(self):
        with self.assertRaisesRegex(UsageError, "Restore readability"):
            freeze_paths({"common": str(self.root / "missing.md")})


class FreezeLinkTest(FreezeTest):
    def test_a_frozen_path_that_is_a_link_is_refused(self):
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.unlink()
        target.symlink_to(self.brief)
        with self.assertRaisesRegex(UsageError, "is a link"):
            freeze_paths(self.paths)

    def test_a_frozen_path_hard_linked_to_its_source_is_refused(self):
        # A hard link shares the source's inode: rewriting the source would
        # rewrite the "frozen" copy with it.
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.unlink()
        os.link(self.brief, target)
        with self.assertRaisesRegex(UsageError, "is a link"):
            freeze_paths(self.paths)

    def test_a_symlinked_frozen_directory_is_refused(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / FROZEN_DIR).symlink_to(elsewhere)
        with self.assertRaisesRegex(UsageError, "is a link or not a directory"):
            freeze_paths(self.paths)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_copy_read_back_through_a_symlinked_directory_is_refused(self):
        from foreman.assign import read_frozen
        frozen = Path(freeze_paths(self.paths)["reviewer"])
        moved = self.root / "moved"
        frozen.parent.rename(moved)
        frozen.parent.symlink_to(moved)
        with self.assertRaisesRegex(UsageError, "is a link or not a directory"):
            read_frozen(frozen)

    def test_a_fifo_or_directory_at_the_frozen_path_is_refused(self):
        for plant in (os.mkfifo, os.mkdir):
            with self.subTest(plant=plant.__name__):
                target = Path(freeze_paths(self.paths)["reviewer"])
                target.unlink()
                plant(target)
                with self.assertRaisesRegex(UsageError, "not a regular file"):
                    freeze_paths(self.paths)
                target.unlink() if plant is os.mkfifo else target.rmdir()

    def test_a_frozen_path_that_fails_inspection_is_an_actionable_refusal(self):
        # coding-policy#460 review: the inspection used to run inside the
        # `except FileExistsError` block, where the sibling `except OSError`
        # never catches it, so a vanished or unreadable copy leaked a traceback.
        # Only the frozen copy fails: the source brief is read through the
        # same calls first, and must still read.
        freeze_paths(self.paths)
        real_open, real_fstat, copies = os.open, os.fstat, set()

        def is_copy(name):
            return re.search(r"\.[0-9a-f]{16}\.md$", str(name)) is not None

        for error in (FileNotFoundError(2, "No such file or directory"), PermissionError(13, "Permission denied")):
            def failing_open(name, flags, *args, error=error, **kwargs):
                if is_copy(name) and not flags & os.O_CREAT:
                    raise error
                return real_open(name, flags, *args, **kwargs)
            with self.subTest(error=type(error).__name__), patch("foreman.assign.os.open", side_effect=failing_open):
                with self.assertRaisesRegex(UsageError, "Cannot open frozen brief .*: {}. Restore".format(error.strerror)):
                    freeze_paths(self.paths)

        def tracking_open(name, flags, *args, **kwargs):
            descriptor = real_open(name, flags, *args, **kwargs)
            if is_copy(name) and not flags & os.O_CREAT:
                copies.add(descriptor)
            return descriptor

        def failing_fstat(descriptor):
            if descriptor in copies:
                raise OSError(5, "Input/output error")
            return real_fstat(descriptor)
        with patch("foreman.assign.os.open", side_effect=tracking_open), \
                patch("foreman.assign.os.fstat", side_effect=failing_fstat):
            with self.assertRaisesRegex(UsageError, "Cannot read frozen brief .*Input/output error"):
                freeze_paths(self.paths)


class FrozenPathTest(unittest.TestCase):
    """`read_frozen` reads only a copy anchored under its own `.dispatched/` (#534)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="foreman-frozen-path-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("source", "other"):
            (self.root / name).mkdir()
            (self.root / name / "brief.md").write_text("brief in {}\n".format(name))
        self.other = Path(freeze_paths({"brief": str(self.root / "other" / "brief.md")})["brief"])
        freeze_paths({"brief": str(self.root / "source" / "brief.md")})

    def test_an_intact_absolute_copy_reads_back(self):
        from foreman.assign import read_frozen
        self.assertEqual(read_frozen(str(self.other)), b"brief in other\n")

    def test_a_traversal_through_another_frozen_directory_is_refused(self):
        from foreman.assign import read_frozen
        # Lexically under source/.dispatched, but it names other's intact copy.
        traversal = "{}/source/{}/../../other/{}/{}".format(self.root, FROZEN_DIR, FROZEN_DIR, self.other.name)
        self.assertTrue(Path(traversal).is_file())
        with self.assertRaisesRegex(UsageError, "not an intact frozen copy"):
            read_frozen(traversal)

    def test_a_retargeted_ancestor_link_is_refused(self):
        from foreman.assign import read_frozen
        # alias -> source when recorded, then retargeted at other: the same
        # path now names other's intact copy (#554).
        alias = self.root / "alias"
        alias.symlink_to(self.root / "other")
        through_alias = alias / FROZEN_DIR / self.other.name
        self.assertTrue(through_alias.is_file())
        with self.assertRaisesRegex(UsageError, "passes through {}, which is a link".format(alias)):
            read_frozen(str(through_alias))

    def test_a_symlink_deeper_in_the_path_is_refused(self):
        from foreman.assign import read_frozen
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "hop").symlink_to(self.root)
        deep = nested / "hop" / "other" / FROZEN_DIR / self.other.name
        self.assertTrue(deep.is_file())
        with self.assertRaisesRegex(UsageError, "passes through {}, which is a link".format(nested / "hop")):
            read_frozen(str(deep))

    def test_a_brief_under_an_alias_freezes_under_its_canonical_directory(self):
        from foreman.assign import read_frozen
        alias = self.root / "alias"
        alias.symlink_to(self.root / "source")
        frozen = Path(freeze_paths({"brief": str(alias / "brief.md")})["brief"])
        self.assertEqual(frozen.parent, self.root / "source" / FROZEN_DIR)
        self.assertEqual(read_frozen(str(frozen)), b"brief in source\n")

    def test_the_source_is_read_through_its_canonical_directory(self):
        # The alias names source when resolved and other by the time of the
        # read: the copy must hold the bytes of the directory it sits under.
        alias = self.root / "alias"
        alias.symlink_to(self.root / "other")
        with patch("foreman.assign.os.path.realpath", return_value=str(self.root / "source")):
            frozen = Path(freeze_paths({"brief": str(alias / "brief.md")})["brief"])
        self.assertEqual(frozen.parent, self.root / "source" / FROZEN_DIR)
        self.assertEqual(frozen.read_bytes(), b"brief in source\n")

    def test_a_source_directory_turned_into_a_link_is_refused(self):
        # Resolved while real, then swapped for a link before the read.
        moved = self.root / "moved"
        with patch("foreman.assign.os.path.realpath", return_value=str(self.root / "source")):
            (self.root / "source").rename(moved)
            (self.root / "source").symlink_to(moved)
            with self.assertRaisesRegex(UsageError, "passes through {}, which became a link".format(self.root / "source")):
                freeze_paths({"brief": str(self.root / "source" / "brief.md")})

    def test_a_freeze_directory_swapped_for_a_link_writes_nothing_there(self):
        # Resolved while real, then source swapped for a link to other before
        # the write: the brief must not land in other's .dispatched.
        from foreman import assign
        moved = self.root / "moved"
        before = sorted((self.root / "other" / FROZEN_DIR).iterdir())
        real_read = assign._read_source

        def read_then_swap(held, canonical, given):
            data = real_read(held, canonical, given)
            (self.root / "source").rename(moved)
            (self.root / "source").symlink_to(self.root / "other")
            return data
        with patch("foreman.assign.os.path.realpath", return_value=str(self.root / "source")), \
                patch("foreman.assign._read_source", side_effect=read_then_swap):
            with self.assertRaisesRegex(UsageError, "passes through {}, which is a link".format(self.root / "source")):
                freeze_paths({"brief": str(self.root / "source" / "brief.md")})
        self.assertEqual(sorted((self.root / "other" / FROZEN_DIR).iterdir()), before)

    def test_a_source_directory_replaced_after_the_read_never_receives_the_copy(self):
        # coding-policy#562 review: a real directory renamed into place is no
        # link, so only the held descriptor keeps the write on the directory
        # the bytes were read from; the read-back then finds no copy there.
        from foreman import assign
        moved = self.root / "moved"
        (self.root / "source" / "brief.md").write_text("a new brief\n")
        real_read = assign._read_source

        def read_then_replace(held, canonical, given):
            data = real_read(held, canonical, given)
            (self.root / "source").rename(moved)
            (self.root / "source").mkdir()
            return data
        with patch("foreman.assign._read_source", side_effect=read_then_replace):
            with self.assertRaisesRegex(UsageError, "Cannot open frozen brief"):
                freeze_paths({"brief": str(self.root / "source" / "brief.md")})
        self.assertFalse((self.root / "source" / FROZEN_DIR).exists())
        self.assertEqual([path.read_bytes() for path in (moved / FROZEN_DIR).glob("brief.*")
                          if path.read_bytes() == b"a new brief\n"], [b"a new brief\n"])

    def test_a_failed_close_leaves_nothing_behind_and_a_retry_succeeds(self):
        # coding-policy#562 review: close is where a delayed ENOSPC surfaces.
        source = str(self.root / "source" / "brief.md")
        (self.root / "source" / "brief.md").write_text("a new brief\n")
        frozen_dir = self.root / "source" / FROZEN_DIR
        before = sorted(frozen_dir.iterdir())
        real_open, real_close, created = os.open, os.close, set()

        def tracking_open(name, flags, *args, **kwargs):
            descriptor = real_open(name, flags, *args, **kwargs)
            if flags & os.O_CREAT:
                created.add(descriptor)
            return descriptor

        def failing_close(descriptor):
            real_close(descriptor)
            if descriptor in created:
                raise OSError(122, "Disk quota exceeded")
        with patch("foreman.assign.os.open", side_effect=tracking_open), \
                patch("foreman.assign.os.close", side_effect=failing_close):
            with self.assertRaisesRegex(UsageError, "Disk quota exceeded. Make its directory writable"):
                freeze_paths({"brief": source})
        self.assertEqual(sorted(frozen_dir.iterdir()), before)
        frozen = Path(freeze_paths({"brief": source})["brief"])
        self.assertEqual(frozen.read_bytes(), b"a new brief\n")

    def test_a_failed_write_leaves_nothing_behind_and_a_retry_succeeds(self):
        # coding-policy#562 review: a partial copy left at the content-addressed
        # name would be refused as "never rewritten" on every retry.
        source = str(self.root / "source" / "brief.md")
        (self.root / "source" / "brief.md").write_text("a new brief\n")
        frozen_dir = self.root / "source" / FROZEN_DIR
        before = sorted(frozen_dir.iterdir())
        with patch("foreman.assign.os.write", side_effect=OSError(28, "No space left on device")):
            with self.assertRaisesRegex(UsageError, "No space left on device. Make its directory writable"):
                freeze_paths({"brief": source})
        self.assertEqual(sorted(frozen_dir.iterdir()), before)
        frozen = Path(freeze_paths({"brief": source})["brief"])
        self.assertEqual(frozen.read_bytes(), b"a new brief\n")

    def test_a_source_path_with_braces_is_reported_not_crashed(self):
        braced = self.root / "br{0}ce{x}"
        braced.mkdir()
        (braced / "brief.md").write_text("braced\n")
        (braced / FROZEN_DIR).symlink_to(self.root / "other")
        with self.assertRaisesRegex(UsageError, "is a link or not a directory"):
            freeze_paths({"brief": str(braced / "brief.md")})
        with self.assertRaisesRegex(UsageError, r"Cannot read briefing file .*br\{0\}ce\{x\}/missing.md"):
            freeze_paths({"brief": str(braced / "missing.md")})

    def test_a_fifo_source_is_refused_without_hanging(self):
        fifo = self.root / "source" / "fifo.md"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(UsageError, "not a regular file. Restore readability"):
            freeze_paths({"brief": str(fifo)})

    def test_a_relative_path_is_refused(self):
        from foreman.assign import read_frozen
        relative = os.path.relpath(self.other, self.root)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.assertTrue(Path(relative).is_file())
        with self.assertRaisesRegex(UsageError, "not an intact frozen copy"):
            read_frozen(relative)


class FreezeDecisionTest(unittest.TestCase):
    """The decision follows the replay answer per role, and never lets a new role skip the freeze."""

    def decide(self, assignments, replays):
        return freeze_decision(assignments, lambda role, name: (role, name) in replays)

    def test_new_work_is_frozen(self):
        self.assertEqual(self.decide({"reviewer": "codex"}, set()), "frozen")

    def test_a_batch_of_replays_keeps_its_sources(self):
        both = {("reviewer", "codex"), ("tester", "grok")}
        self.assertEqual(self.decide({"reviewer": "codex", "tester": "grok"}, both), "source")

    def test_a_replay_is_per_agent(self):
        self.assertEqual(self.decide({"reviewer": "claude"}, {("reviewer", "codex")}), "frozen")

    def test_a_batch_mixing_replays_and_new_roles_is_refused(self):
        with self.assertRaisesRegex(UsageError, "separate calls"):
            self.decide({"reviewer": "codex", "tester": "grok"}, {("reviewer", "codex")})


if __name__ == "__main__":
    unittest.main()
