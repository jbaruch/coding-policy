#!/usr/bin/env python3
"""Score classifier labels against recorded verdicts, and calibrate Jev's bands.

Usage:
  scoring.py score <results.json> <failed> <agent> <model> <split.json>
      Print the accuracy report evaluate.sh emits.
  scoring.py calibrate <results.json>
      Sweep the annotation and gate bands over held-out Jev labels and print
      the recommended constants for foreman/report_gates.py. Exit 2 when the
      results hold fewer than MIN_CALIBRATION_REPORTS usable Jev labels.

`results.json` is a list of labels (report_verdict.py), each with `recorded`
(the verdict the foreman recorded, or a fixture's expected verdict), `source`
(`corpus` or `fixture`) and, for a fixture, `expected_answers`.

Per-question accuracy needs a truth per question, and the corpus records only
the verdict. A recorded `blocking` determines three answers (an open item is
named, not accepted, not out of scope); a recorded `approved` determines none,
since two different answer paths compose it. A fixture's expected answers
determine the rest. A question with no determined row reports accuracy null.
"""

import collections
import json
import sys
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from classify import report_verdict  # noqa: E402 -- the skill dir is on sys.path only from here
from foreman import report_gates  # noqa: E402

#: Truths a recorded verdict determines, per question.
IMPLIED = {"blocking": {"names_open_item": "yes", "open_items_accepted": "no", "open_items_out_of_scope": "no"},
           "approved": {}}
#: Fewer held-out labels than this calibrate nothing: a band fitted on a
#: handful of reports is noise with a decimal point.
MIN_CALIBRATION_REPORTS = 30
#: Share of recorded-`approved` reports a band may gate. A block on an
#: approved report is friction a recorded clear must remove; a re-read is cheap.
MAX_FALSE_BLOCK_RATE = 0.0
MAX_FALSE_REREAD_RATE = 0.10
ANNOTATION_GRID = [round(0.05 * step, 2) for step in range(1, 20)]
OPEN_GRID = [round(0.80 + 0.01 * step, 2) for step in range(20)]
DISPOSED_GRID = [0.01, 0.05, 0.10, 0.20, 0.30]


def fail(message) -> NoReturn:
    sys.stderr.write("scoring: {}\n".format(message))
    raise SystemExit(2)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail("cannot read {}: {}".format(path, exc))


def per_question(rows):
    table = {}
    for qid in report_verdict.question_ids():
        determined = agree = unclear = 0
        for row in rows:
            truth = (row.get("expected_answers") or {}).get(qid) or IMPLIED.get(row["recorded"], {}).get(qid)
            if truth is None:
                continue
            determined += 1
            answer = row["answers"][qid]["answer"]
            unclear += answer == "unclear"
            agree += answer == truth
        table[qid] = {"determined": determined, "agree": agree, "unclear": unclear,
                      "accuracy": round(agree / determined, 4) if determined else None}
    return table


def confusion(rows):
    counts = collections.Counter("{}__{}".format(row["recorded"], row["verdict"]) for row in rows)
    agree = sum(n for key, n in counts.items() if key.split("__")[0] == key.split("__")[1])
    return dict(counts), (round(agree / len(rows), 4) if rows else None)


def score(results, failed, agent, model, split):
    corpus = [row for row in results if row.get("source") != "fixture"]
    fixtures = [row for row in results if row.get("source") == "fixture"]
    matrix, accuracy = confusion(corpus)
    return {"schema_version": 2, "agent": agent, "model": corpus[0]["model"] if corpus else model,
            "split": split, "scored": len(corpus), "failed": failed, "accuracy": accuracy,
            "confusion": matrix, "per_question": per_question(corpus),
            "fixtures": {"scored": len(fixtures), "per_question": per_question(fixtures),
                         "flipped": [{"report": row["report"], "expected": row["recorded"], "predicted": row["verdict"]}
                                     for row in fixtures if row["recorded"] != row["verdict"]]},
            "disagreements": [{"report": row["report"], "recorded": row["recorded"], "predicted": row["verdict"],
                               "evidence": row.get("evidence", ""), "reason": row.get("reason", "")}
                              for row in corpus if row["recorded"] != row["verdict"]]}


