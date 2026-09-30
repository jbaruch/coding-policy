"""Report-contract assessments of delivered worker reports, owned by foreman state.

Report delivery proves an artifact arrived. The assessment reads the report's
contract lines through `report_contract`, against the owner dispatch's role,
specialty and dispatched brief, and records what those lines say. It never
takes the foreman's reading of the report, and it never accepts the whole task.
Reading history validates stored relationships without reopening old sources.
Warm follow-up revalidates those sources and the retired fleet enrollment.

Record schema 2 (state-schema.md, Specialist Assessments). A schema-1 record
held the foreman's own `outcome`, `summary` and `contribution`; the owner
migration (`migrate_assessments`) moves that prose under `legacy` with
`source: foreman_assessment`. A legacy record keeps a `design` or
`implementation` contribution as an exclusion and satisfies nothing else.

A report refused for a contract gap records nothing, save one case: a
well-formed `design` or `implementation` CONTRIBUTION line in it is recorded as
a `source: contribution_only` entry, so the exclusion it adds is never lost.
That entry carries no line fields and satisfies nothing else (#625).
"""

import hashlib
import json
from pathlib import Path

from . import report_contract
from . import runnable
from .chronology import latest_assignment
from .errors import UsageError
from .recovery import receipt, text, validate_receipt
from .tiers import canonical_role
from . import supervision


ASSESSMENT_SCHEMA_VERSION = 2
#: The one older record schema the owner migration upgrades.
LEGACY_SCHEMA_VERSION = 1
CONSULTATION_ROLES = report_contract.CONSULTATION_ROLES
ASSESSABLE_ROLES = CONSULTATION_ROLES | report_contract.VERDICT_ROLES
CONTRIBUTIONS = report_contract.CONTRIBUTIONS
INPUT_FIELDS = frozenset({"id", "dispatch", "report", "delivery"})
#: Input fields schema 1 took from the foreman and schema 2 refuses by name.
RETIRED_INPUT_FIELDS = frozenset({"outcome", "contribution", "summary"})
#: `source` values: parsed from report lines, the declared contribution of a report
#: refused for a contract gap, or a migrated schema-1 foreman assessment.
SOURCES = frozenset({"report", "contribution_only", "foreman_assessment"})
#: Contributions that add an independence exclusion.
EXCLUDING_CONTRIBUTIONS = frozenset({"design", "implementation"})
_BOUND_FIELDS = frozenset({"schema_version", "at", "assignment_index", "task", "role", "agent",
                           "report_evidence", "delivery_evidence"})
#: Exactly the fields a schema-1 record carries.
LEGACY_FIELDS = INPUT_FIELDS | RETIRED_INPUT_FIELDS | _BOUND_FIELDS
#: Exactly the fields a schema-2 record carries.
RECORD_FIELDS = INPUT_FIELDS | _BOUND_FIELDS | frozenset(
    {"source", "brief_evidence", "criteria", "acceptance", "verdict", "contribution", "legacy", "gap"})
#: Line fields a migrated or contribution-only record holds as null.
_LINE_FIELDS = ("brief_evidence", "criteria", "acceptance", "verdict")


def _input(data):
    if isinstance(data, dict) and RETIRED_INPUT_FIELDS.intersection(data):
        raise UsageError("Assessment input no longer takes {}: the record is read from the report's contract "
                         "lines, never from the foreman's assessment. Pass only id, dispatch, report and "
                         "delivery.".format(", ".join(sorted(RETIRED_INPUT_FIELDS.intersection(data)))), {})
    if not isinstance(data, dict) or set(data) != INPUT_FIELDS:
        raise UsageError("Specialist assessment requires id, dispatch, report and delivery.", {})
    for key in INPUT_FIELDS:
        text(data[key], key)


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


def specialty(dispatch):
    """The dispatch's recorded specialty, or None: the owner record decides, never the caller."""
    requirement = dispatch.get("requirements")
    return requirement.get("specialty") if isinstance(requirement, dict) else None


def _corrupt():
    return UsageError("Unsupported or corrupt specialist assessment; preserve history and update the owner.", {})


class ContractGap(UsageError):
    """A contract gap whose report still declared an excluding contribution.

    `record` is the `contribution_only` entry already appended to the state;
    the caller saves the state before letting the refusal propagate.
    """

    def __init__(self, message, details, record):
        super().__init__(message, details)
        self.record = record


