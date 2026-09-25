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

import re
import subprocess
from pathlib import Path

from . import supervision
from .errors import StateError, UsageError
from .state import load_state_checked

#: Ledger decisions that record an assessment (references/task-ledger.md);
#: `pending`, `reported` and `unknown` do not.
ASSESSED = frozenset({"accepted", "needs_work", "blocked", "unavailable"})
#: The task-ledger schema this reader accepts (state-schema.md, Task Ledger).
LEDGER_SCHEMA_VERSION = "1"
FRONT_FIELDS = ("schema_version", "task", "base_revision", "dispatch_state")
EVENT_FIELDS = ("schema_version", "id", "at", "subject", "dispatch_id", "worker", "role", "report", "observed",
                "decision", "head_revision", "evidence", "assessment")
FIELD = re.compile(r"^- ([a-z_]+): (.*)$")
FRONT_FIELD = re.compile(r"^([a-z_]+): (.*)$")


def _unusable(path, why):
    return UsageError("Task ledger {} is not usable here: {}. Fix the ledger by appending a correct event, or pass "
                      "the ledger this task's authorization records; nothing was closed.".format(path, why),
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
            header[match.group(1)] = match.group(2).strip()
    missing = [key for key in FRONT_FIELDS if not header.get(key)]
    if missing:
        raise _unusable(path, "its frontmatter lacks " + ", ".join(missing))
    if header["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise _unusable(path, "it is schema {}, and this build reads schema {}".format(
            header["schema_version"], LEDGER_SCHEMA_VERSION))
    events, current = [], None
    for line in text[front.end():].splitlines():
        if line.startswith("## "):
            current = {"_section": line[3:].strip()}
            events.append(current)
            continue
        match = FIELD.match(line)
        if current is not None and match:
            current[match.group(1)] = match.group(2).strip()
    for event in events:
        absent = [key for key in EVENT_FIELDS if not event.get(key)]
        if absent:
            raise _unusable(path, "event {} lacks {}".format(event["_section"], ", ".join(absent)))
        if event["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise _unusable(path, "event {} is schema {}".format(event["_section"], event["schema_version"]))
    return header, events


def assessed_event(path, member, state_path):
    """The latest assessed event for this enrollment's dispatch, from a ledger bound to this state."""
    header, events = ledger_events(path)
    if header["task"] != member["task"]:
        raise UsageError("Task ledger {} records task {!r}, not this enrollment's {!r}; pass the ledger for {}.".format(
            path, header["task"], member["task"], member["task"]), {"ledger": str(path)})
    bound = str(Path(header["dispatch_state"]).expanduser().resolve())
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
        raise UsageError("Unknown enrollment {!r}; run supervision-status to list active assignments.".format(enrollment),
                         {"enrollment": enrollment})
    return member


def close(state_path, enrollment, ledger, at):
    """Acknowledge this enrollment's pending events and resolve it, once its outcome is in the ledger."""
    ledger = str(Path(ledger).expanduser().resolve())
    member = _member(supervision.load(state_path), enrollment)
    assignment = supervision.expected_assignment(member)
    event = assessed_event(ledger, assignment, state_path)
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
