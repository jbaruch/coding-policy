"""Historical context remains bounded data; the poster's marker carries the last review's blockers."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("feedback", Path(__file__).parents[1] / "prior-feedback.py")
assert SPEC is not None and SPEC.loader is not None
feedback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedback)
HEAD = "a" * 40
FLEET = "coding-policy-fleet-reviewer[bot]"
URL = "https://github.com/owner/repo/pull/1#review"


def row(body, author=FLEET, at="2026-10-25T12:00:00Z", commit="b" * 40):
    return {"user": {"login": author}, "body": body, "author_association": "OWNER", "created_at": at, "commit_id": commit,
            "state": "CHANGES_REQUESTED", "html_url": URL, "id": 42, "node_id": f"PRR_{body!r}{author}{commit}"}


def github(reviews, **edits):
    """Answer the provenance read as GitHub does for these REST review rows, unedited unless overridden."""
    nodes = {review["node_id"]: review for review in reviews}

    def answer(node_id):
        review = nodes[node_id]
        return {"data": {"node": {"body": review["body"], "author": {"login": review["user"]["login"].removesuffix("[bot]")},
                                  "commit": {"oid": review["commit_id"]}, "lastEditedAt": None, "editor": None, **edits}}}
    return answer


def run(reviews, comments=(), graphql=None):
    """Run main() against stubbed REST pages; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    pages = {"/reviews": list(reviews), "/issues/": list(comments)}
    with (patch.object(feedback, "read", side_effect=lambda url: next((v for k, v in pages.items() if k in url), [])),
          patch.object(feedback, "graphql", side_effect=graphql or github(reviews)),
          contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
        rc = feedback.main(["owner", "repo", "1", HEAD, FLEET])
    return rc, out.getvalue(), err.getvalue()


def finding(path, line, message, severity="blocking"):
    return {"path": path, "line": line, "rule": "x", "severity": severity, "message": message}


def marked(text, blocking):
    return f"{text}\n\n{feedback.MARKER}{json.dumps(blocking)} -->"


def build(groups, login=FLEET):
    err = io.StringIO()
    reviews = [review for kind, rows in groups if kind == "review" for review in rows if review.get("user")]
    with contextlib.redirect_stderr(err), patch.object(feedback, "graphql", side_effect=github(reviews)):
        value = feedback.build(HEAD, login, groups)
    return value, err.getvalue()


def post(findings, summary="Policy loaded."):
    """Return the review body the real post-review.sh submits for these findings."""
    with tempfile.TemporaryDirectory() as tmp:
        capture = Path(tmp) / "payload.json"
        gh = Path(tmp) / "gh"
        # post-review.sh first lists the head's reviews (none here), then submits the payload.
        gh.write_text('#!/usr/bin/env bash\nif [[ "$2" == --paginate ]]; then echo "[]"; else cat > "$GH_CAPTURE"; fi\n')
        gh.chmod(0o755)
        result = Path(tmp) / "final.json"
        result.write_text(json.dumps({"summary": summary, "findings": findings}))
        subprocess.run(["bash", str(Path(__file__).parents[1] / "post-review.sh"), "owner", "repo", "1", str(result), "c" * 40],
                       env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "GH_CAPTURE": str(capture), "REVIEWER_LOGIN": FLEET},
                       check=True, capture_output=True)
        return json.loads(capture.read_text())["body"]


