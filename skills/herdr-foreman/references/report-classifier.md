# Report Classifier and Report Gates

The owner of the answers below is code:
`skills/herdr-foreman/classify/classify-report.sh` and
`skills/herdr-foreman/classify/report_verdict.py` ask the questions and label a
report, and `skills/herdr-foreman/foreman/report_gates.py` decides and records
gates. This file says how the pieces fit, what a gate obliges, and how the
bands change. It restates no threshold, answer set or composition rule; those
are the owners' contracts.

## Labelling a Report

- Input: one delivered report, or a round's reports through
  `skills/herdr-foreman/classify/classify-reports.sh`.
- Output: one label per report, the JSON object the
  `skills/herdr-foreman/classify/report_verdict.py` docstring documents
  (verdict, per-question answers, the report's sha256, the question hash and
  the model id). The batch adds `unannotated`, one entry and reason per report
  that got no label.
- The questions live in `skills/herdr-foreman/classify/report-questions.json`.
  Changing one changes every label's `question` hash; update the file's
  `changed` date with it.
- Step 12 runs the batch, `skills/herdr-foreman/classify/classify-reports.sh`.
  Its exit contract is in that script's header. A failed classification lands
  in `unannotated` and is never fatal. Exit 2 is a usage error, and its output
  is never used as labels.
- The deterministic checks that refuse one report before any call are in the
  `skills/herdr-foreman/classify/classify-report.sh` header.

## Adapters

