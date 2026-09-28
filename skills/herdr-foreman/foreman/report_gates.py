"""Report gates: a classifier label may add friction to accepting a report, never remove it.

The report classifier (`classify/`) answers atomic questions about a worker
report. Only a Jev label carries a probability per question, and only such a
label can gate. This module owns everything deterministic about that gate: the
pinned Jev model the bands belong to, the band constants, the decision from
probabilities to a gate level, and the sidecar record of every gate and its
resolution.

Levels:
  `block`  -- high confidence the report names an open item it does not
              dispose of. The report cannot be accepted (a ledger `accepted`
              decision closed through `close-member`, or an `approved`
              `record-report`) until a recorded clear names its reason, from
              the worker role that owns the finding, the judge on a contested
              one, or the operator.
  `reread` -- medium confidence. The report cannot be gated at all until a
              recorded full re-read by the worker whose report it is.
The foreman records resolutions; it never decides one.
  none     -- low confidence, `insufficient_evidence`, a fallback LLM label,
              an unpinned model, any error: no gate effect, and the report is
              read and gated exactly as without a classifier.
A gate never approves, accepts, or skips a check. A recorded clear or re-read
undoes it (rules/script-delegation.md Bounded Classification).

Band calibration: `BANDS_VERSION` names the calibration the constants came
from. They ship uncalibrated and deliberately conservative, so an
uncalibrated model rarely gates; `references/report-classifier.md` carries the
calibration procedure, which replaces them from held-out labelled data.

Sidecar (`<state>.report-gates.json`, schema in state-schema.md, Report Gates):
  {"schema_version": 1, "state_path": "<canonical state>", "gates": [<gate>...]}
"""

import copy
import errno
from contextlib import contextmanager
import hashlib
import json
import os
from datetime import timezone
from pathlib import Path
from typing import NoReturn

from . import runnable
from .chronology import timestamp
from .errors import StateError, UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1

#: The Jev model version the bands below belong to, and the one the report
#: classifier pins. Bands are per model version, so the two move together.
#: Renewal: with the classifier's other pins, on the capability table's
#: cadence (`INTERVAL` in foreman/capabilities.py, weekly). Compare against
#: https://docs.typesafe.ai/models; a bump lands only with a fresh calibration
#: (references/report-classifier.md) on reports the new model has not seen.
JEV_MODEL = "jev-1.13.0"

#: Which calibration produced the constants below. `uncalibrated` until the
#: procedure in references/report-classifier.md runs on live held-out data.
BANDS_VERSION = "uncalibrated-2026-09-27"

#: Annotation bands: P(yes) at or above YES_AT reads `yes`, at or below NO_AT
#: reads `no`, anything between reads `unclear`.
YES_AT = 0.7
NO_AT = 0.3

#: Gate bands. `block` needs P(open item) at or above BLOCK_OPEN_AT with both
#: disposals (accepted, out of scope) at or below BLOCK_DISPOSED_AT; `reread`
#: needs REREAD_OPEN_AT and REREAD_DISPOSED_AT. Conservative while
#: uncalibrated: an uncalibrated model should rarely gate.
BLOCK_OPEN_AT = 0.98
BLOCK_DISPOSED_AT = 0.05
REREAD_OPEN_AT = 0.90
REREAD_DISPOSED_AT = 0.20

#: The atomic question ids the gate reads (classify/report-questions.json).
OPEN = "names_open_item"
DISPOSALS = ("open_items_accepted", "open_items_out_of_scope")
GATE_QUESTIONS = (OPEN, *DISPOSALS)

LEVELS = ("block", "reread")
#: Who may clear a block: the worker role that owns the finding, the judge on
#: a contested one, the operator. The foreman records it and never decides it.
CLEARERS = ("worker", "judge", "operator")
#: Who performs a mandatory re-read: the worker whose role owns the report.
REREADERS = ("worker",)
CLEAR_COMMAND = "report-gate-clear --report <path> --by worker|judge|operator --reason <why it does not block> [--evidence <report carrying the reason>]"
REREAD_COMMAND = "report-gate-reread --report <path> --evidence <re-read report> --note <what it verified>"
COMMANDS = {"report-gate-record", "report-gate-reread", "report-gate-clear", "report-gate-status"}


def _fail(message) -> NoReturn:
    raise UsageError(message, {})


def canonical_state(path):
    return Path(path).expanduser().resolve()


def storage_path(path):
    return Path(str(canonical_state(path)) + ".report-gates.json")


def band(p_yes):
    """The annotation answer for one probability."""
    if p_yes >= YES_AT:
        return "yes"
    if p_yes <= NO_AT:
        return "no"
    return "unclear"


