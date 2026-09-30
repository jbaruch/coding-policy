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
              `record-report`) until a recorded clear names its reason.
  `reread` -- medium confidence. The report cannot be gated at all until a
              recorded full re-read.
Who resolved a gate is read from the owners' records, never from the caller
(`resolve`): a delivered report of the gated report's role or the pinned
adjudicating judge on the same task, or the operator's resolved attention
decision. The foreman records resolutions; it never decides one.
  none     -- probabilities below the bands, an LLM label, an unpinned model,
              a report the classifier did not annotate, any error: no gate
              effect, and the report is read and gated exactly as without a
              classifier. The label's composed verdict never decides a level.
A gate never approves, accepts, or skips a check. A recorded clear or re-read
undoes it (rules/script-delegation.md Bounded Classification).

Band calibration: `BANDS_VERSION` names the calibration the constants came
from. They ship uncalibrated and deliberately conservative, so an
uncalibrated model rarely gates. `classify/scoring.py calibrate` proposes new
values and writes nothing; they change only by a reviewed commit here
(skills/herdr-foreman/references/report-classifier.md, Changing the Bands).

Verdict gates (#646). The same store holds a second gate source: a report
whose owner-parsed `VERDICT:` line is `blocking` (`record_verdict`, called by
`assess-specialist` and `record-report`). A verdict gate is always `block`,
carries the dispatch its verdict was recorded against, and no classifier
fields. It never refuses acceptance (`require_clear` reads classifier gates
alone): it refuses a `release` dispatch on its task (`require_no_verdict_gate`,
called by `apply`). It clears only by an explicit `report-gate-clear` citing
the operator's resolved decision or a re-check: a delivered report from the
gated responsibility, dispatched after the gate, whose owner-parsed verdict at
its current bytes is `approved` and which carries no open classifier gate. A
judge ruling decides a disputed verdict; it never clears the gate itself.

Sidecar (`<state>.report-gates.json`, schema in state-schema.md, Report Gates):
  {"schema_version": 2, "state_path": "<canonical state>", "gates": [<gate>...]}
Schema 1 held classifier gates alone. `load` is the owner migration: it
upgrades a schema-1 document (every gate `source: classifier`, `dispatch:
null`) and rewrites it under the sidecar lock before returning it. A document at any other version is refused, never read as no
gates: for a gate store, "no usable prior state" would read as no gate
(rules/stateful-artifacts.md Migration Policy, the gate-store exception).
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

from . import attention, runnable, supervision
from .chronology import timestamp
from .errors import StateError, UsageError
from .report_contract import CONSULTATION_ROLES
from .state import load_state_checked, save_state, state_lock
from .tiers import canonical_role

SCHEMA_VERSION = 2
#: The one older sidecar schema `load` upgrades in memory.
LEGACY_SCHEMA_VERSION = 1
#: Where a gate came from: a classifier label, or an owner-parsed blocking VERDICT line.
SOURCES = ("classifier", "verdict")
VERDICT_REASON = "the report records `VERDICT: blocking`"

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
#: Who may clear a block, as `resolve` derives it from the owners' records.
CLEARERS = ("worker", "judge", "operator")
#: Who may clear a verdict gate: the gated responsibility's re-check, or the operator. Never the judge.
VERDICT_CLEARERS = ("worker", "operator")
#: The one gate level each resolution action resolves.
ACTION_LEVEL = {"clear": "block", "reread": "reread"}
#: Who clears by citing a delivered report rather than an operator decision.
REPORT_CLEARERS = ("worker", "judge")
#: Who performs a mandatory re-read: a worker in the gated report's role.
REREADERS = ("worker",)
CLEAR_COMMAND = "report-gate-clear --report <path> (--evidence <delivered worker or judge report> --reason <why it does not block> | --decision <resolved attention decision>)"
VERDICT_CLEAR_COMMAND = "report-gate-clear --report <blocking report> (--evidence <approved re-check report> --reason <what the re-check settled> | --decision <resolved attention decision>)"
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
    # The level comes from the recorded probabilities and the bands alone; the
    # label's composed verdict is recorded data, never an input here.
    if label.get("agent") != "jev":
        return none("only a Jev label carries probabilities; an LLM label never gates")
    if label.get("model") != JEV_MODEL:
        return none("label model {!r} is not the pinned {}; its probabilities are outside the bands".format(
            label.get("model"), JEV_MODEL))
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


#: Sidecar paths this process holds the lock on, so an owner call inside a
#: held transaction neither re-locks (the lock is not reentrant) nor skips a write.
_HELD = set()


@contextmanager
def _locked(path):
    """The sidecar lock, reentrant within this process."""
    target = str(storage_path(path))
    if target in _HELD:
        yield
        return
    with state_lock(storage_path(path)):
        _HELD.add(target)
        try:
            yield
        finally:
            _HELD.discard(target)


def load(path):
    """Read and validate. Missing is first use; malformed never reads as empty.

    A schema-1 document is the owner's to migrate: it is upgraded and
    rewritten under the sidecar lock before it is returned.
    """
    document, migrated = _read(path)
    if not migrated:
        return document
    with _locked(path):
        document, migrated = _read(path)
        if migrated:
            save_state(storage_path(path), document)
    return document


def _read(path):
    """The validated document, and whether it was upgraded from schema 1 in memory."""
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
        return empty, False
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
            or type(document["schema_version"]) is not int
            or document["schema_version"] not in (SCHEMA_VERSION, LEGACY_SCHEMA_VERSION)
            or document["state_path"] != str(canonical_state(path)) or not isinstance(document["gates"], list)):
        raise StateError("Report gates {} has an unsupported schema or state identity; preserve it and update "
                         "the owner, never replace it with an empty record.".format(target), {})
    migrated = document["schema_version"] == LEGACY_SCHEMA_VERSION
    if migrated:
        _migrate(document)
    for index, gate in enumerate(document["gates"]):
        problem = _gate_problem(gate)
        if problem:
            raise StateError("Report gates {} record {} is malformed ({}); preserve the file and restore or repair "
                             "that record; a malformed gate is never read as no gate.".format(target, index, problem),
                             {"record": index})
    return document, migrated


