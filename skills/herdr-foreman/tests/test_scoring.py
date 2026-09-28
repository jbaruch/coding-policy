"""Scoring labels against recorded verdicts, and calibrating Jev's bands from held-out labels."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "classify"))

import report_verdict  # noqa: E402 -- the classify dir is on sys.path only from here
import scoring  # noqa: E402
from foreman import report_gates  # noqa: E402

CHANGED = report_verdict.questions()["changed"]
QUESTION = report_verdict.question_hash()

IDS = ("names_open_item", "open_items_accepted", "open_items_out_of_scope", "concludes_nothing_blocks")


def jev(recorded, p_open, p_disposed=0.01, p_concludes=0.1):
    p = dict(zip(IDS, (p_open, p_disposed, p_disposed, p_concludes)))
    return {"agent": "jev", "model": report_gates.JEV_MODEL, "recorded": recorded, "source": "corpus",
            "question": QUESTION, "recorded_at": CHANGED + "T12:00:00+00:00",
            "verdict": "blocking", "answers": {qid: {"answer": report_gates.band(v), "p_yes": v} for qid, v in p.items()}}


class ScoreTest(unittest.TestCase):
    def test_per_question_truth_comes_from_the_recorded_verdict_or_the_fixture(self):
        rows = [jev("blocking", 0.99), jev("approved", 0.1, p_concludes=0.95),
                {**jev("approved", 0.1, p_concludes=0.95), "source": "fixture",
                 "expected_answers": {"names_open_item": "no", "concludes_nothing_blocks": "yes"}}]
        for row in rows:
            row["verdict"] = "blocking" if row["recorded"] == "blocking" else "approved"
        report = scoring.score(rows, 0, "jev", "pinned", {"since": "2026-09-27", "changed": "2026-09-27", "held_out": True})
        self.assertEqual((report["scored"], report["accuracy"]), (2, 1.0))
        # The blocking row fixes the open-item answers; the approved row, with
        # neither disposal answered yes, fixes its no-open-item path.
        self.assertEqual(report["per_question"]["names_open_item"]["determined"], 2)
        self.assertEqual(report["per_question"]["concludes_nothing_blocks"],
                         {"determined": 1, "agree": 1, "unclear": 0, "accuracy": 1.0})
        self.assertEqual(report["fixtures"]["per_question"]["concludes_nothing_blocks"]["accuracy"], 1.0)
        self.assertEqual(report["fixtures"]["flipped"], [])

    def test_an_approval_through_a_disposal_fixes_no_answer(self):
        row = jev("approved", 0.99, p_disposed=0.9)
        row["verdict"] = "approved"
        table = scoring.per_question([row])
        self.assertEqual([table[qid]["determined"] for qid in IDS], [0, 0, 0, 0])

    def test_a_wrong_conclusion_on_an_approval_is_scored_wrong(self):
        row = jev("approved", 0.1, p_concludes=0.1)
        row["verdict"] = "insufficient_evidence"
        self.assertEqual(scoring.per_question([row])["concludes_nothing_blocks"],
                         {"determined": 1, "agree": 0, "unclear": 0, "accuracy": 0.0})


class CalibrateTest(unittest.TestCase):
    def test_too_few_held_out_labels_calibrate_nothing(self):
        with self.assertRaises(SystemExit) as caught:
            scoring.calibrate([jev("blocking", 0.99)] * (scoring.MIN_CALIBRATION_REPORTS - 1))
        self.assertEqual(caught.exception.code, 2)

    def test_the_block_band_never_gates_a_recorded_approval(self):
        rows = ([jev("blocking", 0.995)] * 15 + [jev("blocking", 0.9)] * 5
                + [jev("approved", 0.93, p_concludes=0.9)] * 3 + [jev("approved", 0.05, p_concludes=0.9)] * 12)
        result = scoring.calibrate(rows)
        self.assertEqual(result["counts"]["labels"], 35)
        proposed = result["proposed"]
        self.assertEqual(proposed["block"]["gated_approved"], 0)
        self.assertEqual(proposed["block"]["gated_blocking"], 15)
        self.assertLessEqual(proposed["reread"]["gated_approved"], scoring.MAX_FALSE_REREAD_RATE * 15)
        self.assertEqual(proposed["annotation"]["missed_blockers"], 0)

    def test_llm_and_fixture_labels_are_not_calibration_data(self):
        rows = ([{**jev("blocking", 0.99), "agent": "claude"}] * 40 + [{**jev("blocking", 0.99), "source": "fixture"}] * 40
                + [{**jev("blocking", 0.99), "model": "jev-other"}] * 40)
        with self.assertRaises(SystemExit):
            scoring.calibrate(rows)

    def test_labels_that_are_not_held_out_are_excluded(self):
        good = [jev("blocking", 0.995)] * 20 + [jev("approved", 0.05, p_concludes=0.9)] * 10
        stale = [{**jev("blocking", 0.99), "recorded_at": "2026-01-01T00:00:00+00:00"}] * 40
        other = [{**jev("blocking", 0.99), "question": "0" * 64}] * 40
        undated = [{**jev("blocking", 0.99), "recorded_at": None}] * 40
        with self.assertRaises(SystemExit):
            scoring.calibrate(good[:-1] + stale + other + undated)
        result = scoring.calibrate(good + stale + other + undated)
        self.assertEqual((result["counts"]["labels"], result["counts"]["excluded"]), (30, 120))

    def test_calibrate_prints_a_proposal_and_writes_nothing(self):
        rows = [jev("blocking", 0.995)] * 20 + [jev("approved", 0.05, p_concludes=0.9)] * 10
        owner = Path(report_gates.__file__).read_bytes()
        with tempfile.TemporaryDirectory(prefix="calibrate-") as root:
            results = Path(root) / "results.json"
            results.write_text(json.dumps(rows))
            before = sorted(path.name for path in Path(root).iterdir())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(scoring.main(["calibrate", str(results)]), 0)
            self.assertEqual(sorted(path.name for path in Path(root).iterdir()), before)
        proposal = json.loads(out.getvalue())
        self.assertEqual(set(proposal["proposed"]), {"annotation", "block", "reread"})
        self.assertEqual(hashlib.sha256(Path(report_gates.__file__).read_bytes()).digest(),
                         hashlib.sha256(owner).digest())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