def decide(label):
    """The gate level a label earns, with the reason; never trusts the label's own claims."""
    def none(reason):
        return {"level": None, "reason": reason}
    if not isinstance(label, dict):
        return none("not a label")
    if label.get("agent") != "jev" or label.get("fallback"):
        return none("only a Jev label carries probabilities; an LLM or fallback label never gates")
    if label.get("model") != JEV_MODEL:
        return none("label model {!r} is not the pinned {}; its probabilities are outside the bands".format(
            label.get("model"), JEV_MODEL))
    if label.get("verdict") == "insufficient_evidence":
        return none("insufficient_evidence never gates")
    answers = label.get("answers")
    if not isinstance(answers, dict):
        return none("label carries no answers")
    p = {}
    for qid in GATE_QUESTIONS:
        row = answers.get(qid)
        value = row.get("p_yes") if isinstance(row, dict) else None
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            return none("label carries no probability for {}".format(qid))
        p[qid] = float(value)
    disposed = max(p[qid] for qid in DISPOSALS)
    if p[OPEN] >= BLOCK_OPEN_AT and disposed <= BLOCK_DISPOSED_AT:
        return {"level": "block", "reason": "high confidence the report names an open item it does not dispose of",
                "probabilities": p}
    if p[OPEN] >= REREAD_OPEN_AT and disposed <= REREAD_DISPOSED_AT:
        return {"level": "reread", "reason": "medium confidence the report names an open item it does not dispose of",
                "probabilities": p}
    return none("below the gate bands")


def _utc(value):
    return timestamp(value, "Report gate timestamp").astimezone(timezone.utc).isoformat()


def _report_key(path):
    if not isinstance(path, str) or not path:
        _fail("Name the report by its path.")
    return str(Path(path).expanduser().resolve())


def _digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        _fail("Cannot read report {}: {}. Restore it, then classify it again.".format(path, exc))


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        _fail("{} needs 1-4000 characters naming what you verified.".format(label))
    return value


def load(path):
    """Read and validate only. Missing is first use; malformed never reads as empty."""
    target = storage_path(path)
    empty = {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)), "gates": []}
    linked = StateError("Report gate sidecar {} is a symlink, not the owner's file; restore the regular file at "
                        "that path before gating.".format(target), {})
    if target.is_symlink():
        raise linked
    # Opened without following a link, so one swapped in after the probe is refused too.
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise linked from None
        raise StateError("Cannot read report gates {}: {}. Preserve its bytes and restore access; an unreadable "
                         "gate record is never read as no gates.".format(target, exc), {}) from None
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            document = json.loads(handle.read())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise StateError("Cannot read report gates {}: {}. Preserve its bytes and restore access; an unreadable "
                         "gate record is never read as no gates.".format(target, exc), {}) from None
    if (not isinstance(document, dict) or set(document) != {"schema_version", "state_path", "gates"}
            or type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION
            or document["state_path"] != str(canonical_state(path)) or not isinstance(document["gates"], list)):
        raise StateError("Report gates {} has an unsupported schema or state identity; preserve it and update "
                         "the owner, never replace it with an empty record.".format(target), {})
    for index, gate in enumerate(document["gates"]):
        problem = _gate_problem(gate)
        if problem:
            raise StateError("Report gates {} record {} is malformed ({}); preserve the file and restore or repair "
                             "that record; a malformed gate is never read as no gate.".format(target, index, problem),
                             {"record": index})
    return document


GATE_FIELDS = frozenset({"schema_version", "report", "sha256", "level", "reason", "probabilities", "model",
                         "question", "bands", "at", "status", "resolution"})