#: Fields every schema-2 gate carries.
COMMON_FIELDS = frozenset({"schema_version", "source", "report", "sha256", "level", "reason", "dispatch", "at",
                           "status", "resolution"})
#: What only a classifier gate carries.
CLASSIFIER_ONLY = frozenset({"probabilities", "model", "question", "bands"})
#: The exact field set of a gate, by source.
GATE_FIELDS = {"classifier": COMMON_FIELDS | CLASSIFIER_ONLY, "verdict": COMMON_FIELDS}
#: The exact field set of a schema-1 gate.
LEGACY_GATE_FIELDS = (COMMON_FIELDS | CLASSIFIER_ONLY) - {"source", "dispatch"}
RESOLUTION_FIELDS = frozenset({"schema_version", "at", "action", "by", "reason", "evidence"})


def _migrate(document):
    """Upgrade a schema-1 document in place: every gate a classifier gate with no dispatch.

    A gate that is not a schema-1 gate is left as found, for validation to refuse.
    """
    for gate in document["gates"]:
        if (isinstance(gate, dict) and set(gate) == LEGACY_GATE_FIELDS
                and type(gate["schema_version"]) is int and gate["schema_version"] == LEGACY_SCHEMA_VERSION):
            gate.update(schema_version=SCHEMA_VERSION, source="classifier", dispatch=None)
            resolution = gate["resolution"]
            if (isinstance(resolution, dict) and type(resolution.get("schema_version")) is int
                    and resolution["schema_version"] == LEGACY_SCHEMA_VERSION):
                resolution["schema_version"] = SCHEMA_VERSION
    document["schema_version"] = SCHEMA_VERSION