def _text_in(value, allowed):
    """Whether `value` is a string in `allowed`; an unhashable value is simply not one."""
    return isinstance(value, str) and value in allowed


def _frozen_brief_receipt(evidence, dispatch):
    """Whether a brief receipt names its dispatch's own frozen brief and that copy's content identity.

    Read without reopening the file: the frozen name carries its content
    digest (`assign.freeze_paths`), so the receipt's digest must match it.
    """
    brief = dispatch.get("brief")
    if not isinstance(brief, str) or evidence["path"] != brief:
        return False
    name = Path(brief)
    return name.parent.name == ".dispatched" and evidence["sha256"][:16] in name.name.split(".")


def migrate_assessments(payload):
    """Upgrade schema-1 records in place; True when any record changed. Idempotent.

    A schema-1 record carrying any schema-2 field is refused as corrupt rather
    than read with a meaning it was written without. A record at any other
    version is left for `validate_assessments` to judge.
    """
    records = payload.get("specialist_assessments")
    if not isinstance(records, list):
        return False
    changed = False
    for index, record in enumerate(records):
        if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                or record["schema_version"] != LEGACY_SCHEMA_VERSION):
            continue
        if set(record) != LEGACY_FIELDS:
            raise _corrupt()
        upgraded = {key: record[key] for key in INPUT_FIELDS | _BOUND_FIELDS}
        upgraded.update(schema_version=ASSESSMENT_SCHEMA_VERSION, source="foreman_assessment",
                        brief_evidence=None, criteria=None, acceptance=None, verdict=None,
                        contribution=record["contribution"], gap=None,
                        legacy={"outcome": record["outcome"], "summary": record["summary"]})
        records[index] = upgraded
        changed = True
    return changed


def _validate_lines(record, dispatch):
    if record["source"] == "foreman_assessment":
        legacy = record["legacy"]
        if (not isinstance(legacy, dict) or set(legacy) != {"outcome", "summary"}
                or not _text_in(record["contribution"], CONTRIBUTIONS) or record["gap"] is not None
                or any(record[key] is not None for key in _LINE_FIELDS)):
            raise _corrupt()
        text(legacy["outcome"], "legacy outcome")
        text(legacy["summary"], "legacy summary")
        return
    if record["source"] == "contribution_only":
        gap = record["gap"]
        if (record["legacy"] is not None or not _text_in(record["contribution"], EXCLUDING_CONTRIBUTIONS)
                or any(record[key] is not None for key in _LINE_FIELDS)
                or not isinstance(gap, dict) or set(gap) != {"message", "gaps"} or not isinstance(gap["gaps"], list)):
            raise _corrupt()
        text(gap["message"], "gap message")
        for item in gap["gaps"]:
            text(item, "gap")
        return
    if record["gap"] is not None:
        raise _corrupt()
    role = record["role"]
    if record["legacy"] is not None or record["contribution"] is not None and not _text_in(record["contribution"], CONTRIBUTIONS):
        raise _corrupt()
    if report_contract.verdict_required(role, specialty(dispatch)):
        if not _text_in(record["verdict"], report_contract.VERDICTS):
            raise _corrupt()
    elif record["verdict"] is not None:
        raise _corrupt()
    if role not in CONSULTATION_ROLES:
        if any(record[key] is not None for key in ("brief_evidence", "criteria", "acceptance")):
            raise _corrupt()
        return
    validate_receipt(record["brief_evidence"])
    if not _frozen_brief_receipt(record["brief_evidence"], dispatch):
        raise _corrupt()
    criteria, acceptance = record["criteria"], record["acceptance"]
    # `type(...) is int`, never isinstance: a JSON `true` is a Python bool,
    # which is an int and equals 1.
    if (type(criteria) is not int or criteria < 1 or not isinstance(acceptance, list)
            or any(not isinstance(row, dict) or type(row.get("k")) is not int for row in acceptance)
            or [row["k"] for row in acceptance] != list(range(1, criteria + 1))):
        raise _corrupt()
    for row in acceptance:
        if set(row) != {"k", "state", "evidence"} or not _text_in(row["state"], report_contract.ACCEPTANCE_STATES):
            raise _corrupt()
        text(row["evidence"], "acceptance evidence")