RESOLUTION_FIELDS = frozenset({"schema_version", "at", "action", "by", "reason", "evidence"})
#: The status each resolution action leaves.
RESOLVED_STATUS = {"clear": "cleared", "reread": "reread"}


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _gate_problem(gate):
    """What is wrong with one saved gate record, or None."""
    if not isinstance(gate, dict) or set(gate) != GATE_FIELDS:
        return "fields other than {}".format(", ".join(sorted(GATE_FIELDS)))
    if type(gate["schema_version"]) is not int or gate["schema_version"] != SCHEMA_VERSION:
        return "unsupported schema_version"
    if not _nonempty(gate["report"]) or not Path(gate["report"]).is_absolute():
        return "report is not an absolute path"
    if os.path.normpath(gate["report"]) != gate["report"]:
        return "report is not a canonical path"
    if not _sha(gate["sha256"]):
        return "sha256 is not a lowercase sha256"
    if gate["level"] not in LEVELS:
        return "unknown level"
    if not all(_nonempty(gate[key]) for key in ("reason", "model", "bands", "at")):
        return "reason, model, bands or at is empty"
    if gate["question"] is not None and not isinstance(gate["question"], str):
        return "question is not a string"
    probabilities = gate["probabilities"]
    if (not isinstance(probabilities, dict) or set(probabilities) != set(GATE_QUESTIONS)
            or any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1
                   for p in probabilities.values())):
        return "probabilities do not cover the gate questions in [0, 1]"
    resolution = gate["resolution"]
    if gate["status"] == "open":
        return None if resolution is None else "an open gate carries a resolution"
    if gate["status"] not in RESOLVED_STATUS.values():
        return "unknown status"
    if not isinstance(resolution, dict) or set(resolution) != RESOLUTION_FIELDS:
        return "a resolved gate lacks its resolution"
    if type(resolution["schema_version"]) is not int or resolution["schema_version"] != SCHEMA_VERSION:
        return "unsupported resolution schema_version"
    if RESOLVED_STATUS.get(resolution["action"]) != gate["status"]:
        return "the resolution action and the gate's status disagree"
    if resolution["by"] not in (CLEARERS if resolution["action"] == "clear" else REREADERS):
        return "resolution names who may not resolve it"
    if not _nonempty(resolution["reason"]) or not _nonempty(resolution["at"]):
        return "resolution reason or at is empty"
    evidence = resolution["evidence"]
    if evidence is None:
        return None if resolution["by"] == "operator" else "a worker or judge resolution cites no report"
    if (not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"} or not _nonempty(evidence["path"])
            or not _sha(evidence["sha256"])):
        return "resolution evidence is not a path and sha256"
    return None


@contextmanager
def holding(path):
    """Hold the sidecar lock across a gate check and the decision it guards.

    A caller checking `require_clear` and then committing an acceptance holds
    this for both, so no gate can be recorded in between. Writers take the
    same non-blocking lock and refuse while it is held.
    """
    with state_lock(storage_path(path)):
        yield


def open_gates(path, report):
    key = _report_key(report)
    return [gate for gate in load(path)["gates"] if gate["report"] == key and gate["status"] == "open"]


def require_clear(path, report, accepting):
    """Refuse gating a report whose open gate forbids it.

    An open `reread` refuses any gating decision until the re-read is
    recorded; an open `block` refuses acceptance until a recorded clear.
    """
    for gate in open_gates(path, report):
        if gate["level"] == "reread":
            raise UsageError("Report {} carries an open re-read gate ({}). Dispatch a full re-read to the worker whose "
                             "report it is, then record it with `{}` before gating it.".format(
                                 gate["report"], gate["reason"], runnable.command(REREAD_COMMAND)),
                             {"gate": copy.deepcopy(gate)})
        if gate["level"] == "block" and accepting:
            raise UsageError("Report {} carries an open block gate ({}); it cannot be accepted until the block is "
                             "cleared with a recorded reason from the worker that owns the finding, the judge or the "
                             "operator: `{}`.".format(gate["report"], gate["reason"], runnable.command(CLEAR_COMMAND)),
                             {"gate": copy.deepcopy(gate)})


def _labels(data):
    labels = data.get("labels") if isinstance(data, dict) and "labels" in data else [data]
    if not isinstance(labels, list):
        _fail("The labels file holds one classify-report.sh label or a classify-reports.sh batch.")
    for label in labels:
        if (not isinstance(label, dict) or not isinstance(label.get("report"), str)
                or not isinstance(label.get("sha256"), str)):
            _fail("Every label needs its report path and sha256; pass classify-report(s).sh output unchanged.")
    return labels


def record(path, data, at):
    """Record the gates a batch of labels earns; identical report bytes replay."""
    at = _utc(at)
    labels = _labels(data)
    # Every label is checked before anything is written: a report rewritten
    # since it was classified is reclassified, never gated on stale bytes.
    for label in labels:
        if _digest(label["report"]) != label["sha256"]:
            _fail("Report {} changed since it was classified; run classify-report(s).sh on it again.".format(label["report"]))
    recorded, replayed, ungated = [], [], []
    with state_lock(storage_path(path)):
        document = load(path)
        for label in labels:
            key = _report_key(label["report"])
            decision = decide(label)
            if decision["level"] is None:
                ungated.append({"report": key, "reason": decision["reason"]})
                continue
            # A replay is the same bytes under the same classification; a new model,
            # question, bands version or level records a fresh gate.
            prior = next((gate for gate in document["gates"]
                          if gate["report"] == key and gate["sha256"] == label["sha256"]
                          and gate["level"] == decision["level"] and gate["model"] == label["model"]
                          and gate["question"] == label.get("question") and gate["bands"] == BANDS_VERSION), None)
            if prior is not None:
                replayed.append(copy.deepcopy(prior))
                continue
            gate = {"schema_version": SCHEMA_VERSION, "report": key, "sha256": label["sha256"],
                    "level": decision["level"], "reason": decision["reason"],
                    "probabilities": decision["probabilities"], "model": label["model"],
                    "question": label.get("question"), "bands": BANDS_VERSION, "at": at,
                    "status": "open", "resolution": None}
            document["gates"].append(gate)
            recorded.append(copy.deepcopy(gate))
        if recorded:
            save_state(storage_path(path), document)
    return {"schema_version": SCHEMA_VERSION, "recorded": recorded, "replayed": replayed, "no_gate": ungated}