#: The status each resolution action leaves.
RESOLVED_STATUS = {"clear": "cleared", "reread": "reread"}


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _gate_problem(gate):
    """What is wrong with one saved gate record, or None."""
    if not isinstance(gate, dict) or gate.get("source") not in SOURCES:
        return "no known source ({})".format(", ".join(SOURCES))
    source = gate["source"]
    if set(gate) != GATE_FIELDS[source]:
        return "fields other than {}".format(", ".join(sorted(GATE_FIELDS[source])))
    if type(gate["schema_version"]) is not int or gate["schema_version"] != SCHEMA_VERSION:
        return "unsupported schema_version"
    if not _nonempty(gate["report"]) or not Path(gate["report"]).is_absolute():
        return "report is not an absolute path"
    # The stored path must be what `_report_key` would store: every symlink
    # resolved, so `open_gates` can match it.
    try:
        canonical = os.path.realpath(gate["report"])
    except (OSError, ValueError):
        return "report path cannot be resolved"
    if canonical != gate["report"]:
        return "report is not a canonical path"
    if not _sha(gate["sha256"]):
        return "sha256 is not a lowercase sha256"
    if gate["level"] not in LEVELS:
        return "unknown level"
    if not all(_nonempty(gate[key]) for key in ("reason", "at")):
        return "reason or at is empty"
    if source == "verdict":
        if gate["level"] != "block":
            return "a verdict gate is not a block"
        if not _nonempty(gate["dispatch"]):
            return "a verdict gate names no dispatch"
    else:
        problem = _classifier_problem(gate)
        if problem:
            return problem
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
    if ACTION_LEVEL.get(resolution["action"]) != gate["level"]:
        return "the resolution action does not resolve the gate's level"
    allowed = REREADERS if resolution["action"] != "clear" else VERDICT_CLEARERS if source == "verdict" else CLEARERS
    if resolution["by"] not in allowed:
        return "resolution names who may not resolve it"
    if not _nonempty(resolution["reason"]) or not _nonempty(resolution["at"]):
        return "resolution reason or at is empty"
    evidence = resolution["evidence"]
    if resolution["by"] == "operator":
        if not isinstance(evidence, dict) or set(evidence) != {"attention"} or not _nonempty(evidence["attention"]):
            return "an operator resolution cites no attention decision"
        return None
    if (not isinstance(evidence, dict) or set(evidence) != {"path", "sha256", "dispatch"}
            or not _nonempty(evidence["path"]) or not _sha(evidence["sha256"]) or not _nonempty(evidence["dispatch"])):
        return "a worker or judge resolution cites no delivered report and dispatch"
    return None


def _classifier_problem(gate):
    """What is wrong with a classifier gate's own fields, or None."""
    if gate["dispatch"] is not None:
        return "a classifier gate names a dispatch"
    if not all(_nonempty(gate[key]) for key in ("model", "bands")):
        return "model or bands is empty"
    if gate["question"] is not None and not isinstance(gate["question"], str):
        return "question is not a string"
    probabilities = gate["probabilities"]
    if (not isinstance(probabilities, dict) or set(probabilities) != set(GATE_QUESTIONS)
            or any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1
                   for p in probabilities.values())):
        return "probabilities do not cover the gate questions in [0, 1]"
    return None


@contextmanager
def holding(path):
    """Hold the sidecar lock across a gate check and the decision it guards.

    A caller checking `require_clear` and then committing an acceptance holds
    this for both, so no gate can be recorded in between. Writers take the
    same non-blocking lock and refuse while it is held.
    """
    with _locked(path):
        yield


def open_gates(path, report):
    key = _report_key(report)
    return [gate for gate in load(path)["gates"] if gate["report"] == key and gate["status"] == "open"]


