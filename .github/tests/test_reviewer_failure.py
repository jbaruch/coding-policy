import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reviewer_failure as failure


class ReviewerFailureTest(unittest.TestCase):
    def test_both_providers_distinguish_limit_auth_and_other_failures(self):
        for source, category in [
            ("You've hit your usage limit private-source secret-token", "subscription usage limit"),
            ('{"error":{"code":"usage_limit_reached"}}', "subscription usage limit"),
            ("rate_limit_exceeded private-source", "provider rate or usage limit"),
            ("authentication_error secret-token", "authentication rejected"),
            ("401 unauthorized secret-token", "authentication rejected"),
            ("refresh_token_reused secret-token", "authentication rejected"),
            ("ECONNRESET secret-token", "network or TLS"),
            ("private-source secret-token", "unclassified CLI failure"),
        ]:
            for stdout, stderr in [(source, ""), ("", source)]:
                with self.subTest(source=source, stdout=bool(stdout)):
                    hint = failure.reviewer_failure_hint(stdout, stderr)
                    self.assertIn(category, hint)
                    self.assertNotIn("secret-token", hint)
                    self.assertNotIn("private-source", hint)

    def test_codex_failure_keeps_status_and_never_echoes_child_output(self):
        from io import StringIO
        for rc in [1, 17]:
            result = subprocess.CompletedProcess(["codex"], rc, "You've hit your usage limit secret-token", "private-source")
            with patch.object(failure.subprocess, "run", return_value=result), patch("sys.stderr", new_callable=StringIO) as stderr, patch("sys.stdout", new_callable=StringIO) as stdout:
                self.assertEqual(failure.main(["run", "codex", "--", "codex", "exec"]), rc)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("subscription usage limit", stderr.getvalue())
                self.assertNotIn("secret-token", stderr.getvalue())
                self.assertNotIn("private-source", stderr.getvalue())

    def test_success_passes_arguments_and_prompt_stream_without_replaying_output(self):
        from io import StringIO
        result = subprocess.CompletedProcess(["codex"], 0, "private-source", "secret-token")
        args = ["codex", "exec", "--output-last-message", "/tmp/result.json", "-"]
        with patch.object(failure.subprocess, "run", return_value=result) as run, patch("sys.stdout", new_callable=StringIO) as output:
            self.assertEqual(failure.main(["run", "codex", "--", *args]), 0)
            self.assertEqual(run.call_args.args[0], args)
            self.assertNotIn("input", run.call_args.kwargs)
            self.assertEqual(json.loads(output.getvalue()), {"reviewer":"codex", "cli_completed":True})

    def test_launch_failure_is_actionable_and_invalid_invocations_do_not_run(self):
        from io import StringIO
        with patch.object(failure.subprocess, "run", side_effect=FileNotFoundError("private-source")), patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(failure.main(["run", "codex", "--", "codex"]), 127)
            self.assertNotIn("private-source", output.getvalue())
        with patch.object(failure.subprocess, "run") as run, patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(failure.main(["run", "codex", "--", "other"]), 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