def validate_assessments(state):
    """Validate immutable receipts and their original assignment relationships."""
    records = state.get("specialist_assessments")
    if not isinstance(records, list):
        raise UsageError("State requires a specialist_assessments array; restore the owner-written history.", {})
    ids = set()
    for record in records:
        if (not isinstance(record, dict) or set(record) != RECORD_FIELDS or type(record.get("schema_version")) is not int
                or record["schema_version"] != ASSESSMENT_SCHEMA_VERSION or not _text_in(record.get("source"), SOURCES)):
            raise _corrupt()
        _input({key: record[key] for key in INPUT_FIELDS})
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
        _validate_lines(record, dispatch)


def _gap_refusal(record):
    """The refusal a contribution-only record stands for, rebuilt from what it saved."""
    return ContractGap("{} Its declared `{}` contribution is recorded as an independence exclusion.".format(
        record["gap"]["message"], record["contribution"]),
        {"gaps": list(record["gap"]["gaps"]), "contribution_record": record["id"]}, record)


def _brief_criteria(dispatch):
    """The dispatched brief's receipt and criteria count, read from its frozen bytes."""
    # Deferred: `assign` imports the dispatch stack, and this module loads
    # inside state validation.
    from .assign import read_frozen

    path = dispatch.get("brief")
    if not isinstance(path, str) or not path:
        raise UsageError("Consultation dispatch {} recorded no frozen brief, so its criteria count cannot be read; "
                         "dispatch the consultation again with this build.".format(dispatch["id"]), {})
    data = read_frozen(path)
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError("Dispatched brief {} is not UTF-8; dispatch the consultation again from a composed "
                         "brief.".format(path), {}) from None
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}, report_contract.brief_criteria(body)


def record_assessment(state, state_path, data, at):
    """Append the contract lines of a delivered enrollment's report, bound to its bytes.

    A report missing a required line, or carrying an extra, duplicate or
    malformed one, records nothing.
    """
    _input(data)
    supervision.timestamp(at)
    prior = next((row for row in state["specialist_assessments"] if row["id"] == data["id"]), None)
    if prior is not None:
        if prior["source"] == "contribution_only":
            # An identical retry replays the same refusal; nothing new is
            # recorded. A cleaned-up report replays unread; a present one must
            # still hold the refused bytes.
            if all(prior[key] == data[key] for key in INPUT_FIELDS) and (
                    not Path(data["report"]).exists() or receipt(data["report"])[0] == prior["report_evidence"]):
                raise _gap_refusal(prior)
            raise UsageError("Assessment identity {} holds only the declared contribution of a report refused for a "
                             "contract gap; re-dispatch and assess the new report under a new id.".format(data["id"]), {})
        if prior["source"] != "report":
            raise UsageError("Assessment identity {} names a migrated foreman assessment; record the report under a "
                             "new id.".format(data["id"]), {})
        if any(prior[key] != data[key] for key in INPUT_FIELDS):
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
    role = canonical_role(dispatch["role"])
    bound = {"schema_version": ASSESSMENT_SCHEMA_VERSION, "at": at, **data,
             "assignment_index": index, "task": dispatch["task"],
             "role": role, "agent": dispatch["agent"],
             "report_evidence": report_evidence, "delivery_evidence": delivery_evidence}
    try:
        brief_evidence, criteria = _brief_criteria(dispatch) if role in CONSULTATION_ROLES else (None, None)
        lines = report_contract.report_lines(body, role, specialty(dispatch), criteria)
    except UsageError as exc:
        declared = report_contract.declared_contributions(body) & EXCLUDING_CONTRIBUTIONS
        if not declared:
            raise
        # Add-only: the refusal never discards an exclusion the report declared.
        gaps = exc.details.get("gaps") if isinstance(exc.details, dict) else None
        record = {**bound, "source": "contribution_only", "brief_evidence": None, "criteria": None,
                  "acceptance": None, "verdict": None, "legacy": None,
                  "contribution": "implementation" if "implementation" in declared else "design",
                  "gap": {"message": exc.message, "gaps": [str(item) for item in gaps or ()]}}
        state["specialist_assessments"].append(record)
        raise _gap_refusal(record) from None
    result = {**bound, "source": "report", "brief_evidence": brief_evidence, "criteria": criteria,
              "acceptance": lines["acceptance"], "verdict": lines["verdict"],
              "contribution": lines["contribution"], "legacy": None, "gap": None}
    state["specialist_assessments"].append(result)
    return result


