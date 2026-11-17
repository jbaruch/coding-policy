"""Exercise context isolation, posting, failure and exact-head guarantees."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("claude_review", Path(__file__).resolve().parents[1] / "review.py")
assert SPEC and SPEC.loader
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)
API = reviewer.api
HEAD = "a" * 40
MARKER = "<!-- coding-policy:claude-quality-review:v1 -->"


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GH_TOKEN": "github-secret", "GITHUB_TOKEN": "other-secret",
                              "CLAUDE_CODE_OAUTH_TOKEN": "subscription-secret",
                              "REVIEWER_LOGIN": "jbaruch-claude-reviewer[bot]", "ANTHROPIC_API_KEY": "api-secret", "OPENAI_API_KEY": "openai-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.posts = []
        self.claude_calls = []
        self.api_reads = 0
        self.changed = False
        self.fork = False
        self.fetched = HEAD
        self.review_pages = [[]]
        self.lookup_fails = False
        self.output = {"type": "result", "subtype": "success", "is_error": False,
                       "structured_output": {"summary": "One concrete defect.", "findings": [
                           {"path": "app.py", "line": 2, "severity": "blocking", "message": "Empty input divides by zero."}]}}
        self.runner = patch.object(reviewer, "run", self.run_command)
        self.runner.start()
        self.addCleanup(self.runner.stop)
        self.api_patch = patch.object(reviewer, "api", self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def api(self, endpoint, payload=None):
        if payload is not None:
            self.posts.append(payload)
            return {"state": {"REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED", "APPROVE": "APPROVED"}[payload["event"]], "commit_id": payload["commit_id"], "html_url": "https://github.com/o/r/pull/1#review", "user": {"login": "jbaruch-claude-reviewer[bot]"}}
        self.api_reads += 1
        return {"state": "open", "base": {"ref": "main"},
                "head": {"sha": "b" * 40 if self.changed and self.api_reads > 1 else HEAD,
                         "repo": {"full_name": "fork/r" if self.fork else "o/r"}}}

    def run_command(self, args, *, cwd=None, env=None, data=None):
        if args[:2] == ["git", "clone"]:
            Path(args[-1]).mkdir()
        elif args[:2] == ["git", "rev-parse"]:
            return self.fetched + "\n"
        elif args[:2] == ["git", "diff"]:
            return "diff --git a/app.py b/app.py\n+return total / len(items)\n"
        elif args[0] == "claude":
            self.claude_calls.append((args, env, data))
            self.assertEqual(sorted(Path(cwd).iterdir()), [])
            return json.dumps(self.output)
        elif args[:2] == ["gh", "api"]:
            self.assertEqual(args[2:5], ["--paginate", "--slurp", "repos/o/r/pulls/1/reviews?per_page=100"])
            if self.lookup_fails:
                raise reviewer.ReviewError("gh failed (exit 1); check CI authentication, permissions and CLI availability")
            return json.dumps(self.review_pages)
        elif args[0] == "bash":
            return subprocess.run(args, text=True, check=True, capture_output=True).stdout
        return ""

    def review(self):
        return reviewer.review_pr("o", "r", "1", "main")

    def test_subscription_review_has_no_customizations_or_execution_tools(self):
        result = self.review()
        args, env, prompt = self.claude_calls[0]
        for flag in ("--safe-mode", "--restricted", "--disable-slash-commands",
                     "--strict-mcp-config", "--no-session-persistence"):
            self.assertIn(flag, args)
        self.assertNotIn("--bare", args)  # Bare mode does not support subscription auth.
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--model") + 1], reviewer.MODEL)
        self.assertIn("purely for code quality and bugs", args[args.index("--system-prompt") + 1])
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "subscription-secret")
        self.assertIn("return total", prompt)
        self.assertEqual(result["state"], "posted")
        self.assertEqual(self.posts[0]["event"], "REQUEST_CHANGES")
        self.assertEqual(self.posts[0]["commit_id"], HEAD)
        self.assertTrue(self.posts[0]["body"].startswith(MARKER + "\n"))
        self.assertIn("app.py:2", self.posts[0]["body"])

    def test_no_findings_is_still_a_posted_review(self):
        self.output["structured_output"] = {"summary": "No defects found.", "findings": []}
        self.assertEqual(self.review()["findings"], 0)
        self.assertEqual(len(self.posts), 1)

    def test_advisory_approval_clears_old_blocks_and_mixed_findings_request_changes(self):
        finding = self.output["structured_output"]["findings"][0]
        finding["severity"] = "advisory"
        finding["message"] = "[optional hardening] Add a recovery journal for hypothetical future consumers; no demonstrated defect."
        self.assertEqual(self.review()["blocking"], 0)
        self.assertEqual(self.posts[-1]["event"], "APPROVE")
        self.assertIn("## Advisory findings", self.posts[-1]["body"])
        self.output["structured_output"]["findings"].append(
            {**finding, "severity": "blocking", "message": "Empty input crashes."})
        self.assertEqual(self.review()["blocking"], 1)
        self.assertEqual(self.posts[-1]["event"], "REQUEST_CHANGES")
        self.assertIn("## Blocking findings", self.posts[-1]["body"])

    def earlier_review(self, state, commit=HEAD, login="jbaruch-claude-reviewer[bot]"):
        return {"id": 1, "user": {"login": login}, "state": state, "commit_id": commit, "body": "earlier"}

    def test_first_verdict_on_a_head_stands(self):
        for state in ("APPROVED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                # The matching review sits on the second page, so pagination is read whole.
                self.review_pages = [[self.earlier_review("COMMENTED", "c" * 40)], [self.earlier_review(state)]]
                with patch("sys.stderr") as stderr:
                    result = self.review()
                self.assertEqual(result, {"state": "already_reviewed", "reviewer": "claude", "head_sha": HEAD})
                self.assertEqual(self.posts, [])
                logged = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertIn(f"already reviewed o/r#1 at {HEAD}", logged)

    def test_older_head_other_reviewer_or_dismissed_review_does_not_suppress_the_post(self):
        # A quality review is never ruled-dismissed, so any dismissal, GitHub's
        # automatic stale-review dismissal included, lets a new review run.
        for review in (self.earlier_review("APPROVED", "c" * 40),
                       self.earlier_review("APPROVED", login="coding-policy-fleet-reviewer[bot]"),
                       self.earlier_review("DISMISSED")):
            with self.subTest(review=review):
                self.posts.clear()
                self.review_pages = [[review]]
                self.assertEqual(self.review()["state"], "posted")
                self.assertEqual(len(self.posts), 1)
                self.assertEqual(self.posts[0]["commit_id"], HEAD)

    def test_failed_review_lookup_fails_without_posting(self):
        self.lookup_fails = True
        with self.assertRaisesRegex(reviewer.ReviewError, "gh failed"):
            self.review()
        self.assertEqual(self.posts, [])
        with patch("sys.argv", ["review.py", "o", "r", "1", "main"]), patch("sys.stderr"), patch("sys.stdout"):
            self.assertEqual(reviewer.main(), 1)
        self.assertEqual(self.posts, [])

    def test_unreadable_review_list_fails_without_posting(self):
        for pages in ({"message": "Not Found"}, [{"id": 1}], [["not a review"]]):
            with self.subTest(pages=pages):
                self.review_pages = pages
                with self.assertRaisesRegex(reviewer.ReviewError, "unreadable review list"):
                    self.review()
        self.assertEqual(self.posts, [])

    def test_wrong_app_identity_is_refused_before_any_model_or_post(self):
        with patch.dict(os.environ, {"REVIEWER_LOGIN": "coding-policy-fleet-reviewer[bot]"}):
            with self.assertRaisesRegex(reviewer.ReviewError, "dedicated"):
                self.review()
        self.assertEqual(self.posts, [])
        self.assertEqual(self.claude_calls, [])

    def test_post_errors_never_become_a_clean_review(self):
        for event in ("APPROVE", "REQUEST_CHANGES"):
            with patch.object(reviewer, "run", side_effect=reviewer.ReviewError("post failed")) as run:
                with self.assertRaises(reviewer.ReviewError):
                    API("repos/o/r/pulls/1/reviews", {"event": event, "commit_id": HEAD, "body": "review"})
                self.assertEqual(run.call_count, 1)

    def test_invalid_output_never_becomes_a_clean_review(self):
        for output in ({"is_error": True}, {"is_error": False, "subtype": "success"},
                       {"is_error": False, "subtype": "error_max_turns"}):
            self.output = output
            with self.assertRaises(reviewer.ReviewError):
                self.review()
        self.assertEqual(self.posts, [])

    def test_credential_echo_never_posts(self):
        self.output["structured_output"]["summary"] = "subscription-secret"
        with self.assertRaisesRegex(reviewer.ReviewError, "credential"):
            self.review()
        self.assertEqual(self.posts, [])

    def test_head_move_during_review_refuses_stale_verdict(self):
        self.changed = True
        with self.assertRaisesRegex(reviewer.ReviewError, "stale"):
            self.review()
        self.assertEqual(self.posts, [])

    def test_head_move_during_fetch_runs_no_model(self):
        self.fetched = "b" * 40
        with self.assertRaisesRegex(reviewer.ReviewError, "during fetch"):
            self.review()
        self.assertEqual(self.claude_calls, [])

    def test_fork_receives_no_model_call(self):
        self.fork = True
        with self.assertRaisesRegex(reviewer.ReviewError, "same-repository"):
            self.review()
        self.assertEqual(self.claude_calls, [])

    def test_invalid_locations_fail_closed(self):
        for path, line in (("../secret", 1), ("/etc/passwd", 1), ("file.py", 0), ("file.py", True)):
            self.output["structured_output"]["findings"][0].update(path=path, line=line)
            with self.assertRaises(reviewer.ReviewError):
                self.review()
        self.assertEqual(self.posts, [])


class FailureDiagnosticsTest(unittest.TestCase):
    def test_failed_cli_emits_fixed_actionable_categories_without_echoing_output(self):
        cases = (
            ("authentication_error: secret-token", "authentication rejected"),
            ("OAuth token has expired: secret-token", "refresh the CI subscription credential"),
            ("Invalid API key secret-token", "authentication rejected"),
            ("You’ve hit your limit: private-source", "subscription usage limit reached"),
            ("You've hit your limit: private-source", "subscription usage limit reached"),
            ('{"status":429,"message":"usage limit: private-source"}', "provider rate or usage limit (HTTP 429)"),
            ("model_not_found: private-source", "model unavailable"),
            ("unknown option '--private-source'", "CLI argument rejected"),
            ("ECONNRESET private-source", "network or TLS failure"),
            ("unexpected private-source secret-token", "unclassified CLI failure"),
        )
        for output, expected in cases:
            for stdout, stderr in ((output, ""), ("", output)):
                with self.subTest(output=output, stream=bool(stdout)):
                    result = subprocess.CompletedProcess(["claude"], 1, stdout, stderr)
                    with patch.object(reviewer.subprocess, "run", return_value=result):
                        with self.assertRaises(reviewer.ReviewError) as caught:
                            reviewer.run(["claude", "--print"])
                    message = str(caught.exception)
                    self.assertIn(expected, message)
                    self.assertIn("exit 1", message)
                    self.assertNotIn("secret-token", message)
                    self.assertNotIn("private-source", message)

    def test_success_output_remains_unchanged(self):
        result = subprocess.CompletedProcess(["claude"], 0, '{"ok":true}', "")
        with patch.object(reviewer.subprocess, "run", return_value=result):
            self.assertEqual(reviewer.run(["claude", "--print"]), '{"ok":true}')

    def test_unknown_error_reports_only_fixed_vocabulary(self):
        message = reviewer.claude_failure_hint("private-source OAuth scope secret-token", "")
        self.assertTrue(message.endswith("fixed diagnostic markers: oauth,scope"))
        self.assertNotIn("secret-token", message)
        self.assertNotIn("private-source", message)

    def test_other_command_failures_do_not_claim_a_claude_diagnosis(self):
        result = subprocess.CompletedProcess(["git"], 128, "", "authentication_error secret-token")
        with patch.object(reviewer.subprocess, "run", return_value=result):
            with self.assertRaises(reviewer.ReviewError) as caught:
                reviewer.run(["git", "fetch"])
        self.assertIn("git failed (exit 128)", str(caught.exception))
        self.assertNotIn("authentication rejected", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
