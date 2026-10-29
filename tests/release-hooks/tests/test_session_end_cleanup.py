"""Owned temporary native-session fixtures for disposable-only cleanup.

Every path, symlink target and sentinel belongs to this suite. CLI cases run
with a private native temporary root, never the operator's scratchpad tree.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "skills/release/hooks/session-end-cleanup.py"
SESSION = "11111111-1111-4111-8111-111111111111"
OTHER_SESSION = "22222222-2222-4222-8222-222222222222"


def load_hook() -> Any:
    spec = importlib.util.spec_from_file_location("session_end_cleanup_fixture", HOOK)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Cannot load the cleanup hook at {HOOK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionEndCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="coding-policy-session-end-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.native_root = self.private_directory(self.root / "native")
        self.cwd = self.private_directory(self.root / "unrelated-cwd")
        self.pad = self.scratchpad(SESSION)
        self.disposable = self.private_directory(self.pad / ".coding-policy-disposable")
        self.generated = self.write(self.disposable / "rebuild.tmp", b"Reproducible fixture bytes.\n")
        self.note = self.write(self.pad / "retain-notes.md", b"Retain session notes.\n")
        self.other_pad = self.scratchpad(OTHER_SESSION)
        other_namespace = self.private_directory(self.other_pad / ".coding-policy-disposable")
        self.other_file = self.write(other_namespace / "retain.tmp", b"Another session owns this.\n")
        self.payload: dict[str, Any] = {"hook_event_name": "SessionEnd", "session_id": SESSION,
                                       "scratchpad_dir": str(self.pad), "reason": "prompt_input_exit"}
        self.env = dict(os.environ, CLAUDE_CODE_TMPDIR=str(self.native_root), TMPDIR=str(self.native_root))
        for name in ("CLAUDE_JOB_DIR", "HERDR_ENV", "HERDR_PANE_ID"):
            self.env.pop(name, None)

    def private_directory(self, path: Path) -> Path:
        for parent in reversed((path, *path.parents)):
            if parent == self.root or parent.is_relative_to(self.root):
                parent.mkdir(mode=0o700, exist_ok=True)
                parent.chmod(0o700)
        return path

    def scratchpad(self, session: str, *, native_root: Path | None = None) -> Path:
        base = self.native_root if native_root is None else native_root
        return self.private_directory(base / f"claude-{os.getuid()}" / "-fixture-project" / session / "scratchpad")

    @staticmethod
    def write(path: Path, body: bytes) -> Path:
        path.write_bytes(body)
        path.chmod(0o600)
        return path

    def run_hook(self, payload: Any = None, *, raw: bytes | None = None,
                 env: dict[str, str] | None = None) -> dict[str, Any]:
        if raw is None:
            raw = json.dumps(self.payload if payload is None else payload).encode("utf-8")
        result = subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True,
                                cwd=self.cwd, env=self.env if env is None else env, timeout=3, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        value = json.loads(result.stdout)
        return self.result(value)

    def result(self, value: Any) -> dict[str, Any]:
        self.assertIsInstance(value, dict)
        self.assertIn(value["status"], ("cleaned", "skipped", "incomplete"))
        self.assertIsInstance(value["reason"], str)
        self.assertIs(type(value["removed"]), int)
        self.assertGreaterEqual(value["removed"], 0)
        for field in ("additionalContext", "hookSpecificOutput", "decision", "continue", "wake_agent"):
            self.assertNotIn(field, value)
        return value

    def call_hook(self, module: Any) -> dict[str, Any]:
        return self.result(module.cleanup(self.payload, self.env))

    def call_main(self, module: Any) -> tuple[dict[str, Any], str]:
        output, errors = io.StringIO(), io.StringIO()
        incoming = io.TextIOWrapper(io.BytesIO(json.dumps(self.payload).encode("utf-8")))
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", incoming), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(module.main(), 0)
        return self.result(json.loads(output.getvalue())), errors.getvalue()

    def assert_preserved(self) -> None:
        self.assertEqual(self.generated.read_bytes(), b"Reproducible fixture bytes.\n")
        self.assertEqual(self.note.read_bytes(), b"Retain session notes.\n")
        self.assertEqual(self.other_file.read_bytes(), b"Another session owns this.\n")

    def test_removes_only_the_disposable_namespace_and_preserves_siblings(self) -> None:
        nested = self.private_directory(self.disposable / "nested")
        self.write(nested / "rebuild-again.tmp", b"Disposable nested bytes.\n")
        result = self.run_hook()
        self.assertEqual(result["status"], "cleaned")
        self.assertGreater(result["removed"], 0)
        self.assertFalse(self.disposable.exists())
        self.assertTrue(self.pad.is_dir())
        self.assertEqual(self.note.read_bytes(), b"Retain session notes.\n")
        self.assertEqual(self.other_file.read_bytes(), b"Another session owns this.\n")

    def test_supported_normal_exit_reasons_cleanup(self) -> None:
        for reason in ("prompt_input_exit", "logout", "other"):
            with self.subTest(reason=reason):
                self.private_directory(self.disposable)
                self.write(self.generated, b"Reproducible fixture bytes.\n")
                self.run_hook({**self.payload, "reason": reason})
                self.assertFalse(self.disposable.exists())
                self.assertTrue(self.note.exists())

    def test_repeated_cleanup_is_safe_when_namespace_is_already_absent(self) -> None:
        self.run_hook()
        self.run_hook()
        self.assertFalse(self.disposable.exists())
        self.assertTrue(self.pad.is_dir())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_missing_path_or_session_never_derives_a_target(self) -> None:
        for field in ("hook_event_name", "scratchpad_dir", "session_id", "reason"):
            with self.subTest(field=field):
                payload = dict(self.payload)
                payload.pop(field)
                self.run_hook(payload)
                self.assert_preserved()

    def test_stop_and_unknown_hook_events_preserve_scratchpad(self) -> None:
        for event in ("Stop", "SessionStart", "FutureEvent", "", None):
            with self.subTest(event=event):
                self.run_hook({**self.payload, "hook_event_name": event})
                self.assert_preserved()

    def test_clear_resume_and_unsupported_reasons_preserve_scratchpad(self) -> None:
        for reason in ("clear", "resume", "future_reason", "", None):
            with self.subTest(reason=reason):
                self.run_hook({**self.payload, "reason": reason})
                self.assert_preserved()

    def test_native_background_job_marker_preserves_scratchpad(self) -> None:
        background = self.private_directory(self.root / "background-job")
        self.run_hook(env={**self.env, "CLAUDE_JOB_DIR": str(background)})
        self.assert_preserved()

    def test_foreground_herdr_marker_may_cleanup_its_own_opt_in_namespace(self) -> None:
        self.run_hook(env={**self.env, "HERDR_ENV": "owned-fixture"})
        self.assertFalse(self.disposable.exists())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_explicit_background_work_and_continuations_preserve_scratchpad(self) -> None:
        for field, value in (("background_tasks", [{"id": "owned-background-fixture"}]),
                             ("session_crons", [{"id": "owned-cron-fixture"}]),
                             ("background", True), ("is_background", True),
                             ("agent_id", "owned-background-agent")):
            with self.subTest(field=field):
                self.run_hook({**self.payload, field: value})
                self.assert_preserved()

    def test_empty_background_lists_do_not_block_foreground_cleanup(self) -> None:
        self.run_hook({**self.payload, "background_tasks": [], "session_crons": [],
                       "background": False, "is_background": False, "agent_id": ""})
        self.assertFalse(self.disposable.exists())
        self.assertTrue(self.note.exists())

    def test_session_mismatch_and_non_native_ids_preserve_scratchpad(self) -> None:
        for session in (OTHER_SESSION, "..", "../" + SESSION, SESSION + "/extra", "", 1, None):
            with self.subTest(session=session):
                self.run_hook({**self.payload, "session_id": session})
                self.assert_preserved()

    def test_path_types_relative_paths_and_wrong_leaf_preserve_scratchpad(self) -> None:
        for path in (None, 1, {}, [], "", ".", "scratchpad", str(self.pad.parent), str(self.pad / "nested")):
            with self.subTest(path=path):
                self.run_hook({**self.payload, "scratchpad_dir": path})
                self.assert_preserved()

    def test_owned_directory_outside_native_temporary_root_is_preserved(self) -> None:
        outside = self.scratchpad(SESSION, native_root=self.root / "outside-native-root")
        namespace = self.private_directory(outside / ".coding-policy-disposable")
        sentinel = self.write(namespace / "retain.tmp", b"Outside the selected native root.\n")
        self.run_hook({**self.payload, "scratchpad_dir": str(outside)})
        self.assertEqual(sentinel.read_bytes(), b"Outside the selected native root.\n")
        self.assert_preserved()

    def test_another_native_uid_namespace_is_preserved(self) -> None:
        outside = self.private_directory(self.native_root / f"claude-{os.getuid() + 1}" /
                                         "-fixture-project" / SESSION / "scratchpad")
        namespace = self.private_directory(outside / ".coding-policy-disposable")
        sentinel = self.write(namespace / "retain.tmp", b"A different native uid namespace.\n")
        self.run_hook({**self.payload, "scratchpad_dir": str(outside)})
        self.assertEqual(sentinel.read_bytes(), b"A different native uid namespace.\n")
        self.assert_preserved()

    def test_symbolic_scratchpad_root_is_preserved_without_following_it(self) -> None:
        retained = self.pad.with_name("retained-scratchpad")
        self.pad.rename(retained)
        self.pad.symlink_to(retained, target_is_directory=True)
        self.run_hook()
        self.assertTrue(self.pad.is_symlink())
        self.assert_preserved()

    def test_symbolic_native_ancestor_is_not_followed(self) -> None:
        project = self.pad.parent.parent
        retained = project.with_name("retained-project")
        project.rename(retained)
        project.symlink_to(retained, target_is_directory=True)
        self.run_hook()
        self.assertTrue(project.is_symlink())
        self.assert_preserved()

    def test_custom_native_root_with_a_symbolic_ancestor_is_not_followed(self) -> None:
        alias = self.root / "native-alias"
        alias.symlink_to(self.native_root, target_is_directory=True)
        path = alias / self.pad.relative_to(self.native_root)
        result = self.run_hook({**self.payload, "scratchpad_dir": str(path)},
                               env={**self.env, "CLAUDE_CODE_TMPDIR": str(alias)})
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(alias.is_symlink())
        self.assert_preserved()

    def test_symbolic_namespace_does_not_delete_its_target(self) -> None:
        retained = self.pad / "retained-disposable"
        self.disposable.rename(retained)
        self.disposable.symlink_to(retained, target_is_directory=True)
        self.run_hook()
        self.assertTrue(self.disposable.is_symlink())
        self.assert_preserved()

    def test_symbolic_child_never_deletes_an_external_target(self) -> None:
        target = self.private_directory(self.root / "retain-external")
        sentinel = self.write(target / "retain.md", b"A link cannot authorize deleting its target.\n")
        (self.disposable / "external-link").symlink_to(target, target_is_directory=True)
        self.run_hook()
        self.assertEqual(sentinel.read_bytes(), b"A link cannot authorize deleting its target.\n")
        self.assertEqual(self.note.read_bytes(), b"Retain session notes.\n")
        self.assertTrue(self.other_file.exists())

    def test_fifo_namespace_refuses_without_waiting_for_a_writer(self) -> None:
        self.generated.unlink()
        self.disposable.rmdir()
        os.mkfifo(self.disposable, 0o600)
        self.run_hook()
        self.assertTrue(stat.S_ISFIFO(self.disposable.lstat().st_mode))
        self.assertTrue(self.note.exists())

    def test_fifo_child_does_not_block_or_escape_disposable_scope(self) -> None:
        os.mkfifo(self.disposable / "pipe", 0o600)
        self.run_hook()
        self.assertEqual(self.note.read_bytes(), b"Retain session notes.\n")
        self.assertTrue(self.other_file.exists())

    def test_insecure_namespace_modes_preserve_existing_contents(self) -> None:
        for mode in (0o720, 0o702, 0o777):
            with self.subTest(mode=oct(mode)):
                self.disposable.chmod(mode)
                self.run_hook()
                self.assert_preserved()
                self.assertEqual(stat.S_IMODE(self.disposable.stat().st_mode), mode)

    def test_owned_directory_readable_but_not_writable_by_others_is_safe(self) -> None:
        self.disposable.chmod(0o755)
        self.run_hook()
        self.assertFalse(self.disposable.exists())
        self.assertTrue(self.note.exists())

    def test_insecure_session_directory_preserves_existing_contents(self) -> None:
        self.pad.parent.chmod(0o777)
        self.run_hook()
        self.assert_preserved()
        self.assertEqual(stat.S_IMODE(self.pad.parent.stat().st_mode), 0o777)

    def test_invalid_json_nonobjects_and_oversized_input_preserve_contents(self) -> None:
        for raw in (b"", b"{", b"null", b"[]", b"true", b"1", b'"text"', b"\xff",
                    json.dumps({**self.payload, "padding": "x" * 2_097_152}).encode("utf-8")):
            with self.subTest(size=len(raw), prefix=raw[:12]):
                self.run_hook(raw=raw)
                self.assert_preserved()

    def test_excessive_json_nesting_skips_without_continuation(self) -> None:
        self.run_hook(raw=b"[" * 2048 + b"]" * 2048)
        self.assert_preserved()

    def test_foreign_namespace_owner_preserves_contents(self) -> None:
        module = load_hook()
        original = os.fstat
        selected = self.disposable.stat()
        def foreign_owner(descriptor: int) -> Any:
            info = original(descriptor)
            if (info.st_dev, info.st_ino) == (selected.st_dev, selected.st_ino):
                return SimpleNamespace(**{name: (os.getuid() + 1 if name == "st_uid" else getattr(info, name))
                                          for name in dir(info) if name.startswith("st_")})
            return info
        with patch.object(module.os, "fstat", side_effect=foreign_owner):
            self.call_hook(module)
        self.assert_preserved()

    def test_internal_deadline_retains_residuals_with_an_honest_status(self) -> None:
        module = load_hook()
        calls = []
        def clock() -> float:
            calls.append(True)
            return float(len(calls) - 1)
        result = self.result(module.cleanup(self.payload, self.env, clock=clock))
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(self.disposable.is_dir())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_deep_tree_refuses_or_leaves_contained_residuals(self) -> None:
        module = load_hook()
        target = self.disposable
        for _number in range(65):
            target = self.private_directory(target / "nested")
        self.write(target / "rebuild.tmp", b"Bounded deep-tree fixture.\n")
        result = self.result(module.cleanup(self.payload, self.env, clock=lambda: 0.0))
        self.assertIn(result["status"], ("skipped", "incomplete"))
        self.assertTrue(self.disposable.is_dir())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_entry_limit_reports_actual_removals_and_preserves_siblings(self) -> None:
        module = load_hook()
        files = [self.generated]
        for number in range(9):
            files.append(self.write(self.disposable / f"rebuild-{number}.tmp", b"Disposable bytes.\n"))
        with patch.object(module, "MAX_ENTRIES", 6):
            result = self.result(module.cleanup(self.payload, self.env, clock=lambda: 0.0))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["removed"], sum(not path.exists() for path in files))
        self.assertTrue(any(path.exists() for path in files))
        self.assertTrue(self.disposable.is_dir())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_deadline_after_a_real_removal_preserves_residuals_and_counts_effects(self) -> None:
        module = load_hook()
        files = [self.generated, self.write(self.disposable / "second.tmp", b"Disposable bytes.\n"),
                 self.write(self.disposable / "third.tmp", b"Disposable bytes.\n")]
        def clock() -> float:
            return 1.0 if any(not path.exists() for path in files) else 0.0
        result = self.result(module.cleanup(self.payload, self.env, clock=clock))
        self.assertEqual(result["status"], "incomplete")
        self.assertGreater(result["removed"], 0)
        self.assertEqual(result["removed"], sum(not path.exists() for path in files))
        self.assertTrue(any(path.exists() for path in files))
        self.assertTrue(self.disposable.is_dir())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_namespace_replaced_by_symlink_cannot_delete_the_replacement_target(self) -> None:
        module = load_hook()
        original = os.open
        target = self.private_directory(self.root / "race-target")
        sentinel = self.write(target / "retain.md", b"Preserve the replacement target.\n")
        replaced = []
        def replace_before_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if str(path) in (".coding-policy-disposable", str(self.disposable)) and not replaced:
                self.disposable.rename(self.pad / "retained-before-race")
                self.disposable.symlink_to(target, target_is_directory=True)
                replaced.append(True)
            return original(path, flags, *args, **kwargs)
        with patch.object(module.os, "open", side_effect=replace_before_open):
            self.call_hook(module)
        self.assertEqual(replaced, [True], "The race must actually replace the selected namespace")
        self.assertEqual(sentinel.read_bytes(), b"Preserve the replacement target.\n")
        self.assertTrue(self.disposable.is_symlink())
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_open_namespace_renamed_out_of_disposable_scope_preserves_both_trees(self) -> None:
        module = load_hook()
        original = os.scandir
        selected = self.disposable.stat()
        retained = self.pad / "retained-after-open"
        replaced = []
        def relocate_after_open(path: Any) -> Any:
            if isinstance(path, int) and not replaced:
                info = os.fstat(path)
                if (info.st_dev, info.st_ino) == (selected.st_dev, selected.st_ino):
                    self.disposable.rename(retained)
                    self.private_directory(self.disposable)
                    self.write(self.disposable / "replacement.tmp", b"The replacement has its own owner.\n")
                    replaced.append(True)
            return original(path)
        with patch.object(module.os, "scandir", side_effect=relocate_after_open):
            result = self.call_hook(module)
        self.assertEqual(replaced, [True], "The race must relocate the opened namespace")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["removed"], 0)
        self.assertEqual((retained / self.generated.name).read_bytes(), b"Reproducible fixture bytes.\n")
        self.assertEqual((self.disposable / "replacement.tmp").read_bytes(), b"The replacement has its own owner.\n")
        self.assertTrue(self.note.exists())
        self.assertTrue(self.other_file.exists())

    def test_initial_filesystem_failure_is_incomplete_and_warns_without_blocking_exit(self) -> None:
        module = load_hook()
        original = os.open
        denied = []
        def deny_namespace(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if str(path) in (".coding-policy-disposable", str(self.disposable)):
                denied.append(True)
                raise PermissionError("Owned fixture denial")
            return original(path, flags, *args, **kwargs)
        with patch.object(module.os, "open", side_effect=deny_namespace):
            result, stderr = self.call_main(module)
        self.assertEqual(denied, [True], "The fixture must actually reject the selected filesystem access")
        self.assertEqual((result["status"], result["reason"], result["removed"]),
                         ("incomplete", "filesystem_error", 0))
        self.assertTrue(stderr.strip(), "An actual failed cleanup must leave an actionable diagnostic")
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