def _band(p, yes_at, no_at):
    return "yes" if p >= yes_at else "no" if p <= no_at else "unclear"


def calibrate(results):
    ids = report_verdict.question_ids()
    rows = [row for row in results if row.get("agent") == "jev" and row.get("source") != "fixture"
            and all(isinstance((row.get("answers") or {}).get(qid, {}).get("p_yes"), (int, float)) for qid in ids)]
    if len(rows) < MIN_CALIBRATION_REPORTS:
        fail("{} usable held-out Jev labels; calibration needs at least {}. Run evaluate.sh --agent jev --since "
             "<date> --results <file> on more reports.".format(len(rows), MIN_CALIBRATION_REPORTS))
    annotation = []
    for yes_at in ANNOTATION_GRID:
        for no_at in ANNOTATION_GRID:
            if no_at >= yes_at:
                continue
            verdicts = [report_verdict.compose({qid: _band(row["answers"][qid]["p_yes"], yes_at, no_at) for qid in ids})[0]
                        for row in rows]
            missed = sum(v == "approved" and row["recorded"] == "blocking" for v, row in zip(verdicts, rows))
            agree = sum(v == row["recorded"] for v, row in zip(verdicts, rows))
            abstain = sum(v == "insufficient_evidence" for v in verdicts)
            annotation.append({"YES_AT": yes_at, "NO_AT": no_at, "missed_blockers": missed,
                               "accuracy": round(agree / len(rows), 4), "abstained": abstain})
    annotation.sort(key=lambda r: (r["missed_blockers"], -r["accuracy"], r["abstained"]))
    approved = [row for row in rows if row["recorded"] == "approved"]
    blocking = [row for row in rows if row["recorded"] == "blocking"]

    def gated(group, open_at, disposed_at):
        return sum(row["answers"][report_gates.OPEN]["p_yes"] >= open_at
                   and max(row["answers"][qid]["p_yes"] for qid in report_gates.DISPOSALS) <= disposed_at
                   for row in group)

    gates = []
    for open_at in OPEN_GRID:
        for disposed_at in DISPOSED_GRID:
            gates.append({"OPEN_AT": open_at, "DISPOSED_AT": disposed_at,
                          "gated_approved": gated(approved, open_at, disposed_at),
                          "gated_blocking": gated(blocking, open_at, disposed_at)})

    def pick(max_rate):
        ceiling = max_rate * len(approved)
        fits = [row for row in gates if row["gated_approved"] <= ceiling]
        fits.sort(key=lambda r: (-r["gated_blocking"], -r["OPEN_AT"], r["DISPOSED_AT"]))
        return fits[0] if fits else None

    return {"schema_version": 1, "labels": len(rows), "approved": len(approved), "blocking": len(blocking),
            "model": rows[0]["model"], "annotation": annotation[0], "block": pick(MAX_FALSE_BLOCK_RATE),
            "reread": pick(MAX_FALSE_REREAD_RATE), "annotation_top": annotation[:5]}


def main(argv):
    if len(argv) == 6 and argv[0] == "score":
        results = read_json(argv[1])
        try:
            failed = int(argv[2])
        except ValueError:
            fail("the failure count must be an integer")
        print(json.dumps(score(results, failed, argv[3], argv[4], read_json(argv[5])), sort_keys=True))
        return 0
    if len(argv) == 2 and argv[0] == "calibrate":
        print(json.dumps(calibrate(read_json(argv[1])), sort_keys=True))
        return 0
    fail("usage: scoring.py score <results> <failed> <agent> <model> <split.json> | calibrate <results>")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