def require_clear(path, report, accepting):
    """Refuse gating a report whose open gate forbids it.

    An open `reread` refuses any gating decision until the re-read is
    recorded; an open `block` refuses acceptance until a recorded clear.
    Verdict gates are not read here: a blocking report is still an accepted
    assignment, and its gate holds the release instead (`require_no_verdict_gate`).
    """
    for gate in open_gates(path, report):
        if gate["source"] != "classifier":
            continue
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
    with _locked(path):
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
                          if gate["source"] == "classifier"
                          and gate["report"] == key and gate["sha256"] == label["sha256"]
                          and gate["level"] == decision["level"] and gate["model"] == label["model"]
                          and gate["question"] == label.get("question") and gate["bands"] == BANDS_VERSION), None)
            if prior is not None:
                replayed.append(copy.deepcopy(prior))
                continue
            gate = {"schema_version": SCHEMA_VERSION, "source": "classifier", "dispatch": None,
                    "report": key, "sha256": label["sha256"],
                    "level": decision["level"], "reason": decision["reason"],
                    "probabilities": decision["probabilities"], "model": label["model"],
                    "question": label.get("question"), "bands": BANDS_VERSION, "at": at,
                    "status": "open", "resolution": None}
            document["gates"].append(gate)
            recorded.append(copy.deepcopy(gate))
        if recorded:
            save_state(storage_path(path), document)
    return {"schema_version": SCHEMA_VERSION, "recorded": recorded, "replayed": replayed, "no_gate": ungated}


def record_verdict(path, report, sha256, dispatch, at):
    """Record the verdict gate an owner-parsed `VERDICT: blocking` earns.

    `sha256` is the digest of the bytes the owner parsed and `dispatch` the
    dispatch that verdict was recorded against, both from the owner's own
    record. Idempotent per (source, report, sha256): a replay returns the
    existing gate whatever its status, and new bytes record a new gate.
    """
    at = _utc(at)
    key = _report_key(report)
    if not _sha(sha256) or not _nonempty(dispatch):
        _fail("A verdict gate needs the parsed report's sha256 and the dispatch its verdict was recorded against.")
    with _locked(path):
        document = load(path)
        prior = next((gate for gate in document["gates"]
                      if gate["source"] == "verdict" and gate["report"] == key and gate["sha256"] == sha256), None)
        if prior is not None:
            return {"schema_version": SCHEMA_VERSION, "recorded": [], "replayed": [copy.deepcopy(prior)]}
        gate = {"schema_version": SCHEMA_VERSION, "source": "verdict", "report": key, "sha256": sha256,
                "level": "block", "reason": VERDICT_REASON, "dispatch": dispatch, "at": at,
                "status": "open", "resolution": None}
        document["gates"].append(gate)
        save_state(storage_path(path), document)
    return {"schema_version": SCHEMA_VERSION, "recorded": [copy.deepcopy(gate)], "replayed": []}


def open_verdict_gates(path, task, dispatches):
    """The open verdict gates whose recorded dispatch belongs to `task`.

    A gate whose dispatch the ledger no longer holds is refused, never read as
    belonging to no task.
    """
    rows = {row["id"]: row for row in dispatches}
    found = []
    for gate in load(path)["gates"]:
        if gate["source"] != "verdict" or gate["status"] != "open":
            continue
        row = rows.get(gate["dispatch"])
        if row is None:
            raise StateError("Verdict gate on {} names dispatch {}, which the task ledger does not hold; restore that "
                             "ledger before releasing any task.".format(gate["report"], gate["dispatch"]),
                             {"gate": copy.deepcopy(gate)})
        if row.get("task") == task:
            found.append(copy.deepcopy(gate))
    return found


