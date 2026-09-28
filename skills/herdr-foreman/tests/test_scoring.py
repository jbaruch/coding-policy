"""Scoring labels against recorded verdicts, and calibrating Jev's bands from held-out labels."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "classify"))

import scoring  # noqa: E402 -- the classify dir is on sys.path only from here
from foreman import report_gates  # noqa: E402

IDS = ("names_open_item", "open_items_accepted", "open_items_out_of_scope", "concludes_nothing_blocks")


def jev(recorded, p_open, p_disposed=0.01, p_concludes=0.1):
    p = dict(zip(IDS, (p_open, p_disposed, p_disposed, p_concludes)))
    return {"agent": "jev", "model": report_gates.JEV_MODEL, "recorded": recorded, "source": "corpus",
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
        self.assertEqual(result["labels"], 35)
        self.assertEqual(result["block"]["gated_approved"], 0)
        self.assertEqual(result["block"]["gated_blocking"], 15)
        self.assertLessEqual(result["reread"]["gated_approved"], scoring.MAX_FALSE_REREAD_RATE * 15)
        self.assertEqual(result["annotation"]["missed_blockers"], 0)

    def test_llm_and_fixture_labels_are_not_calibration_data(self):
        rows = [{**jev("blocking", 0.99), "agent": "claude"}] * 40 + [{**jev("blocking", 0.99), "source": "fixture"}] * 40
        with self.assertRaises(SystemExit):
            scoring.calibrate(rows)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
