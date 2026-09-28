"""Close and check one supervised worker assignment in a single owner call (#508).

The foreman repeated two command chains for every report. `close-member`
composes the chain that ends an assignment's observation, and `check-member`
the chain that checks whether its report landed. Each composes the existing
owner functions; neither reimplements them.

`close-member` enforces the order the chain requires: the foreman's assessed
outcome must already be in the task ledger before any event is acknowledged
or the enrollment resolved (rules/agent-team-operation.md Fleet Supervision:
acknowledging an observation never accepts the assignment). The ledger stays
the foreman's; this reads it and writes nothing to it. A repeated call replays
the identical acknowledgement and resolution.

`check-member` derives the inputs `wait-report.sh --once` needs from the owner
records: the enrollment's agent and report, the task's registered base, and
the matching dispatch's send time. Only the worker's checkout is supplied by
the caller, since no owner record keeps it.
"""

import os
import re
import subprocess
from pathlib import Path

from . import report_gates
from . import runnable
from . import supervision
from .chronology import timestamp
from .errors import StateError, UsageError
from .state import load_state_checked

# Task-ledger schema 1 (#589). These constants ARE the ledger's field formats:
# state-schema.md (Task Ledger) and references/task-ledger.md name them and
# restate none of them, so one edit here changes the contract and the validator.
#: The schema version the frontmatter and every event carry.
LEDGER_SCHEMA_VERSION = "1"
#: Frontmatter fields, each required exactly once.
FRONT_FIELDS = ("schema_version", "task", "base_revision", "dispatch_state")
#: Event fields, each required exactly once per event; `id` repeats the event's section heading
#: and names no other event.
EVENT_FIELDS = ("schema_version", "id", "at", "subject", "dispatch_id", "worker", "role", "report", "observed",
                "decision", "head_revision", "evidence", "assessment")
#: Event fields held as free text: the schema promises no format for them beyond presence.
FREE_TEXT_FIELDS = ("observed", "evidence", "assessment")
#: The literal an unavailable value is recorded as, never a guess.
UNKNOWN = "unknown"
#: The literal a field that does not apply to its event's subject is recorded as.
NOT_APPLICABLE = "not_applicable"
#: Each subject's decisions and what each one means. A subject is one of these keys.
DECISION_MEANINGS = {
    "task": {
        "in_progress": "Required task work remains",
        "awaiting_diagnosis": "An exhausted allowance awaits the judge's remedy",
        "diagnosed_stop": "A `stop` remedy ships what is clean and tracks the remainder",
        "waiting_for_operator": "A named required operator decision remains outstanding",
        "ready_for_release": "Step 12's current-tip verification gate holds; release remains outstanding",
        "completed": "All task acceptance criteria and required release/cleanup obligations are verified",
    },
    "assignment": {
        "pending": "Dispatch is planned or confirmed; no report has been assessed",
        "reported": "Delivery was confirmed; the foreman has not yet accepted the work",
        "accepted": "The foreman read the report and verified that the assignment's acceptance criteria hold",
        "needs_work": "Evidence shows unmet criteria or invalidates a prior acceptance",
        "blocked": "A specific unresolved dependency or decision prevents the assignment from proceeding",
        "unavailable": "A report is missing or unavailable under the wait/recovery contract",
        UNKNOWN: "Dispatch or outcome evidence is insufficient; reconcile before retrying",
    },
}
#: The subjects, in the order refusals name them.
SUBJECTS = tuple(DECISION_MEANINGS)
#: Each subject's decision vocabulary.
DECISIONS = {subject: frozenset(meanings) for subject, meanings in DECISION_MEANINGS.items()}
#: Assignment decisions that record an assessment; `pending`, `reported` and `unknown` do not.
ASSESSED = frozenset({"accepted", "needs_work", "blocked", "unavailable"})
#: The assignment identity a task event carries as NOT_APPLICABLE, and an assignment event never does.
ASSIGNMENT_IDENTITY = ("dispatch_id", "worker", "role")
#: `base_revision` and `head_revision`: a full SHA-1 or SHA-256 commit id in either case.
#: Schema 1 promises a "full SHA" and never a case, so an uppercase id a writer copied is still schema 1.
LEDGER_SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
#: `head_revision` literals that stand in for a LEDGER_SHA commit id.
HEAD_PLACEHOLDERS = frozenset({UNKNOWN, NOT_APPLICABLE})
#: `report` literals that stand in for an absolute path, on every subject's events.
REPORT_PLACEHOLDERS = frozenset({UNKNOWN})
# `at` holds a timezone-qualified ISO-8601 timestamp, the form chronology.timestamp
# accepts. `report` and `dispatch_state` hold absolute paths free of NUL bytes
# (`_absolute`); `dispatch_state` also names an existing file (`_resolve_bound`).
#: A field name starts with a letter, so no ledger line can set the parser's own `_section` key.
FIELD = re.compile(r"^- ([a-z][a-z_]*): (.*)$")
FRONT_FIELD = re.compile(r"^([a-z_]+): (.*)$")