def require_no_verdict_gate(path, task, dispatches):
    """Refuse a release dispatch while the task carries an open verdict gate."""
    found = open_verdict_gates(path, task, dispatches)
    if found:
        raise UsageError("Task {} carries {} open verdict gate(s) on {}; a blocking VERDICT holds the release. Dispatch "
                         "a re-check to the same responsibility at the current tip, record its verdict, then clear each "
                         "gate with `{}`.".format(task, len(found), ", ".join(gate["report"] for gate in found),
                                                  runnable.command(VERDICT_CLEAR_COMMAND)),
                         {"gates": found})


def _ledger_state(state_path):
    """The task ledger's recovery store and assessments, read without migrating or writing it."""
    state, usable = load_state_checked(state_path, persist_migration=False)
    if not usable:
        raise StateError("State file {} is unusable, so no gate resolution can be bound to a delivered report; "
                         "restore it before resolving a gate.".format(state_path), {"path": str(state_path)})
    return state["recovery"], state["specialist_assessments"]


def _verdicts(store, assessments):
    """Every owner-parsed verdict, keyed to the report bytes it was parsed from.

    Sources, each bound to who delivered it: a report-sourced specialist
    assessment names the assessed `dispatch`; a `record-report` receipt names
    the reviewed developer dispatch's `task` and the `reviewer` agent.
    """
    found = []
    for row in assessments:
        if row.get("source") == "report" and row.get("verdict") is not None:
            found.append({"path": _report_key(row["report"]), "sha256": row["report_evidence"]["sha256"],
                          "verdict": row["verdict"], "dispatch": row.get("dispatch"), "task": None, "agent": None})
    for row in store["dispatches"]:
        receipt = row.get("report")
        if isinstance(receipt, dict) and isinstance(receipt.get("evidence"), dict):
            found.append({"path": _report_key(receipt["report"]), "sha256": receipt["evidence"]["sha256"],
                          "verdict": receipt["verdict"], "dispatch": None, "task": row.get("task"),
                          "agent": receipt.get("reviewer")})
    return found


def _approves(row, path, digest, dispatch):
    """Whether an owner-parsed verdict approves these report bytes as delivered by `dispatch`."""
    if row["path"] != path or row["sha256"] != digest or row["verdict"] != "approved":
        return False
    if row["dispatch"] is not None:
        return row["dispatch"] == dispatch["id"]
    return (row["task"] is not None and row["task"] == dispatch.get("task")
            and row["agent"] is not None and row["agent"] == dispatch.get("agent"))


def ledger_view(state_path, judge_agent):
    """What the owners already record about who delivered which report, and when.

    Deliveries are supervision's `report_observed` events and the recovery
    store's `delivery_recoveries`; roles and tasks are the recovery store's
    dispatches; operator decisions are the attention sidecar's entries;
    owner-parsed verdicts are the assessments and `record-report` receipts.
    `judge_agent` is the pinned judge from config.json, or None.
    """
    store, assessments = _ledger_state(state_path)
    data = supervision.load(state_path)
    _document, entries, _progress = attention.load(state_path)
    deliveries = []
    for event in data["events"]:
        detail = event.get("data")
        if (event.get("kind") == "report_observed" and isinstance(detail, dict) and detail.get("present") is True
                and _nonempty(detail.get("path")) and _sha(detail.get("sha256"))):
            deliveries.append({"dispatch": event["member"], "path": _report_key(detail["path"]),
                               "sha256": detail["sha256"], "at": event["at"]})
    for row in store["delivery_recoveries"]:
        saved = row["receipts"]["report"]
        deliveries.append({"dispatch": row["dispatch"], "path": _report_key(saved["path"]),
                           "sha256": saved["sha256"], "at": row["at"]})
    return {"dispatches": {row["id"]: row for row in store["dispatches"]},
            "enrolled": {row["id"]: _report_key(row["assignment"]["report"]) for row in data["members"]},
            "deliveries": deliveries, "decisions": entries, "judge": judge_agent,
            "verdicts": _verdicts(store, assessments)}