def all_met(record):
    """Whether a report-sourced record states no criterion `unmet`; a reviewer or tester record has none.

    Acceptance is contract completeness, whatever the `verdict`: a blocking
    verdict gates the round at SKILL.md Step 12 and the round-flow Release Gate.
    """
    return record["source"] == "report" and all(row["state"] == "met" for row in record["acceptance"] or ())


def accepted_investigations(state):
    """Investigator records the diagnose and judge gates count: every criterion met and no blocking verdict.

    These gates rely on the investigation's outcome, not only its acceptance:
    an investigator seated with a verdict specialty can record `blocking`.
    """
    return [row for row in state["specialist_assessments"] if row["role"] == "investigator" and investigated(row)]


def investigated(record):
    """An accepted record whose outcome a diagnosis can rule on: all criteria met, verdict not `blocking`."""
    return all_met(record) and record.get("verdict") != "blocking"


def require_accepted(state, dispatch_id, report):
    """Refuse an `accepted` closure the report's recorded contract lines do not support.

    A reviewer or tester is accepted with any valid recorded `verdict`: it
    needs a report-sourced record of this dispatch and report whose receipt
    matches the current bytes. A consultation's record must also state every
    `ACCEPTANCE` line `met`. Any other responsibility passes through. A
    blocking verdict gates the round at SKILL.md Step 12, never acceptance.
    """
    dispatch = next((row for row in state["recovery"]["dispatches"] if row["id"] == dispatch_id), None)
    if dispatch is None:
        raise UsageError("Enrollment {} has no owner dispatch, so its responsibility and report contract cannot be "
                         "read; reconcile the dispatch through references/dispatch-recovery.md before accepting.".format(
                             dispatch_id), {"dispatch": dispatch_id})
    if canonical_role(dispatch["role"]) not in ASSESSABLE_ROLES:
        return None
    records = [row for row in state["specialist_assessments"]
               if row["dispatch"] == dispatch_id and row["report"] == report and row["source"] == "report"]
    if not records:
        raise UsageError("Dispatch {} has no report-contract assessment of {}; run `{}` on the delivered report, or "
                         "record `needs_work` naming its gap.".format(dispatch_id, report, runnable.command("assess-specialist")),
                         {"dispatch": dispatch_id})
    record = records[-1]
    current, _body = receipt(report)
    if current != record["report_evidence"]:
        raise UsageError("Report {} changed since its assessment; assess the current bytes before accepting.".format(report),
                         {"dispatch": dispatch_id})
    if not all_met(record):
        unmet = [row["k"] for row in record["acceptance"] if row["state"] != "met"]
        raise UsageError("Report {} states criteria {} unmet; record `needs_work` and re-dispatch the consultation "
                         "with those criteria named.".format(report, ", ".join(str(k) for k in unmet)),
                         {"dispatch": dispatch_id, "unmet": unmet})
    return record


def require_followup(state, state_path, assignments):
    """Require report-assessed prior work and retired observation ownership, not idle."""
    for role, name in assignments.items():
        prior = latest_assignment(state["assignments"], agent=name)
        if prior is None:
            raise UsageError("No previous specialist assignment exists; dispatch a fresh consultation first.", {})
        index, row = prior
        assessment = next((item for item in reversed(state["specialist_assessments"])
                           if item["assignment_index"] == index and item["source"] == "report"), None)
        if assessment is None or row["role"] != canonical_role(role):
            raise UsageError("Retained specialist needs the preceding assignment's report-contract assessment; a migrated foreman assessment never qualifies. Run `{}` before following up.".format(
                runnable.command("assess-specialist")), {})
        for key in ("report_evidence", "delivery_evidence"):
            current, _body = receipt(assessment[key]["path"])
            if current != assessment[key]:
                raise UsageError("Specialist follow-up evidence changed; restore the original report and delivery receipt or start a fresh assessed handoff.", {})
        fleet = supervision.load(state_path)
        member = next((item for item in fleet["members"] if item["id"] == assessment["dispatch"]), None)
        if member is None or member["active"] or not member.get("resolution") or any(item["member"] == member["id"] for item in supervision.pending(fleet)):
            raise UsageError("Handle the preceding specialist's observations and resolve its enrollment before following up; a report assessment does not retire supervision.", {})