def _unusable(path, why):
    # Appending cannot repair a malformed earlier event: the ledger is append-only
    # and every event is validated, so the recovery is a new ledger file.
    return UsageError("Task ledger {} is not usable here: {}. Keep this file, record a recovered ledger at a new path "
                      "after reconciling its sources (state-schema.md, Task Ledger) and pass that path, or pass the "
                      "ledger this task's authorization records; nothing was closed.".format(path, why),
                      {"ledger": str(path)})


def ledger_events(path):
    """The ledger's frontmatter and events, each carrying every schema-1 field, or a refusal."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read the task ledger {}: {}. Pass the absolute TASK-LEDGER.md path the task "
                         "authorization records.".format(path, exc), {"ledger": str(path)}) from None
    front = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not front:
        raise _unusable(path, "it has no frontmatter")
    header = {}
    for line in front.group(1).splitlines():
        match = FRONT_FIELD.match(line)
        if match:
            if match.group(1) in FRONT_FIELDS and match.group(1) in header:
                raise _unusable(path, "its frontmatter repeats {}".format(match.group(1)))
            header[match.group(1)] = match.group(2).strip()
    missing = [key for key in FRONT_FIELDS if not header.get(key)]
    if missing:
        raise _unusable(path, "its frontmatter lacks " + ", ".join(missing))
    if header["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise _unusable(path, "it is schema {}, and this build reads schema {}".format(
            header["schema_version"], LEDGER_SCHEMA_VERSION))
    if LEDGER_SHA.fullmatch(header["base_revision"]) is None:
        raise _unusable(path, "its base_revision {!r} is not a full hexadecimal commit SHA".format(header["base_revision"]))
    if not _absolute(header["dispatch_state"]):
        raise _unusable(path, "its dispatch_state {!r} is not an absolute path free of NUL bytes".format(header["dispatch_state"]))
    events, current = [], None
    for line in text[front.end():].splitlines():
        if line.startswith("## "):
            current = {"_section": line[3:].strip()}
            events.append(current)
            continue
        match = FIELD.match(line)
        if current is not None and match:
            # A repeated schema field would let a later line replace a malformed
            # value before validation reads it. Prose bullets under `assessment`
            # may repeat names the schema does not use.
            if match.group(1) in EVENT_FIELDS and match.group(1) in current:
                raise _unusable(path, "event {} repeats {}".format(current["_section"], match.group(1)))
            current[match.group(1)] = match.group(2).strip()
    for event in events:
        absent = [key for key in EVENT_FIELDS if not event.get(key)]
        if absent:
            raise _unusable(path, "event {} lacks {}".format(event["_section"], ", ".join(absent)))
        if event["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise _unusable(path, "event {} is schema {}".format(event["_section"], event["schema_version"]))
        _check_formats(path, event)
    seen, repeated = set(), set()
    for event in events:
        (repeated if event["id"] in seen else seen).add(event["id"])
    if repeated:
        raise _unusable(path, "event id {} names more than one event".format(", ".join(sorted(repeated))))
    return header, events


def _check_formats(path, event):
    """Refuse an event whose non-prose fields are not in their schema-1 formats."""
    section = event["_section"]
    if event["id"] != section:
        raise _unusable(path, "event {} carries id {!r}, not its section heading".format(section, event["id"]))
    try:
        timestamp(event["at"], "at")
    except UsageError:
        raise _unusable(path, "event {} has at {!r}, not a timezone-qualified ISO-8601 timestamp".format(
            section, event["at"])) from None
    head = event["head_revision"]
    if head not in HEAD_PLACEHOLDERS and LEDGER_SHA.fullmatch(head) is None:
        raise _unusable(path, "event {} has head_revision {!r}, not a full hexadecimal commit SHA, unknown or "
                        "not_applicable".format(section, head))
    subject = event["subject"]
    if subject not in DECISIONS:
        raise _unusable(path, "event {} has subject {!r}, not {}".format(section, subject, " or ".join(SUBJECTS)))
    if event["decision"] not in DECISIONS[subject]:
        raise _unusable(path, "event {} has decision {!r}, not one of the {} decisions ({})".format(
            section, event["decision"], subject, ", ".join(sorted(DECISIONS[subject]))))
    for key in ASSIGNMENT_IDENTITY:
        if (event[key] == NOT_APPLICABLE) != (subject == "task"):
            raise _unusable(path, "event {} has {} {!r}; a task event carries not_applicable and an assignment "
                            "event its actual value".format(section, key, event[key]))
    report = event["report"]
    if report not in REPORT_PLACEHOLDERS and not _absolute(report):
        raise _unusable(path, "event {} has report {!r}, not an absolute path or unknown".format(section, report))


def _absolute(value):
    """Whether a ledger path field holds an absolute path free of NUL bytes."""
    return os.path.isabs(value) and "\0" not in value


def _resolve_bound(path, dispatch_state):
    """The ledger's dispatch_state resolved the same way on every supported Python, or a refusal.

    Non-strict `resolve` stopped raising on a symlink loop in Python 3.13, so
    resolve strictly: a loop raises RuntimeError before 3.13 and OSError from
    it on. A path that does not exist is refused too: the ledger must bind a
    utility state that exists on disk, never one a string comparison merely
    matches. Every failure is one refusal, so nothing is resolved twice.
    """
    try:
        return str(Path(dispatch_state).resolve(strict=True))
    except (OSError, RuntimeError) as exc:
        raise _unusable(path, "its dispatch_state {!r} does not resolve: {}".format(dispatch_state, exc)) from None


def assessed_event(path, member, state_path):
    """The latest assessed event for this enrollment's dispatch, from a ledger bound to this state."""
    header, events = ledger_events(path)
    if header["task"] != member["task"]:
        raise UsageError("Task ledger {} records task {!r}, not this enrollment's {!r}; pass the ledger for {}.".format(
            path, header["task"], member["task"], member["task"]), {"ledger": str(path)})
    bound = _resolve_bound(path, header["dispatch_state"])
    if bound != str(Path(state_path).expanduser().resolve()):
        raise _unusable(path, "it is bound to dispatch state {}, not {}".format(header["dispatch_state"], state_path))
    matching = [row for row in events if row["subject"] == "assignment" and row["dispatch_id"] == member["id"]
                and row["worker"] == member["agent"] and row["report"] == member["report"]]
    if not matching or matching[-1]["decision"] not in ASSESSED:
        latest = matching[-1]["decision"] if matching else None
        raise UsageError("The task ledger has no assessed outcome for dispatch {} ({}, {}): its latest event for it is {}. "
                         "Append the assessed decision ({}) to {} before closing the assignment.".format(
                             member["id"], member["agent"], member["report"], repr(latest) if latest else "missing",
                             ", ".join(sorted(ASSESSED)), path),
                         {"ledger": str(path), "latest_decision": latest})
    return matching[-1]