def _owner(view, key):
    """The applied dispatch whose supervision enrollment binds the gated report."""
    rows = [view["dispatches"][name] for name, report in sorted(view["enrolled"].items())
            if report == key and name in view["dispatches"] and view["dispatches"][name].get("status") == "applied"]
    if not rows:
        _fail("Report {} is bound to no applied dispatch's supervision enrollment, so no resolution can be tied to "
              "its task and role. Restore that dispatch's enrollment (`{}`), then retry.".format(
                  key, runnable.command("supervision-status")))
    if len({(row["task"], row["role"]) for row in rows}) > 1:
        _fail("Report {} is enrolled for more than one task or role; reconcile the enrollments before resolving "
              "its gate.".format(key))
    return rows[0]


def _resolver(view, owner, dispatch):
    """The resolving role a delivered report's dispatch holds, or None."""
    if dispatch.get("status") != "applied" or dispatch.get("task") != owner["task"]:
        return None
    if (view["judge"] is not None and dispatch.get("agent") == view["judge"] and dispatch.get("role") == "judge"
            and dispatch.get("judge_mode") == "adjudication"):
        return "judge"
    if dispatch.get("role") == owner["role"] and dispatch["id"] != owner["id"]:
        return "worker"
    return None


def _delivered(view, owner, key, since, evidence, allowed):
    """The resolving role and receipt for a cited report, from a delivery the owners recorded."""
    path = _report_key(evidence)
    if path == key:
        _fail("A resolution cites the resolving worker's own report, not the gated report itself.")
    digest = _digest(path)
    for row in view["deliveries"]:
        if row["path"] != path or row["sha256"] != digest or timestamp(row["at"], "Delivery") <= since:
            continue
        dispatch = view["dispatches"].get(row["dispatch"])
        if dispatch is None:
            continue
        role = _resolver(view, owner, dispatch)
        if role in allowed:
            return role, {"path": path, "sha256": digest, "dispatch": dispatch["id"]}
    _fail("Report {} carries no delivery receipt that resolves this gate: it must be the current bytes of a report "
          "supervision observed (or `{}` recovered) after the gate was recorded, for an applied dispatch on task {} "
          "held by {}. Dispatch that re-read or ruling and cite its delivered report.".format(
              path, runnable.command("recover-report"), owner["task"], " or ".join(
                  {"worker": "the {} role".format(owner["role"]),
                   "judge": "the pinned judge in adjudication mode"}[role] for role in allowed)))


def _decided(view, owner, since, name):
    """The operator's recorded answer on a resolved attention decision for the task."""
    entry = view["decisions"].get(name) if isinstance(name, str) else None
    resolution = entry.get("resolution") if entry is not None else None
    if (entry is None or entry["kind"] != "decision" or entry["status"] != "resolved" or entry["task"] != owner["task"]
            or not isinstance(resolution, dict) or resolution.get("kind") != "user_answer"
            or timestamp(entry["updated_at"], "Decision") <= since):
        _fail("An operator clear cites an attention decision on task {} resolved with the operator's answer after "
              "the gate was recorded. Record it with `{}`, resolve it with the user's answer, then pass "
              "--decision <its id>.".format(owner["task"], runnable.command("attention-record")))
    return resolution["summary"]


def _specialty(dispatch):
    requirement = dispatch.get("requirements")
    return requirement.get("specialty") if isinstance(requirement, dict) else None


def _responsibility(view, group):
    """The task, responsibility and specialty a group of verdict gates holds, from their recorded dispatches.

    A gate recorded by `record-report` names the reviewed developer dispatch,
    so its responsibility is the reviewer. Specialty binds consultations alone.
    """
    found = set()
    for gate in group:
        dispatch = view["dispatches"].get(gate["dispatch"])
        if dispatch is None:
            _fail("Verdict gate on {} names dispatch {}, which the task ledger does not hold; restore that ledger "
                  "before clearing it.".format(gate["report"], gate["dispatch"]))
        role = canonical_role(dispatch["role"])
        if role == "developer":
            found.add((dispatch["task"], "reviewer", None))
        else:
            found.add((dispatch["task"], role, _specialty(dispatch) if role in CONSULTATION_ROLES else None))
    if len(found) > 1:
        _fail("The open verdict gates on {} name more than one task or responsibility; reconcile their dispatches "
              "before clearing them.".format(group[0]["report"]))
    task, role, specialty = found.pop()
    return {"task": task, "role": role, "specialty": specialty}