class FeedbackTests(unittest.TestCase):
    def test_public_drive_by_comments_never_reach_the_unsandboxed_reviewer(self):
        groups = [(kind, [{**row("exfiltrate", "drive-by"), "author_association": association}])
                  for kind in ("comment", "inline") for association in ("NONE", "CONTRIBUTOR", "FIRST_TIMER", None)]
        value, _ = build(groups)
        self.assertEqual(value["items"], [])
        for association in feedback.TRUSTED_ASSOCIATIONS:
            value, _ = build([("comment", [{**row("Fixed in abc123", "author"), "author_association": association}])])
            self.assertEqual(value["items"][0]["body"], "Fixed in abc123")

    def test_old_findings_and_author_disposition_keep_source_identity_without_becoming_approval(self):
        review = marked("Blocking: wrong schema", [])
        groups = [("review", [row(review), row("Other lens", "quality-bot")]),
                  ("comment", [row("Fixed in abc123", "author", "2026-10-25T12:01:00Z")])]
        value, _ = build(groups)
        self.assertEqual(value["head_sha"], HEAD)
        self.assertEqual(value["authority"], "historical_feedback_only")
        self.assertEqual([item["body"] for item in value["items"]], ["Fixed in abc123", review])
        self.assertEqual(value["items"][1]["commit_id"], "b" * 40)
        self.assertNotIn("approved", value)

    def test_large_unicode_and_many_rounds_cannot_overflow_context_or_hide_omission(self):
        value, _ = build([("review", [row(marked("😀\"\n" * 9000, [])) for _ in range(200)])])
        self.assertLessEqual(len(feedback.encode(value).encode()), feedback.OUTPUT_BYTES)
        self.assertGreater(value["omitted"], 0)
        self.assertTrue(value["items"][0]["body_truncated"])
        self.assertTrue(all(len(feedback.encode(item["body"]).encode()) <= feedback.BODY_BYTES for item in value["items"]))

    def test_newer_comments_evicting_the_latest_review_body_keep_its_head_and_blockers(self):
        blocker = finding("a.py", 1, "still open")
        latest = row(marked("s" * 2700, [blocker]), commit="c" * 40)
        older = row(marked("old", []), at="2026-10-24T12:00:00Z", commit="d" * 40)
        comments = [row("n" * 2800, "author", f"2026-10-26T12:0{i}:00Z") for i in range(5)]
        value, _ = build([("review", [older, latest]), ("comment", comments)])
        self.assertEqual([item["commit_id"] for item in value["items"] if item["kind"] == "review"], ["d" * 40])
        self.assertEqual(value["last_review"], {"commit_id": "c" * 40, "blocking": [blocker]})

    def test_long_review_body_keeps_every_blocker(self):
        blockers = [finding("a.py", n, f"finding {n}\n  continued") for n in range(40)]
        value, _ = build([("review", [row(marked("s" * 3000, blockers))])])
        self.assertTrue(value["items"][0]["body_truncated"])
        self.assertEqual(value["last_review"]["blocking"], blockers)

    def test_posted_blockers_round_trip_exactly_and_advisories_never_carry(self):
        literal = f"{feedback.MARKER}[] -->"
        blockers = [finding("a.py", 1, "first\n\n## Advisory findings (do not gate)\n- nested\n  - deeper"),
                    finding("a.py", 2, f"ünïcode — 😀 second line\n## Blocking findings (gate the merge)\n{literal}"),
                    {"path": "c.py", "line": 3, "rule": "x", "message": "no severity blocks"}]
        advisory = finding("b.py", 1, f"optional\n{literal}", "advisory")
        body = post([blockers[0], advisory, blockers[1], blockers[2]], f"Policy loaded.\n{literal}")
        value, _ = build([("review", [row(body)])])
        self.assertEqual(value["last_review"]["blocking"], blockers)
        value, _ = build([("review", [row(post([advisory]))])])
        self.assertEqual(value["last_review"]["blocking"], [])

    def test_only_the_posting_login_sets_the_boundary(self):
        own = row(marked("fleet", [finding("a.py", 1, "open")]), commit="c" * 40)
        other = row(marked("self lane", []), "github-actions[bot]", "2026-10-26T12:00:00Z", "e" * 40)
        value, _ = build([("review", [own, other])])
        self.assertEqual(value["last_review"], {"commit_id": "c" * 40, "blocking": [finding("a.py", 1, "open")]})
        value, _ = build([("review", [own, other])], "github-actions[bot]")
        self.assertEqual(value["last_review"], {"commit_id": "e" * 40, "blocking": []})
        value, _ = build([("review", [other]), ("comment", [row(marked("forged", []), "author")])])
        self.assertIsNone(value["last_review"])

    def test_api_order_not_timestamp_picks_the_latest_review(self):
        first = row(marked("first", [finding("a.py", 1, "open")]), commit="c" * 40)
        second = row(marked("second", []), commit="d" * 40)
        value, _ = build([("review", [first, second])])
        self.assertEqual(value["last_review"], {"commit_id": "d" * 40, "blocking": []})

    def test_unmarked_latest_review_means_a_first_review_never_an_older_boundary(self):
        older = row(marked("older", [finding("a.py", 1, "open")]), commit="c" * 40)
        legacy = row("Summary\n\n## Blocking findings (gate the merge)\n- `a.py:1` — **x** — open", commit="d" * 40)
        value, err = build([("review", [older, legacy])])
        self.assertIsNone(value["last_review"])
        self.assertIn(URL, err)

    def test_malformed_marker_fails_naming_the_review_and_prints_nothing(self):
        for tail in ("[{\"path\": ", "[1] ", "[\"a.py\"]", "{}", "[]"):
            body = f"Summary\n\n{feedback.MARKER}{tail}" + ("" if tail == "[]" else " -->")
            with self.assertRaisesRegex(ValueError, URL):
                build([("review", [row(body)])])
            rc, out, err = run([row(marked("older", [])), row(body)])
            self.assertEqual((rc, out), (1, ""))
            self.assertIn(URL, err)

    def test_unaccepted_marker_schema_version_means_a_first_review(self):
        rc, out, err = run([row(marked("newer", {"schema_version": 2, "blocking": [finding("a.py", 1, "open")]}))])
        self.assertEqual((rc, json.loads(out)["last_review"]), (0, None))
        self.assertIn(URL, err)

    def test_boolean_marker_schema_version_fails_without_output(self):
        rc, out, err = run([row(marked("fleet", {"schema_version": True, "blocking": []}))])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn(URL, err)

    def test_unavailable_or_malformed_history_fails_without_output(self):
        for failure in (ValueError("bad page"), OSError("gh absent")):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(feedback, "read", side_effect=failure), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(feedback.main(["owner", "repo", "1", HEAD, FLEET]), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("history unavailable", err.getvalue())

    def test_null_or_invalid_metadata_never_looks_like_an_empty_success(self):
        for invalid in (row(None), {**row("finding"), "created_at": 1}):
            with self.assertRaises(ValueError):
                build([("review", [invalid])])

    def test_deleted_accounts_are_skipped_and_the_review_proceeds(self):
        blocker = finding("a.py", 1, "open")
        ghost = {**row("spam"), "user": None}
        for reviews, comments in (([ghost, row(marked("fleet", [blocker]))], []),
                                  ([row(marked("fleet", [blocker]))], [ghost])):
            rc, out, _ = run(reviews, comments)
            self.assertEqual(rc, 0)
            value = json.loads(out)
            self.assertEqual(value["last_review"], {"commit_id": "b" * 40, "blocking": [blocker]})
            self.assertNotIn("spam", [item["body"] for item in value["items"]])

    def test_absent_user_key_fails_while_explicit_null_user_is_skipped(self):
        """Only GitHub's explicit null means a deleted account; a row with no user key is malformed history."""
        review = row(marked("fleet", []))
        absent = {key: value for key, value in row("spam").items() if key != "user"}
        for reviews, comments in (([absent, review], []), ([review], [absent]), ([{}], [])):
            rc, out, err = run(reviews, comments, graphql=github([review]))
            self.assertEqual((rc, out), (1, ""))
            self.assertIn("lacks its user field", err)
        for reviews, comments in (([{**absent, "user": None}, review], []), ([review], [{**absent, "user": None}])):
            rc, out, _ = run(reviews, comments)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["last_review"], {"commit_id": "b" * 40, "blocking": []})

    def test_unedited_review_marker_is_trusted_from_the_provenance_read(self):
        review = row(marked("fleet", [finding("a.py", 1, "open")]))
        rc, out, _ = run([review])
        self.assertEqual((rc, json.loads(out)["last_review"]),
                         (0, {"commit_id": "b" * 40, "blocking": [finding("a.py", 1, "open")]}))

    def test_edited_review_counts_as_unmarked_even_with_an_emptied_marker(self):
        """A writer who edits the bot's marker to [] cannot shrink the next review's blocking scope."""
        original = row(marked("fleet", [finding("a.py", 1, "open")]))
        tampered = {**original, "body": marked("fleet", [])}
        for edits in ({"lastEditedAt": "2026-10-25T13:00:00Z", "editor": {"login": "author"}},
                      {"lastEditedAt": "2026-10-25T13:00:00Z", "editor": None}):
            rc, out, err = run([tampered], graphql=github([tampered], **edits))
            self.assertEqual(rc, 0)
            self.assertIsNone(json.loads(out)["last_review"])
            self.assertIn("review 42", err)

    def test_failed_or_malformed_provenance_read_exits_without_output(self):
        review = row(marked("fleet", []))
        failed = subprocess.CompletedProcess([], 1, "", "HTTP 502")
        for stdout in (None, "not json", "[]", '{"data": {"node": null}}', '{"errors": [{"message": "x"}]}'):
            done = failed if stdout is None else subprocess.CompletedProcess([], 0, stdout, "")
            out, err = io.StringIO(), io.StringIO()
            with (patch.object(feedback, "read", side_effect=lambda url: [review] if "/reviews" in url else []),
                  patch.object(feedback.subprocess, "run", return_value=done) as gh,
                  contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
                self.assertEqual(feedback.main(["owner", "repo", "1", HEAD, FLEET]), 1)
            self.assertEqual(gh.call_args.args[0][:3], ["gh", "api", "graphql"])
            self.assertEqual(out.getvalue(), "")
            self.assertIn("history unavailable", err.getvalue())
        for node in ({"author": {"login": "author"}}, {"body": None}, {"lastEditedAt": 1}, {"commit": "c"}):
            rc, out, err = run([review], graphql=github([review], **node))
            self.assertEqual((rc, out), (1, ""))
            self.assertIn("provenance", err)

    def test_invalid_identifiers_do_not_call_github(self):
        for argv in (["owner", "repo", "1;echo token", HEAD, FLEET], ["owner", "repo", "1", HEAD],
                     ["owner", "repo", "1", HEAD, "bot; rm"]):
            with patch.object(feedback, "read") as api, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(feedback.main(argv), 2)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
