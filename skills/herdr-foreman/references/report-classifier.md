# Report Classifier and Report Gates

The owner of the answers below is code: `skills/herdr-foreman/classify/` asks
the questions and composes the verdict, and
`skills/herdr-foreman/foreman/report_gates.py` decides and records gates. This
file says how the pieces fit, what a gate obliges, and how the bands are
calibrated. It restates no threshold; the constants live at the top of
`report_gates.py`.

## Questions and Verdict

- `classify/report-questions.json` holds the atomic questions and their
  criteria. A model answers each one `yes`, `no` or `unclear`.
- `classify/report_verdict.py` `compose` turns the answers into `blocking`,
  `approved` or `insufficient_evidence`. No model sees that policy.
- Deterministic checks run first. The report travels as the `report` field of
  a JSON object between marker lines keyed to its own sha256, so a report
  cannot forge its delimiter. Every quote an LLM returns must be a passage of
  the report, or the label is `insufficient_evidence`.
- Changing a question changes the label's `question` hash. Update the
  `changed` date in `report-questions.json` with it, so `evaluate.sh` scores
  the change only on reports recorded afterwards.

## Adapters and Fallback

- Jev (TypeSafe System One) answers each question as one Noul with P(yes). It
  needs `TYPESAFE_API_KEY` (`.env.example`). The client is
  `classify/typesafe_client.py`, shared with the evidence assessor (#472).
- With no `--agent`, an unavailable Jev falls back to Claude. The fallback is
  printed on stderr and recorded in the label's `fallback`.
- An LLM label carries no probabilities. It annotates and never gates.

## Report Gates

- `foreman report-gate-record --labels <classify-reports output>` records the
  gate each label earns. The owner recomputes the level from the label's
  probabilities and the pinned model; it never trusts the label's own `gate`.
  A report whose bytes changed since classification is refused: reclassify it.
- `block` — the report cannot be accepted until
  `foreman report-gate-clear --report <path> --by foreman|operator --reason <why>`
  records why it does not block. `close-member` refuses an `accepted` ledger
  decision and `record-report` refuses an `approved` verdict while it is open.
  A `needs_work` or `blocked` decision is never refused by a block.
- `reread` — the report cannot be gated at all until
  `foreman report-gate-reread --report <path> --note <what you verified>`
  records a full re-read. Both `close-member` and `record-report` refuse while
  it is open.
- No gate — low confidence, `insufficient_evidence`, a fallback or LLM label,
  an unpinned model, a failed call. The report is read and gated as it would
  be without a classifier.
- A gate never approves, accepts or skips a check. A recorded clear or re-read
  undoes it.
- `foreman report-gate-status [--report <path>]` lists open and resolved gates.

### Sidecar schema 1

`foreman/report_gates.py` owns `<canonical selected state>.report-gates.json`:
`{"schema_version": 1, "state_path", "gates": [...]}`. Each gate carries
`schema_version`, `report` (resolved path), `sha256`, `level`, `reason`,
`probabilities`, `model`, `question`, `bands`, `at`, `status`
(`open`|`cleared`|`reread`) and `resolution` (`null`, or `at`, `action`, `by`,
`reason`). Writes take the sidecar's own lock. A missing file is first use;
an unreadable or unsupported one refuses every reader, never reading as no
gates. `close-member` and `record-report` read it and never write it.

## Calibrating the Bands

The bands ship as `BANDS_VERSION = "uncalibrated-..."` with conservative
values, so an uncalibrated model rarely gates. Calibrate them on data the
questions were not written against:

1. Export `TYPESAFE_API_KEY` in the shell that runs the evaluation.
2. Run `bash skills/herdr-foreman/classify/evaluate.sh --agent jev --results <labels.json>`.
   The default split starts at the questions' `changed` date; the output's
   `split.held_out` must be `true`.
3. Run `python3 skills/herdr-foreman/classify/scoring.py calibrate <labels.json>`.
   It refuses fewer labels than `MIN_CALIBRATION_REPORTS`; collect more rounds
   and repeat. Its selection rules are the constants at the top of
   `classify/scoring.py`.
4. Copy the recommended `annotation`, `block` and `reread` values into the
   constants in `foreman/report_gates.py` and set `BANDS_VERSION` to the
   calibration date and label count.
5. Record the held-out size, the chosen values and the resulting false-gate
   counts in the CHANGELOG entry.

Recalibrate after every `JEV_MODEL` bump and every question change: bands are
per model version and per question wording.