def _rechecked(view, owner, key, since, evidence, document):
    """The receipt of a re-check that clears a verdict gate, or a refusal naming what it lacks.

    A re-check is a report delivered for an applied dispatch of the gated
    responsibility on the same task, reserved after the gate, whose
    owner-parsed verdict at its current bytes, recorded for that dispatch, is
    `approved`, and which carries no open classifier gate. A judge ruling never
    qualifies.
    """
    path = _report_key(evidence)
    if path == key:
        _fail("A re-check cites the re-checking worker's report, not the gated report itself.")
    digest = _digest(path)
    held = "the {} role{} on task {}".format(owner["role"], " ({})".format(owner["specialty"])
                                             if owner["specialty"] else "", owner["task"])
    candidates = []
    for row in view["deliveries"]:
        dispatch = view["dispatches"].get(row["dispatch"])
        if row["path"] != path or row["sha256"] != digest or dispatch is None:
            continue
        if canonical_role(dispatch.get("role")) == "judge":
            _fail("A judge ruling decides a disputed verdict but never clears its gate. Dispatch the {} re-check the "
                  "ruling directs, record its verdict, then cite that report.".format(owner["role"]))
        candidates.append(dispatch)
    matching = [dispatch for dispatch in candidates
                if dispatch.get("status") == "applied" and dispatch.get("task") == owner["task"]
                and canonical_role(dispatch.get("role")) == owner["role"]
                and (owner["role"] not in CONSULTATION_ROLES or _specialty(dispatch) == owner["specialty"])
                and _nonempty(dispatch.get("at")) and timestamp(dispatch["at"], "Dispatch") > since]
    if not matching:
        _fail("Report {} is no re-check of this verdict gate: cite the current bytes of a report supervision observed "
              "(or `{}` recovered) for an applied dispatch of {}, reserved after the gate was recorded.".format(
                  path, runnable.command("recover-report"), held))
    approved = [dispatch for dispatch in matching
                if any(_approves(row, path, digest, dispatch) for row in view["verdicts"])]
    if not approved:
        _fail("Re-check {} carries no owner-parsed `VERDICT: approved` at its current bytes recorded for the "
              "dispatch that delivered it. Record it with `{}` or `{}` first; a blocking, unrecorded or "
              "foreign-dispatch verdict clears nothing.".format(
                  path, runnable.command("assess-specialist"), runnable.command("record-report")))
    matching = approved
    if any(gate["report"] == path and gate["status"] == "open" and gate["source"] == "classifier"
           for gate in document["gates"]):
        _fail("Re-check {} carries an open classifier gate of its own; resolve it (`{}`) before it clears "
              "another report's gate.".format(path, runnable.command("report-gate-status")))
    return {"path": path, "sha256": digest, "dispatch": matching[0]["id"]}