def _evidence(value, by):
    """The report that carries a worker's or judge's reason, bound to its bytes."""
    if value is None:
        if by == "operator":
            return None
        _fail("A {} resolution cites the report that carries it; pass --evidence <report path>.".format(by))
    path = _report_key(value)
    return {"path": path, "sha256": _digest(path)}


def resolve(path, report, action, reason, by, at, evidence=None):
    """Record a re-read or a clear against the report's open gates.

    A clear comes from the worker role that owns the finding, the judge on a
    contested finding, or the operator. A re-read is a worker's full re-read,
    dispatched to the role whose report it is. A worker or judge resolution
    cites the report that carries it.
    """
    at = _utc(at)
    key = _report_key(report)
    _text(reason, "The re-read note" if action == "reread" else "The clear reason")
    if by not in (CLEARERS if action == "clear" else REREADERS):
        _fail("A clear comes from the owning worker, the judge or the operator; a re-read from the worker whose "
              "report it is. Pass --by {}.".format("|".join(CLEARERS if action == "clear" else REREADERS)))
    cited = _evidence(evidence, by)
    # A re-reader is never the operator, so a re-read always cites a report.
    if action == "reread" and (cited is None or cited["path"] == key):
        _fail("A re-read cites the re-reading worker's own report, not the gated report itself.")
    with state_lock(storage_path(path)):
        document = load(path)
        pending = [gate for gate in document["gates"] if gate["report"] == key and gate["status"] == "open"]
        if not pending:
            _fail("Report {} has no open gate; `{}` lists what is open.".format(key, runnable.command("report-gate-status")))
        if action == "reread" and any(gate["level"] == "block" for gate in pending):
            _fail("Report {} carries a block gate; a re-read does not clear it. Record the reason it does not block "
                  "with `{}`.".format(key, runnable.command(CLEAR_COMMAND)))
        for gate in pending:
            gate["status"] = "cleared" if action == "clear" else "reread"
            gate["resolution"] = {"schema_version": SCHEMA_VERSION, "at": at, "action": action, "by": by,
                                  "reason": reason, "evidence": cited}
        save_state(storage_path(path), document)
    return {"schema_version": SCHEMA_VERSION, "resolved": copy.deepcopy(pending)}


def status(path, report=None):
    gates = load(path)["gates"]
    if report is not None:
        key = _report_key(report)
        gates = [gate for gate in gates if gate["report"] == key]
    return {"schema_version": SCHEMA_VERSION, "bands": BANDS_VERSION, "model": JEV_MODEL,
            "open": [copy.deepcopy(gate) for gate in gates if gate["status"] == "open"],
            "resolved": [copy.deepcopy(gate) for gate in gates if gate["status"] != "open"]}


def register_commands(sub, common):
    parser = sub.add_parser("report-gate-record", parents=[common],
                            help="Record the gates a round's classifier labels earn.")
    parser.add_argument("--labels", required=True, help="classify-reports.sh (or classify-report.sh) output file.")
    parser.add_argument("--now", metavar="ISO8601")
    parser = sub.add_parser("report-gate-reread", parents=[common],
                            help="Record the mandatory full re-read that clears a report's re-read gate.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--evidence", required=True, help="The re-reading worker's report.")
    parser.add_argument("--now", metavar="ISO8601")
    parser = sub.add_parser("report-gate-clear", parents=[common],
                            help="Clear a report's gate with the recorded reason it does not block.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--by", required=True, choices=CLEARERS)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence", help="The report carrying the reason; required unless --by operator.")
    parser.add_argument("--now", metavar="ISO8601")
    parser = sub.add_parser("report-gate-status", parents=[common], help="List open and resolved report gates.")
    parser.add_argument("--report")


def run_command(args, state_path, now):
    if args.command == "report-gate-record":
        try:
            data = json.loads(Path(args.labels).expanduser().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise UsageError("Cannot read labels {}: {}. Save classify-reports.sh stdout to a file and pass it "
                             "unchanged.".format(args.labels, exc), {}) from None
        return record(state_path, data, args.now or now)
    if args.command == "report-gate-reread":
        return resolve(state_path, args.report, "reread", args.note, "worker", args.now or now, args.evidence)
    if args.command == "report-gate-clear":
        return resolve(state_path, args.report, "clear", args.reason, args.by, args.now or now, args.evidence)
    return status(state_path, args.report)
