"""All native adapters enforce the same reset input proof before model work."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foreman import foreman_reset as reset, memory, reset_input_hook as hook, supervision as store
from tests.test_foreman_reset import FakeClient, SESSION, CLEARED, PANE

AT = "2026-10-01T00:00:00+00:00"
CHILD = {"pid": 2121, "identity": "claimed-child"}
FOREGROUND = [{"pid": 4242, "identity": "started-once"}]


class ResetHookTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = self.directory / "state.json"
        self.root = self.directory / "bindings"
        self.proof = self.directory / "ledger.md"
        self.proof.write_text("Continue the recorded task.\n")
        memory.stow(self.state, {"id": "round-7", "capture": "Resume release.", "unresolved_work": ["Step 14"],
                               "gaps": [], "required_reads": [str(self.proof)]}, AT)
        self.who = store.identity(SESSION["value"], str(self.directory), "1", pane_id=PANE)
        store.bind(self.state, self.who, AT, root=self.root)
        self.plan = {"pane_id": PANE, "stow": "round-7"}
        reset.schedule(self.state, self.plan, AT, lambda: CHILD["pid"], native_session=SESSION,
                       probe=lambda pid: CHILD, alive=lambda process: True)
        reset.claim(self.state, self.plan, CHILD)
        with patch("foreman.foreman_reset.process_identity", return_value=CHILD):
            reset.arm_resume(self.state, PANE, "round-7", SESSION, FOREGROUND)
        self.payload = {"session_id": CLEARED, "cwd": str(self.directory),
                        "prompt": reset.guarded_resume("round-7", self.state, SESSION, FOREGROUND)}
        self.env = {"HERDR_ENV": "1", "HERDR_PANE_ID": PANE}

    def invoke(self, *, kind="codex", payload=None, env=None, client=None, probe=lambda pid: CHILD):
        client = client or FakeClient(["idle"], kind=kind)
        with patch("foreman.foreman_reset.process_identity", side_effect=client.identify):
            return hook.check(self.payload if payload is None else payload, self.env if env is None else env,
                              AT, client=client, root=self.root, probe=probe)

    def test_all_three_agents_transfer_the_same_verified_owner_and_refuse_replay(self):
        # Each subtest has independent owner files and a fixed native payload.
        for kind in ("claude", "codex", "grok"):
            self.setUp()
            with self.subTest(kind=kind):
                self.assertEqual(self.invoke(kind=kind), hook.acceptance())
                data = store.load(self.state)
                self.assertEqual(data["binding"]["identity"]["value"], CLEARED)
                self.assertEqual(data["events"], [])
                self.assertEqual(self.invoke(kind=kind)["decision"], "block")

    def test_an_accepted_receipt_stays_consumed_even_if_the_old_binding_is_restored(self):
        self.assertEqual(self.invoke(), hook.acceptance())
        store.bind(self.state, self.who, AT, root=self.root)
        self.assertEqual(self.invoke()["decision"], "block")
        self.assertEqual(store.load(self.state)["binding"]["identity"], self.who)

    def test_old_session_wrong_pane_or_cwd_missing_environment_and_dead_child_refuse(self):
        for payload, env, probe in (
                ({**self.payload, "session_id": SESSION["value"]}, self.env, lambda pid: CHILD),
                ({**self.payload, "cwd": "/different"}, self.env, lambda pid: CHILD),
                (self.payload, {**self.env, "HERDR_PANE_ID": "w9:p9"}, lambda pid: CHILD),
                (self.payload, {}, lambda pid: CHILD),
                (self.payload, self.env, lambda pid: None)):
            with self.subTest(payload=payload["session_id"], env=env):
                self.assertEqual(self.invoke(payload=payload, env=env, probe=probe)["decision"], "block")
                self.assertEqual(store.load(self.state)["binding"]["identity"], self.who)

    def test_changed_stow_replacement_process_and_forged_process_pin_refuse(self):
        replaced = FakeClient(["idle"], pids=[5151])
        self.assertEqual(self.invoke(client=replaced)["decision"], "block")
        forged = {**self.payload, "prompt": reset.guarded_resume("round-7", self.state, SESSION,
                            [{"pid": 5151, "identity": "started-once"}])}
        self.assertEqual(self.invoke(payload=forged, client=replaced)["decision"], "block")
        self.proof.write_text("Changed after stow.\n")
        self.assertEqual(self.invoke()["decision"], "block")

    def test_changed_prompt_unknown_wire_or_missing_arm_refuse(self):
        for prompt in (self.payload["prompt"] + "additional work", "normal\n" + reset.RESET_RECEIPT_PREFIX + "{}"):
            self.assertEqual(self.invoke(payload={**self.payload, "prompt": prompt})["decision"], "block")
        path = reset.record_path(self.state)
        document = json.loads(path.read_text())
        document["resets"][0]["foreground"] = None
        path.write_text(json.dumps(document))
        self.assertEqual(self.invoke()["decision"], "block")

    def test_normal_prompts_do_not_read_or_write_owner_state(self):
        self.assertIsNone(self.invoke(payload={"prompt": "Fix the test"}, env={}))

    def test_a_forged_locator_cannot_create_directories_or_lock_files(self):
        foreign = self.directory / "never-created" / "owner.json"
        prompt = reset.guarded_resume("round-7", foreign, SESSION, FOREGROUND)
        self.assertEqual(self.invoke(payload={**self.payload, "prompt": prompt})["decision"], "block")
        self.assertFalse(foreign.parent.exists())

    def test_the_read_only_candidate_is_reverified_after_locking(self):
        probes = iter((CHILD, None))
        self.assertEqual(self.invoke(probe=lambda pid: next(probes))["decision"], "block")
        self.assertEqual(store.load(self.state)["binding"]["identity"], self.who)

    def test_partial_binding_transfer_never_admits_or_retries_the_input(self):
        with patch("foreman.reset_input_hook.save_state", side_effect=OSError("fixture storage failure")):
            self.assertEqual(self.invoke()["decision"], "block")
        self.assertEqual(store.load(self.state)["binding"]["identity"]["value"], CLEARED)
        row = json.loads(reset.record_path(self.state).read_text())["resets"][0]
        self.assertIsNone(row["accepted_session"])
        self.assertIsNone(reset.accepted_resume(self.state, PANE, "round-7", SESSION))
        self.assertEqual(self.invoke()["decision"], "block")

    def test_checkout_and_inherited_pythonpath_cannot_shadow_the_installed_verifier(self):
        shadow = self.directory / "foreman"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("")
        marker = self.directory / "checkout-executed"
        (shadow / "reset_input_hook.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nprint('unverified allow')\n")
        inherited = self.directory / "inherited"
        inherited.mkdir()
        inherited_marker = self.directory / "inherited-executed"
        (inherited / "json.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(inherited_marker)) + ").touch()\nraise RuntimeError('shadow')\n")
        script = ROOT.parents[1] / "hooks/herdr-reset-input.sh"
        result = subprocess.run(["bash", str(script)], cwd=self.directory,
                                env={**os.environ, "PYTHONPATH": str(inherited)},
                                input=json.dumps({"prompt": "Ordinary prompt"}),
                                capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(inherited_marker.exists())

    def test_native_claude_long_paste_frame_preserves_exact_inner_input(self):
        wrapped = '<pasted_content id="f0fc">\n' + self.payload["prompt"] + '\n</pasted_content id="f0fc">'
        self.assertEqual(self.invoke(kind="claude", payload={**self.payload, "prompt": wrapped}), hook.acceptance())
        self.assertEqual(store.load(self.state)["binding"]["identity"]["value"], CLEARED)
        self.assertEqual(self.invoke(kind="claude", payload={**self.payload, "prompt": wrapped})["decision"], "block")

    def test_mismatched_paste_frames_and_extra_inner_content_are_not_authority(self):
        for opening, closing, inner in (("f0fc", "other", self.payload["prompt"]),
                                       ("f0fc", "f0fc", self.payload["prompt"] + "\nAdditional task")):
            wrapped = '<pasted_content id="' + opening + '">\n' + inner + '\n</pasted_content id="' + closing + '">'
            self.assertEqual(self.invoke(kind="claude", payload={**self.payload, "prompt": wrapped})["decision"], "block")
            self.assertEqual(store.load(self.state)["binding"]["identity"], self.who)

    def test_an_unrelated_new_binding_cannot_substitute_for_hook_acceptance(self):
        new = {**self.who, "value": CLEARED}
        store.bind(self.state, new, AT, root=self.root)
        self.assertIsNone(reset.accepted_resume(self.state, PANE, "round-7", SESSION))
        self.assertEqual(self.invoke()["decision"], "block")

    def test_only_the_claimed_child_can_arm_and_its_saved_pins_cannot_be_overwritten(self):
        path = reset.record_path(self.state)
        document = json.loads(path.read_text())
        document["resets"][0]["foreground"] = None
        path.write_text(json.dumps(document))
        with patch("foreman.foreman_reset.process_identity", return_value={"pid": 9999, "identity": "another"}):
            with self.assertRaisesRegex(reset.UsageError, "cannot be armed"):
                reset.arm_resume(self.state, PANE, "round-7", SESSION, FOREGROUND)
        with patch("foreman.foreman_reset.process_identity", return_value=CHILD):
            reset.arm_resume(self.state, PANE, "round-7", SESSION, FOREGROUND)
            with self.assertRaisesRegex(reset.UsageError, "cannot be armed"):
                reset.arm_resume(self.state, PANE, "round-7", SESSION, [{"pid": 5151, "identity": "another"}])

    def test_native_adapters_are_shared_and_quoted_in_both_manifests(self):
        manifest = json.loads((ROOT.parents[1] / ".tessl-plugin/plugin.json").read_text())
        commands = []
        for agent in ("claude-code", "codex"):
            commands.append(manifest["nativeHooks"][agent]["UserPromptSubmit"][0]["hooks"][0]["command"])
        self.assertEqual(commands, ['bash "${TESSL_PLUGIN_DIR}/hooks/herdr-reset-input.sh"'] * 2)
        # Grok imports the Claude-compatible hook surface (native validation
        # is documented beside the owner; no competing Grok implementation).
        script = ROOT.parents[1] / "hooks/herdr-reset-input.sh"
        result = subprocess.run(["bash", str(script)], input=json.dumps({"prompt": "Ordinary prompt"}),
                                capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        alias = self.directory / "plugin with spaces"
        alias.symlink_to(ROOT.parents[1], target_is_directory=True)
        spaced = subprocess.run(["bash", str(alias / "hooks/herdr-reset-input.sh")], input=json.dumps({"prompt": "Ordinary prompt"}),
                                capture_output=True, text=True, check=False)
        self.assertEqual((spaced.returncode, spaced.stdout), (0, ""), spaced.stderr)


if __name__ == "__main__":
    unittest.main()