def resolve(path, report, action, reason, at, view, evidence=None, decision=None):
    """Record a re-read or a clear against the report's open gates.

    Who resolves is read from the owners' records, never from the caller: a
    worker or judge resolution cites a report delivered for a dispatch on the
    gated report's task after the gate -- the gated report's own role (a
    worker), or the pinned judge in adjudication mode. An operator clear cites
    a resolved attention decision for that task, whose answer is the reason.
    A verdict gate's evidence must be a re-check (`_rechecked`); the judge
    never clears one.
    """
    at = _utc(at)
    key = _report_key(report)
    if action == "reread" and (evidence is None or decision is not None):
        _fail("A re-read cites the re-reading worker's delivered report; pass --evidence <report>.")
    if action == "clear" and (evidence is None) == (decision is None):
        _fail("A clear cites either the worker's or judge's delivered report (--evidence) or the operator's "
              "resolved decision (--decision), exactly one.")
    if evidence is not None:
        _text(reason, "The re-read note" if action == "reread" else "The clear reason")
    elif reason is not None:
        _fail("An operator clear quotes the decision's recorded answer; drop --reason.")
    with _locked(path):
        document = load(path)
        open_gates_here = [gate for gate in document["gates"] if gate["report"] == key and gate["status"] == "open"]
        # Each command resolves its own level only: a clear never discharges a
        # re-read, and a re-read never clears a block.
        pending = [gate for gate in open_gates_here if gate["level"] == ACTION_LEVEL[action]]
        if not pending:
            if open_gates_here:
                other = "reread" if action == "clear" else "block"
                _fail("Report {} has no open {} gate; its open {} gate is resolved only by `{}`.".format(
                    key, ACTION_LEVEL[action], other,
                    runnable.command(REREAD_COMMAND if other == "reread" else CLEAR_COMMAND)))
            _fail("Report {} has no open gate; `{}` lists what is open.".format(key, runnable.command("report-gate-status")))
        # Each source resolves by its own rule, and the command is atomic: every
        # pending gate on the report resolves, or none does.
        resolutions = []
        for source in SOURCES:
            group = [gate for gate in pending if gate["source"] == source]
            if not group:
                continue
            since = max(timestamp(gate["at"], "Gate") for gate in group)
            if source == "verdict":
                owner = _responsibility(view, group)
                if decision is not None:
                    by, said, cited = "operator", _decided(view, owner, since, decision), {"attention": decision}
                else:
                    by, said, cited = "worker", reason, _rechecked(view, owner, key, since, evidence, document)
            else:
                owner = _owner(view, key)
                if decision is not None:
                    by, said, cited = "operator", _decided(view, owner, since, decision), {"attention": decision}
                else:
                    by, cited = _delivered(view, owner, key, since, evidence,
                                           REREADERS if action == "reread" else REPORT_CLEARERS)
                    said = reason
            resolutions.append((group, by, said, cited))
        for group, by, said, cited in resolutions:
            for gate in group:
                gate["status"] = "cleared" if action == "clear" else "reread"
                gate["resolution"] = {"schema_version": SCHEMA_VERSION, "at": at, "action": action, "by": by,
                                      "reason": said, "evidence": cited}
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
    parser.add_argument("--reason", help="Why it does not block; required with --evidence.")
    parser.add_argument("--evidence", help="The owning worker's or adjudicating judge's delivered report; for a "
                                           "verdict gate, the same responsibility's approved re-check.")
    parser.add_argument("--decision", help="The operator's resolved attention decision on the task.")
    parser.add_argument("--now", metavar="ISO8601")
    parser = sub.add_parser("report-gate-status", parents=[common], help="List open and resolved report gates.")
    parser.add_argument("--report")


def run_command(args, state_path, now, judge_agent=None):
    if args.command == "report-gate-record":
        try:
            data = json.loads(Path(args.labels).expanduser().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise UsageError("Cannot read labels {}: {}. Save classify-reports.sh stdout to a file and pass it "
                             "unchanged.".format(args.labels, exc), {}) from None
        return record(state_path, data, args.now or now)
    if args.command == "report-gate-reread":
        return resolve(state_path, args.report, "reread", args.note, args.now or now,
                       ledger_view(state_path, judge_agent), evidence=args.evidence)
    if args.command == "report-gate-clear":
        return resolve(state_path, args.report, "clear", args.reason, args.now or now,
                       ledger_view(state_path, judge_agent), evidence=args.evidence, decision=args.decision)
    return status(state_path, args.report)