def _member(data, enrollment):
    member = next((row for row in data["members"] if row["id"] == enrollment), None)
    if member is None:
        raise UsageError("Unknown enrollment {!r}; run `{}` to list active assignments.".format(
            enrollment, runnable.command("supervision-status")),
                         {"enrollment": enrollment})
    return member


def close(state_path, enrollment, ledger, at):
    """Acknowledge this enrollment's pending events and resolve it, once its outcome is in the ledger."""
    ledger = str(Path(ledger).expanduser().resolve())
    member = _member(supervision.load(state_path), enrollment)
    assignment = supervision.expected_assignment(member)
    event = assessed_event(ledger, assignment, state_path)
    # A classifier gate only adds friction: an open re-read refuses any
    # closure, an open block refuses an accepted one (foreman/report_gates.py).
    # The gate lock is held through the closure, so no gate lands in between.
    with report_gates.holding(state_path):
        report_gates.require_clear(state_path, assignment["report"], event["decision"] == "accepted")
        outcome = "Task ledger event {}: {}".format(event.get("id", "unknown"), event["decision"])
        drained = supervision.drain(state_path)
        mine = [row for row in drained["events"] if row["member"] == enrollment]
        acknowledged = []
        if mine:
            acknowledged = supervision.acknowledge(state_path, {"through": drained["through"], "outcomes": [
                {"event": row["id"], "outcome": outcome, "evidence": [ledger]} for row in mine]}, at)["acknowledged"]
        resolved = supervision.resolve(state_path, {"id": enrollment, "outcome": outcome, "evidence": [ledger]}, at)
    return {"schema_version": 1, "enrollment": enrollment, "ledger_event": event.get("id"),
            "decision": event["decision"], "acknowledged": acknowledged, "resolved": resolved}


