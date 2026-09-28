"""Assessments of delivered specialist work, owned by foreman state.

Report delivery proves an artifact arrived. The contribution class is the one
the bound report declares on its `CONTRIBUTION:` line, derived here from the
report bytes; a report declaring none records `design` (#601). A
consultation's report carries one `ACCEPTANCE <k>/<N>: met|unmet` line per
criterion, and a reviewer's or tester's one `VERDICT:` line; the owner parses
them and refuses a report missing any, or with a criterion `unmet` or
unresolved, so it returns to its responsibility (`require_report_result`). The
foreman quotes the report as outcome and summary, and the owner refuses either
one the bound report does not contain. These receipts never accept the whole
task.

Schema 2 adds `contribution_source`. A schema-1 record carried a foreman's own
classification; the owner migration keeps its value and marks it
`foreman_assessment`, so readers treat both versions alike.
Reading history validates stored relationships without reopening old sources.
Warm follow-up revalidates those sources and the retired fleet enrollment.
"""

import json
import re

from . import runnable
from .chronology import latest_assignment
from .errors import UsageError
from .recovery import receipt, report_verdicts, text, validate_receipt
from .tiers import canonical_role
from . import supervision


ASSESSMENT_SCHEMA_VERSION = 2
CONSULTATION_ROLES = frozenset({"advisor", "investigator", "architect"})
ASSESSABLE_ROLES = CONSULTATION_ROLES | {"reviewer", "tester"}
CONTRIBUTIONS = frozenset({"none", "design", "implementation"})
INPUT_FIELDS = frozenset({"id", "dispatch", "report", "delivery", "outcome", "contribution", "summary"})
#: `contribution` is optional input: the owner derives it from the report, and
#: a supplied value must agree with the derivation.
REQUIRED_INPUT = INPUT_FIELDS - {"contribution"}
#: Where a record's contribution came from.
CONTRIBUTION_SOURCES = frozenset({"report_declared", "report_undeclared", "foreman_assessment"})
#: A whole line `CONTRIBUTION: <class>`, the shape every report template asks for.
CONTRIBUTION_LINE = re.compile(r"^CONTRIBUTION:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
#: The class an undeclared contribution records: unresolved history is never `none`.
UNDECLARED_CONTRIBUTION = "design"
#: A consultation report's per-criterion line: `ACCEPTANCE <k>/<N>: <status>`,
#: optionally bulleted or backquoted, evidence after the status. Criteria are
#: numbered 1..N in the order the brief states them.
ACCEPTANCE_LINE = re.compile(
    r"^[ \t>*-]*`?ACCEPTANCE[ \t]+(\d+)/(\d+):[ \t]*([A-Za-z_-]*)`?(.*)$", re.MULTILINE)
#: The evidence that follows a status: a dash, then at least one non-space character.
EVIDENCE = re.compile(r"^[ \t]*(?:—|–|-)[ \t]*\S")
#: The resolved statuses an acceptance line may carry.
ACCEPTANCE_STATUSES = frozenset({"met", "unmet"})
#: The fields the foreman quotes from the bound report rather than writes.
QUOTED_FIELDS = ("outcome", "summary")
RECORD_FIELDS = INPUT_FIELDS | {"schema_version", "at", "assignment_index", "task", "role", "agent",
                                "report_evidence", "delivery_evidence", "contribution_source"}


def declared_contribution(body):
    """`(class, source)` from a report's `CONTRIBUTION:` lines.

    None declared reads as `UNDECLARED_CONTRIBUTION`. Conflicting or unknown
    declarations are refused: the report goes back to its responsibility.
    """
    values = CONTRIBUTION_LINE.findall(body)
    if not values:
        return UNDECLARED_CONTRIBUTION, "report_undeclared"
    if len(set(values)) != 1 or values[0] not in CONTRIBUTIONS:
        raise UsageError(
            "The report's CONTRIBUTION lines read {}; a report declares exactly one of none, design or "
            "implementation. Return it to its responsibility with the gap named.".format(", ".join(values)), {})
    return values[0], "report_declared"


def _collapse(value):
    return " ".join(value.split())


def require_quoted(body, data):
    """Refuse an outcome or summary the bound report does not contain.

    A quote matches the report with runs of whitespace collapsed, so a line
    wrapped differently in the input still reads as the same words.
    """
    report = _collapse(body)
    missing = [key for key in QUOTED_FIELDS if _collapse(data[key]) not in report]
    if missing:
        raise UsageError(
            "The bound report does not contain the supplied {}; quote its acceptance lines verbatim, or return the "
            "report to its responsibility with the gap named.".format(" or ".join(missing)), {"missing": missing})


def acceptance_results(body):
    """`{k: status}` from a report's acceptance lines, refusing an incomplete set.

    Every criterion 1..N carries exactly one line, every line declares the
    same N, and every status is `met` or `unmet`. Anything else is the gap the
    report returns to its responsibility with.
    """
    lines = ACCEPTANCE_LINE.findall(body)
    if not lines:
        raise UsageError(
            "The report carries no `ACCEPTANCE <k>/<N>: met|unmet` lines; return it to its responsibility for one "
            "line per acceptance criterion.", {"missing": "acceptance"})
    totals = {int(total) for _index, total, _status, _rest in lines}
    indices = [int(index) for index, _total, _status, _rest in lines]
    total = next(iter(totals))
    if len(totals) != 1 or total < 1 or sorted(indices) != list(range(1, total + 1)):
        raise UsageError(
            "The report's acceptance lines are incomplete or inconsistent (criteria {} of totals {}); return it to "
            "its responsibility for exactly one line per criterion 1..N.".format(
                sorted(indices), sorted(totals)), {"indices": sorted(indices), "totals": sorted(totals)})
    results = {int(index): status for index, _total, status, _rest in lines}
    unresolved = sorted(index for index, status in results.items() if status not in ACCEPTANCE_STATUSES)
    if unresolved:
        raise UsageError(
            "Acceptance criteria {} carry no met/unmet status; return the report to its responsibility with the gap "
            "named.".format(unresolved), {"unresolved": unresolved})
    unevidenced = sorted(int(index) for index, _total, _status, rest in lines if not EVIDENCE.match(rest))
    if unevidenced:
        raise UsageError(
            "Acceptance criteria {} carry no evidence after the status; return the report to its responsibility for "
            "`ACCEPTANCE <k>/<N>: met|unmet — <evidence>`.".format(unevidenced), {"unevidenced": unevidenced})
    return results


def require_report_result(body, role):
    """Refuse a report whose own result lines are missing, unresolved or unmet.

    A consultation needs every acceptance criterion `met`; a reviewer or
    tester needs exactly one `VERDICT:` line.
    """
    if role in CONSULTATION_ROLES:
        unmet = sorted(index for index, status in acceptance_results(body).items() if status != "met")
        if unmet:
            raise UsageError(
                "Acceptance criteria {} are reported unmet; return the consultation to its responsibility with them "
                "named.".format(unmet), {"unmet": unmet})
        return
    if len(report_verdicts(body)) != 1:  # one line, not one distinct value
        raise UsageError(
            "The {} report states no single `VERDICT: blocking | approved` line; return it to its responsibility "
            "with the gap named.".format(role), {"missing": "verdict"})


def migrate_assessments(payload):
    """Carry schema-1 records to schema 2 in place; True when any changed.

    Only the owner calls this, on load. A schema-1 contribution was the
    foreman's own classification and keeps that meaning explicitly.
    """
    records = payload.get("specialist_assessments")
    if not isinstance(records, list):
        return False
    changed = False
    for record in records:
        if (isinstance(record, dict) and type(record.get("schema_version")) is int
                and record["schema_version"] == 1 and "contribution_source" not in record):
            record.update(schema_version=ASSESSMENT_SCHEMA_VERSION, contribution_source="foreman_assessment")
            changed = True
    return changed


def _input(data, required=REQUIRED_INPUT):
    if not isinstance(data, dict) or not required <= set(data) <= INPUT_FIELDS:
        raise UsageError("Specialist assessment requires id, dispatch, report, delivery, outcome and summary, and "
                         "accepts contribution; quote the delivered report's acceptance lines.", {})
    for key in data:
        text(data[key], key)
    if "contribution" in data and data["contribution"] not in CONTRIBUTIONS:
        raise UsageError("Contribution must be none, design or implementation, as the report's CONTRIBUTION line "
                         "declares it.", {})


def _dispatch(state, identifier):
    found = next((row for row in state["recovery"]["dispatches"] if row["id"] == identifier), None)
    # The seat stays on the dispatch, so its RESPONSIBILITY decides whether the
    # delivered report is assessable: a `reviewer#api` slice verdict is a
    # reviewer's (#434).
    if found is None or found["status"] != "applied" or canonical_role(found["role"]) not in ASSESSABLE_ROLES:
        raise UsageError("Assess a confirmed consultation, reviewer or tester dispatch; reconcile an unknown send before recording its outcome.", {})
    index = found.get("assignment_index")
    if type(index) is not int or not 0 <= index < len(state["assignments"]):
        raise UsageError("Specialist dispatch has no owned assignment; restore its original ledger before assessing it.", {})
    return found, index


def validate_assessments(state):
    """Validate immutable receipts and their original assignment relationships."""
    records = state.get("specialist_assessments")
    if not isinstance(records, list):
        raise UsageError("State requires a specialist_assessments array; restore the owner-written history.", {})
    ids = set()
    for record in records:
        if (not isinstance(record, dict) or set(record) != RECORD_FIELDS
                or type(record.get("schema_version")) is not int or record["schema_version"] != ASSESSMENT_SCHEMA_VERSION
                or record.get("contribution_source") not in CONTRIBUTION_SOURCES
                or (record["contribution_source"] == "report_undeclared"
                    and record.get("contribution") != UNDECLARED_CONTRIBUTION)):
            raise UsageError("Unsupported or corrupt specialist assessment; preserve history and update the owner.", {})
        _input({key: record[key] for key in INPUT_FIELDS}, required=INPUT_FIELDS)
        text(record["at"], "assessment time")
        supervision.timestamp(record["at"])
        if record["id"] in ids:
            raise UsageError("Duplicate specialist assessment identity; restore the append-only owner history.", {})
        ids.add(record["id"])
        dispatch, index = _dispatch(state, record["dispatch"])
        if supervision.timestamp(record["at"]) < supervision.timestamp(state["assignments"][index]["at"]):
            raise UsageError("Specialist assessment predates its assignment; restore the original dated evidence.", {})
        if (type(record["assignment_index"]) is not int or record["assignment_index"] != index
                or record["task"] != dispatch["task"] or record["agent"] != dispatch["agent"]
                or record["role"] != canonical_role(dispatch["role"])):
            raise UsageError("Specialist assessment no longer matches its original dispatch; restore the owned assignment relationship.", {})
        for key, source in (("report_evidence", "report"), ("delivery_evidence", "delivery")):
            validate_receipt(record[key])
            if record[key]["path"] != record[source]:
                raise UsageError("Specialist assessment receipt names a different artifact; preserve its original evidence.", {})


def record_assessment(state, state_path, data, at):
    """Append an assessment bound to a delivered enrollment's report bytes.

    The contribution class is derived from those bytes; a supplied one that
    disagrees is refused.
    """
    _input(data)
    supervision.timestamp(at)
    prior = next((row for row in state["specialist_assessments"] if row["id"] == data["id"]), None)
    if prior is not None:
        if any(prior[key] != data[key] for key in data):
            raise UsageError("Assessment identity already names different input; record a new assessment without rewriting prior evidence.", {})
        return prior
    dispatch, index = _dispatch(state, data["dispatch"])
    if supervision.timestamp(at) < supervision.timestamp(state["assignments"][index]["at"]):
        raise UsageError("Assessment cannot predate dispatch; use the actual assessment time.", {})
    fleet = supervision.load(state_path)
    member = next((row for row in fleet["members"] if row["id"] == data["dispatch"]), None)
    if member is None or member["assignment"]["report"] != data["report"] or member["assignment"]["agent"] != dispatch["agent"] or member["assignment"]["task"] != dispatch["task"]:
        raise UsageError("Assessment must name the report enrolled before this dispatch; inspect `{}` and preserve that report path.".format(
            runnable.command("supervision-status")), {})
    report_evidence, body = receipt(data["report"])
    require_report_result(body, canonical_role(dispatch["role"]))
    contribution, source = declared_contribution(body)
    require_quoted(body, data)
    if "contribution" in data and data["contribution"] != contribution:
        raise UsageError(
            "The bound report records contribution {!r} ({}); the supplied {!r} disagrees. Omit it, or return the "
            "report to its responsibility.".format(contribution, source.replace("_", " "), data["contribution"]),
            {"derived": contribution, "supplied": data["contribution"]})
    delivery_evidence, delivered = receipt(data["delivery"])
    try:
        proof = json.loads(delivered)
    except json.JSONDecodeError as exc:
        raise UsageError("Delivery receipt is invalid JSON ({}); save the successful wait-report output before assessing the specialist.".format(exc.msg), {}) from None
    recovered = next((row for row in state["recovery"]["delivery_recoveries"]
                      if row["dispatch"] == dispatch["id"] and row["input"]["report"] == data["report"]
                      and row.get("found") is True and row["receipts"]["report"] == report_evidence
                      and row == proof), None)
    waited = (isinstance(proof, dict) and proof.get("found") is True
              and proof.get("agent") == dispatch["agent"] and proof.get("report_path") == data["report"])
    if not waited and recovered is None:
        raise UsageError("Delivery receipt must prove this worker's exact report arrived; a lifecycle status or pending checkpoint is insufficient.", {})
    # The RESPONSIBILITY, never the seat: this record is independently
    # versioned, and widening its `role` to hold `reviewer#api` would repurpose
    # the field without versioning it (rules/stateful-artifacts.md Migration
    # Policy). The seat stays on the dispatch this record cites (#434).
    result = {"schema_version": ASSESSMENT_SCHEMA_VERSION, "at": at, **data,
              "contribution": contribution, "contribution_source": source,
              "assignment_index": index, "task": dispatch["task"],
              "role": canonical_role(dispatch["role"]), "agent": dispatch["agent"],
              "report_evidence": report_evidence, "delivery_evidence": delivery_evidence}
    state["specialist_assessments"].append(result)
    return result


def require_followup(state, state_path, assignments):
    """Require assessed prior work and retired observation ownership, not idle."""
    for role, name in assignments.items():
        prior = latest_assignment(state["assignments"], agent=name)
        if prior is None:
            raise UsageError("No previous specialist assignment exists; dispatch a fresh consultation first.", {})
        index, row = prior
        assessment = next((item for item in reversed(state["specialist_assessments"]) if item["assignment_index"] == index), None)
        if assessment is None or row["role"] != canonical_role(role):
            raise UsageError("Retained specialist needs the preceding assignment's saved foreman assessment; run `{}` before following up.".format(
                runnable.command("assess-specialist")), {})
        for key in ("report_evidence", "delivery_evidence"):
            current, body = receipt(assessment[key]["path"])
            if current != assessment[key]:
                raise UsageError("Specialist follow-up evidence changed; restore the original report and delivery receipt or start a fresh assessed handoff.", {})
            if key == "report_evidence":
                require_report_result(body, assessment["role"])
        fleet = supervision.load(state_path)
        member = next((item for item in fleet["members"] if item["id"] == assessment["dispatch"]), None)
        if member is None or member["active"] or not member.get("resolution") or any(item["member"] == member["id"] for item in supervision.pending(fleet)):
            raise UsageError("Handle the preceding specialist's observations and resolve its enrollment before following up; a report assessment does not retire supervision.", {})
