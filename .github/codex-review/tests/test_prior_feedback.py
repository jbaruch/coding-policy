"""Historical context remains bounded data, including on degraded API reads."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("feedback", Path(__file__).parents[1] / "prior-feedback.py")
assert SPEC is not None and SPEC.loader is not None
feedback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedback)
HEAD = "a" * 40


def row(body, author="coding-policy-fleet-reviewer[bot]", at="2026-10-25T12:00:00Z"):
    return {"user": {"login": author}, "body": body, "created_at": at, "commit_id": "b" * 40,
            "state": "CHANGES_REQUESTED", "html_url": "https://github.com/owner/repo/pull/1#review"}


class FeedbackTests(unittest.TestCase):
    def test_old_findings_and_author_disposition_keep_source_identity_without_becoming_approval(self):
        groups = [("review", [row("Blocking: wrong schema"), row("Other lens", "quality-bot")]),
                  ("comment", [row("Fixed in abc123", "author", "2026-10-25T12:01:00Z")])]
        value = feedback.build(HEAD, groups)
        self.assertEqual(value["head_sha"], HEAD)
        self.assertEqual(value["authority"], "historical_feedback_only")
        self.assertEqual([item["body"] for item in value["items"]], ["Fixed in abc123", "Blocking: wrong schema"])
        self.assertEqual(value["items"][1]["commit_id"], "b" * 40)
        self.assertNotIn("approved", value)

    def test_large_unicode_and_many_rounds_cannot_overflow_context_or_hide_omission(self):
        value = feedback.build(HEAD, [("review", [row("😀\"\n" * 9000) for _ in range(200)])])
        self.assertLessEqual(len(feedback.encode(value).encode()), feedback.OUTPUT_BYTES)
        self.assertGreater(value["omitted"], 0)
        self.assertTrue(value["items"][0]["body_truncated"])
        self.assertTrue(all(len(feedback.encode(item["body"]).encode()) <= feedback.BODY_BYTES for item in value["items"]))

    def test_unavailable_or_malformed_history_is_explicit_and_warns(self):
        for failure in (ValueError("bad page"), OSError("gh absent")):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(feedback, "read", side_effect=failure), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(feedback.main(["owner", "repo", "1", HEAD]), 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "unavailable")
            self.assertIn("complete current-head review", err.getvalue())

    def test_null_or_invalid_metadata_never_looks_like_an_empty_success(self):
        for invalid in (row(None), {**row("finding"), "created_at": 1}):
            with self.assertRaises(ValueError):
                feedback.build(HEAD, [("review", [invalid])])

    def test_invalid_identifiers_do_not_call_github(self):
        with patch.object(feedback, "read") as api, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(feedback.main(["owner", "repo", "1;echo token", HEAD]), 2)
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