def wait_inputs(state_path, enrollment, warn=None):
    """Agent, report, base revision and send time for this enrollment's dispatch."""
    member = _member(supervision.load(state_path), enrollment)
    assignment = supervision.expected_assignment(member)
    state, usable = load_state_checked(state_path, warn, persist_migration=False)
    if not usable:
        raise StateError("The dispatch state at {} is unusable, so this dispatch's base and send time cannot be read; "
                         "restore it before checking the report.".format(state_path), {"state": str(state_path)})
    store = state["recovery"]
    task = store["tasks"].get(assignment["task"])
    # The enrollment id is its dispatch id; the dispatch's send time is what lets
    # repeated checkpoints reach the stall outcome (wait-report.sh --since).
    dispatch = next((row for row in store["dispatches"] if row.get("id") == enrollment), None)
    since = (dispatch.get("result") or {}).get("at") if dispatch and dispatch.get("status") == "applied" else None
    if since is None:
        raise UsageError("Dispatch {} has no applied send time in {}, so a checkpoint could never reach its stall "
                         "outcome. Reconcile the dispatch through references/dispatch-recovery.md before checking "
                         "its report.".format(enrollment, state_path), {"enrollment": enrollment})
    return {"agent": assignment["agent"], "report": assignment["report"],
            "base": task["base_revision"] if task else None, "since": since}


#: wait-report.sh's checkpoint verdicts. Any other exit, 2 above all, is a
#: usage, precondition or tool failure of the wait itself.
VERDICT_EXITS = frozenset({0, 1, 3, 4, 5})


def check(state_path, enrollment, worktree=None, *, run=subprocess.run, warn=None):
    """Run `wait-report.sh --once` for this enrollment; returns (payload, exit code)."""
    inputs = wait_inputs(state_path, enrollment, warn)
    argv = ["bash", str(Path(__file__).resolve().parents[1] / "wait-report.sh"), "--once"]
    for flag, value in (("--worktree", worktree), ("--base", inputs["base"]), ("--since", inputs["since"])):
        if value:
            argv += [flag, value]
    argv += [inputs["agent"], inputs["report"]]
    done = run(argv, capture_output=True, text=True, check=False)
    return {"schema_version": 1, "enrollment": enrollment, "inputs": inputs, "exit": done.returncode,
            "wait": done.stdout.strip(), "diagnostics": done.stderr.strip()}, done.returncode