- Jev (TypeSafe System One) is the default. It needs `TYPESAFE_API_KEY`
  (`.env.example`). The client is
  `skills/herdr-foreman/classify/typesafe_client.py`, shared with the evidence
  assessor (#472).
- An unavailable Jev, or one whose client refuses the request (the report
  carries the key), produces no label. The report lands in `unannotated` and
  takes the reasoning path, a full read. No other classifier is asked.
- `--agent codex|claude|grok` runs an LLM adapter for measurement
  (`skills/herdr-foreman/classify/evaluate.sh`). An LLM label carries no
  probabilities and never gates.

## Report Gates

- `foreman report-gate-record --labels <classify-reports output>` records the
  gate each label earns and prints
  `{"schema_version": 2, "recorded": [...], "replayed": [...], "no_gate": [...]}`:
  `recorded` holds each new gate with its `report`, `level` and `reason`;
  `replayed` a gate already on record for the same report bytes and
  classification; `no_gate` each report its label leaves ungated, with the
  reason. Exit 1 records nothing and names the cause on stderr. The owner computes the level from the label's
  probabilities, the pinned model and the bands alone; the label's own
  `verdict` and `gate` are never inputs.
  A report whose bytes changed since classification is refused: reclassify it.
- `block` — the report cannot be accepted until
  `foreman report-gate-clear --report <path> (--evidence <report> --reason <why> | --decision <attention id>)`
  records why it does not block. `close-member` refuses an `accepted` ledger
  decision and `record-report` refuses an `approved` verdict while it is open.
  A `needs_work` or `blocked` decision is never refused by a block.
- `reread` — the report cannot be gated at all until a full re-read is
  recorded with
  `foreman report-gate-reread --report <path> --evidence <re-read report> --note <what it verified>`.
  Both `close-member` and `record-report` refuse while it is open.
- No gate — probabilities below the bands, an LLM label, an unpinned model, an
  unannotated report. The report is read and gated as it would be without a
  classifier.

### Who Resolves a Gate

The owner reads who resolved a gate from records it already holds, never
from the caller. The gated report's task and role come from the applied
dispatch whose supervision enrollment binds it; a report no enrollment binds
cannot be resolved until the enrollment is restored.

- Worker — `--evidence` names a report whose current bytes supervision
  observed (`report_observed`) or `recover-report` recovered after the gate
  was recorded, for another applied dispatch on the same task in the gated
  report's role. It clears a block or records a re-read.
- Judge — the same delivery evidence, for an applied dispatch of the pinned
  judge (`config.json` `judge`) in adjudication mode on the same task. It
  clears a block; it never records a re-read.
- Operator — `--decision` names an attention `decision` on the same task,
  resolved with the operator's `user_answer` after the gate was recorded. Its
  answer summary is the recorded reason; `--reason` is refused.
- `report-gate-clear` resolves only `block` gates and `report-gate-reread` only
  `reread` gates. A command with no open gate of its level refuses and names
  the other command; a report carrying both stays unaccepted until both are
  resolved.
- Anything else records nothing and names what is missing. The foreman
  records a resolution and never decides one.
- A gate never approves, accepts or skips a check. A recorded clear or re-read
  undoes it.
- `foreman report-gate-status [--report <path>]` lists open and resolved gates.

### Verdict Gates

The same store holds a second gate source (#646): a report whose owner-parsed
`VERDICT:` line is `blocking`. No label is involved and no label ever touches
one.

- `assess-specialist` (a replay included) and `record-report` record it,
  `source: verdict`, `level: block`, with the dispatch the verdict was recorded
  against. One gate per (report, bytes): a replay returns the existing gate
  whatever its status; new bytes record a new gate.
- It never refuses acceptance: `close-member` and `record-report` ignore it.
- `apply` refuses a fresh `release` dispatch on its task while it is open, dry
  runs included. A completed release replay is exempt.
- `report-gate-clear` clears it with `--decision` as above, or with
  `--evidence` naming the gated responsibility's approved re-check. A judge's
  report is refused: a ruling decides, and the re-check that cites it clears.
  The re-check predicate is `_rechecked`'s docstring.
- A clear on a report carrying both sources resolves every pending gate by its
  own source's rule, or none.

### Sidecar schema 2

`skills/herdr-foreman/foreman/report_gates.py` owns `<canonical selected state>.report-gates.json`:
`{"schema_version": 2, "state_path", "gates": [...]}`. Each gate carries
`schema_version`, `source` (`classifier`|`verdict`), `report` (resolved,
canonical path), `sha256`, `level`, `reason`, `dispatch`, `at`, `status`
(`open`|`cleared`|`reread`) and `resolution` (`null`, or `schema_version`, `at`, `action`, `by`
(`worker`|`judge`|`operator`; `worker`|`operator` for a verdict gate), `reason`, `evidence` (`attention` for an
operator, or `path`, `sha256` and `dispatch`)). A classifier gate also carries
`probabilities`, `model`, `question` and `bands`, and `dispatch` is `null`. A
verdict gate carries none of the four, is always `block`, and names its
`dispatch`. Every record is validated whole on every read. Writes
take the sidecar's own lock. `close-member` and `record-report` hold that lock
from their gate check through their commit, so a gate recorded meanwhile is
refused rather than slipped in between; `record-report` writes its verdict
gate under that same lock. A missing file is first use;
an unreadable or unsupported one, or a symlink at its path, refuses every
reader, never reading as no gates. A classifier replay is the same report bytes under the
same `model`, `question`, `bands` and `level`; any other classification of
those bytes records a new gate. `close-member` reads it and never writes it.

Schema 1 held classifier gates alone, without `source` or `dispatch`. Every
read goes through the owner, so the first read of a schema-1 document migrates
it: each gate becomes `source: classifier`, `dispatch: null`, its resolution's
`schema_version` becomes 2, and the document is rewritten under the sidecar
lock before it is returned. A read while another process holds that lock is
refused and retried, never served unmigrated. A reader at schema 1 meeting
a schema-2 document refuses it as unsupported rather than reading it as no
gates: for a gate store, "no usable prior state" would read as no gate, so the
lagging-reader default of `rules/stateful-artifacts.md` Migration Policy does
not apply here.

## Changing the Bands

The live bands are the named constants at the top of
`skills/herdr-foreman/foreman/report_gates.py`, with `BANDS_VERSION` naming
their calibration. They ship uncalibrated and conservative. Calibration
proposes; a reviewed pull request installs:

1. Export `TYPESAFE_API_KEY` in the shell that runs the evaluation.
2. Run `bash skills/herdr-foreman/classify/evaluate.sh --agent jev --results <labels.json>`.
3. Run `python3 skills/herdr-foreman/classify/scoring.py calibrate <labels.json>`.
   It prints proposed bands and the counts it used, and writes nothing. It
   refuses to propose from too few held-out labels; its held-out test and
   selection rules are in the `skills/herdr-foreman/classify/scoring.py`
   docstring and top-of-file constants.
4. Open a pull request that changes the constants and `BANDS_VERSION`, with the
   proposal's counts and the resulting false-gate counts in its CHANGELOG
   entry.

Recalibrate after every `JEV_MODEL` bump and every question change.
