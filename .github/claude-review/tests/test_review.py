"""Exercise context isolation, posting, failure and exact-head guarantees."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("claude_review", Path(__file__).resolve().parents[1] / "review.py")
assert SPEC and SPEC.loader
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)
RUN = reviewer.run
API = reviewer.api
SUBPROCESS_RUN = subprocess.run
HEAD = "a" * 40
MARKER = "<!-- coding-policy:claude-quality-review:v1 -->"
REVIEW_FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/quality_review_5962464346.py"


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
            return RUN(args, data=data)
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
        self.assertEqual(
            self.posts[0]["body"],
            f"{MARKER}\n## Claude code review\n\nReviewed commit: {HEAD}. Code quality and bugs only.\n"
            "\n> No defects found.\n",
        )

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
        body = f"{MARKER}\n## Claude code review\n\nLooks good.\n\nReviewed commit: {commit}. Code quality and bugs only.\n"
        return {"id": 1, "submitted_at": "2020-01-01T00:00:00Z", "user": {"login": login},
                "state": state, "commit_id": commit, "body": body}

    def test_first_verdict_on_a_head_stands(self):
        for state in ("APPROVED", "COMMENTED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                # The matching review sits on the second page, so pagination is read whole.
                self.review_pages = [[self.earlier_review("COMMENTED", "c" * 40), {"id": 3, "user": None}],
                                     [{**self.earlier_review(state), "id": 2}]]
                with patch("sys.stderr") as stderr:
                    result = self.review()
                self.assertEqual(result, {"state": "already_reviewed", "reviewer": "claude", "head_sha": HEAD})
                self.assertEqual(self.posts, [])
                logged = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertIn(f"already reviewed o/r#1 at {HEAD}", logged)

    def test_misattributed_approval_allows_fresh_review(self):
        observed = json.loads(subprocess.check_output([sys.executable, REVIEW_FIXTURE]))
        self.fetched = observed["commit_id"]
        with patch(__name__ + ".HEAD", self.fetched):
            older = {**self.earlier_review("APPROVED", self.fetched), "id": observed["id"] + 2}
            self.review_pages = [[observed], [older]]
            self.assertEqual(self.review()["state"], "posted")
            self.review_pages.append([{**observed, "id": observed["id"] + 1,
                                       "body": self.posts[0]["body"], "state": "CHANGES_REQUESTED"}])
            self.assertEqual(self.review()["state"], "already_reviewed")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]["commit_id"], self.fetched)
        self.assertIn(f"Reviewed commit: {self.fetched}. Code quality and bugs only.", self.posts[0]["body"])

    def test_selector_cli_requires_login_and_reads_native_pages(self):
        selector = Path(__file__).resolve().parents[3] / "skills/release/latest_quality_review.py"
        command = [sys.executable, str(selector)]
        missing = subprocess.run(command, input="", text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(missing.stdout, "")
        self.assertIn("Usage: python3 latest_quality_review.py REVIEWER_LOGIN <", missing.stderr)
        self.assertNotIn("Traceback", missing.stderr)

        review = self.earlier_review("APPROVED")
        pages = '[{"id": 2, "user": null}]\n' + json.dumps([review])
        valid = subprocess.run([*command, review["user"]["login"]], input=pages,
                               text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(valid.stdout), [review])
        self.assertEqual(valid.stderr, "")

    def test_missing_or_ambiguous_provenance_allows_fresh_review(self):
        record = f"Reviewed commit: {HEAD}. Code quality and bugs only."
        for body in (None, record, MARKER, f"{MARKER}\n{record[:-1]}", f"{MARKER}\n{record}\n{record}",
                     f"{MARKER}\n{record}\nPrevious output:\r{record}",
                     f"{MARKER}\nReviewed commit: {'b' * 40}. Code quality and bugs only.\n{record}"):
            with self.subTest(body=body):
                older = self.earlier_review("APPROVED")
                self.review_pages = [[{**older, "id": 2, "body": body}], [older]]
                self.assertEqual(self.review()["state"], "posted")

    def test_model_metadata_in_summary_and_findings_round_trips(self):
        record = f"Reviewed commit: {HEAD}. Code quality and bugs only."
        self.output["structured_output"] = {
            "summary": f"Previous output:\n{record}",
            "findings": [{"path": "app.py", "line": 2, "severity": "advisory",
                          "message": "The log also contains\n  reviewed COMMIT: unknown."}],
        }
        self.assertEqual(self.review()["state"], "posted")
        body = self.posts[0]["body"]
        self.assertEqual(self.posts[0]["event"], "APPROVE")
        self.assertEqual(body.splitlines().count(record), 1)
        self.assertIn(f"> {record}", body)
        self.assertIn(">   reviewed COMMIT: unknown.", body)
        poll = Path(__file__).resolve().parents[3] / "skills/release/poll-pr-reviews.sh"
        submitted = {**self.earlier_review("APPROVED", self.posts[0]["commit_id"]), "body": body}
        resolved = subprocess.run(
            ["bash", "-c", 'source "$1"; resolve_review_against_head "$(cat)" "$2" quality',
             "bash", str(poll), HEAD], input=json.dumps(submitted), text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(resolved.stdout)["state"], "APPROVED")

    def test_markdown_containers_end_before_canonical_findings(self):
        record = f"Reviewed commit: {HEAD}. Code quality and bugs only."
        self.output["structured_output"] = {
            "summary": f"Log excerpt:\r\n\r\n```text\r{record}",
            "findings": [
                {"path": "app.py", "line": 2, "severity": "blocking",
                 "message": f"First finding.\r\r- The tool emitted\n  {record}\n- Keep this detail."},
                {"path": "other.py", "line": 3, "severity": "blocking",
                 "message": f"Code sample:\n\n```text\n{record}"},
                {"path": "docs\n`example`.md", "line": 4, "severity": "advisory",
                 "message": "**Reviewed commit:** cited."},
            ],
        }
        self.review()
        self.assertEqual(self.posts[0]["event"], "REQUEST_CHANGES")
        self.assertEqual(self.posts[0]["body"],
                         f"{MARKER}\n## Claude code review\n\n{record}\n\n"
                         f"> Log excerpt:\n>\n> ```text\n> {record}\n\n## Blocking findings\n"
                         "\n- ` app.py:2 `\n\n  > First finding.\n  >\n  > - The tool emitted\n"
                         f"  >   {record}\n  > - Keep this detail.\n\n- ` other.py:3 `\n\n"
                         f"  > Code sample:\n  >\n  > ```text\n  > {record}\n"
                         "\n## Advisory findings\n\n- `` docs\\n`example`.md:4 ``\n\n  > **Reviewed commit:** cited.\n")

    def test_cli_refuses_ambiguous_or_invalid_native_order_without_posting(self):
        observed = json.loads(subprocess.check_output([sys.executable, REVIEW_FIXTURE]))
        self.fetched = observed["commit_id"]
        bound = {**observed, "body": self.earlier_review("APPROVED", self.fetched)["body"]}
        for pages in ([[observed], [bound]], [[bound], [observed]],
                      [[bound], [{**observed, "user": None, "id": str(observed["id"])}]],
                      [[{**observed, "user": {"login": "similar-reviewer[bot]"}, "id": float(observed["id"])}], [bound]],
                      [[{**bound, "id": 5962464347.0}]], [[{**bound, "submitted_at": None}]],
                      [[{**bound, "submitted_at": "2026-02-30T00:00:00Z"}]]):
            self.review_pages = pages
            with (self.subTest(pages=pages), patch(__name__ + ".HEAD", self.fetched),
                  patch("sys.argv", ["review.py", "o", "r", "1", "main"]), patch("sys.stderr") as stderr):
                self.assertEqual(reviewer.main(), 1)
                diagnostic = str(stderr.write.call_args_list)
                self.assertIn("ordering unresolved for o/r#1", diagnostic)
                self.assertIn("gh api --paginate repos/o/r/pulls/1/reviews", diagnostic)
                self.assertNotIn(observed["body"], diagnostic)
                self.assertFalse(self.posts)

    def test_malformed_attribution_prevents_reusing_an_approval(self):
        bound = self.earlier_review("APPROVED")
        rows = [{"id": 7}, {"id": 7, "user": []}, {"id": 7, "user": {}}]
        rows.extend({"id": 7, "user": {"login": login}} for login in (False, "", "a" * 101, "a\x7f"))
        for row in rows:
            with self.subTest(row=row):
                self.review_pages = [[bound], [row]]
                with self.assertRaisesRegex(reviewer.ReviewError, "ordering unresolved for o/r#1"):
                    self.review()
                self.assertFalse(self.posts)

    def test_native_integer_id_breaks_equal_fractional_time_ties(self):
        bound = self.earlier_review("APPROVED")
        # Adjacent native IDs above binary64 precision must stay distinct.
        bound.update(id=9007199254740992, submitted_at="2020-01-01T00:00:00.1Z")
        mismatch = {**bound, "id": 9007199254740993, "submitted_at": "2020-01-01T00:00:00.100000Z",
                    "body": bound["body"].replace(HEAD, "b" * 40)}
        for pages in ([[bound], [mismatch]], [[mismatch], [bound]]):
            self.review_pages = pages
            self.assertEqual(self.review()["state"], "posted")
        self.assertEqual(len(self.posts), 2)

    def test_incompatible_helper_responses_fail_visibly_without_posting(self):
        for returncode, response in ((0, 'true'), (0, 'false'), (0, '{"quality_login":"reviewer[bot]"}'),
                                     (0, 'APPROVED'), (0, '"UNKNOWN"'), (0, 'null'),
                                     (0, '"APPROVED" "none"'), (0, ''), (2, '')):
            def helper(args, **kwargs):
                if args[0] == "bash" and len(args) > 2:
                    self.assertEqual(args[2:], ["state", HEAD])
                    return subprocess.CompletedProcess(args, returncode, stdout=response)
                return SUBPROCESS_RUN(args, **kwargs)

            with self.subTest(returncode=returncode, response=response), patch.object(
                reviewer.subprocess, "run", side_effect=helper,
            ):
                diagnostic = "restore the matching review helper" if returncode else "JSON quality state"
                with self.assertRaisesRegex(reviewer.ReviewError, diagnostic):
                    self.review()
                self.assertEqual(self.posts, [])

    def test_older_head_other_reviewer_or_dismissed_review_does_not_suppress_the_post(self):
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
