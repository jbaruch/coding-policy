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
FIELD = re.compile(r"^- ([a-z_]+): (.*)$")


def ledger_events(path):
    """The task ledger's frontmatter task and its event sections, in order."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read the task ledger {}: {}. Pass the absolute TASK-LEDGER.md path the task "
                         "authorization records.".format(path, exc), {"ledger": str(path)}) from None
    front = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    task = None
    if front:
        match = re.search(r"^task: (.+)$", front.group(1), re.M)
        task = match.group(1).strip() if match else None
    events, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = {}
            events.append(current)
            continue
        match = FIELD.match(line)
        if current is not None and match:
            current[match.group(1)] = match.group(2).strip()
    return task, events


def assessed_event(path, member):
    """The latest assessed assignment event for this enrollment's worker and report."""
    task, events = ledger_events(path)
    if task != member["task"]:
        raise UsageError("Task ledger {} records task {!r}, not this enrollment's {!r}; pass the ledger for {}.".format(
            path, task, member["task"], member["task"]), {"ledger": str(path)})
    matching = [row for row in events if row.get("subject") == "assignment" and row.get("worker") == member["agent"]
                and row.get("report") == member["report"]]
    if not matching or matching[-1].get("decision") not in ASSESSED:
        latest = matching[-1].get("decision") if matching else None
        raise UsageError("The task ledger has no assessed outcome for {} ({}): its latest event for that report is {}. "
                         "Append the assessed decision ({}) to {} before closing the assignment.".format(
                             member["agent"], member["report"], repr(latest) if latest else "missing",
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
    event = assessed_event(ledger, assignment)
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
    dispatch = next((row for row in reversed(store["dispatches"])
                     if row.get("agent") == assignment["agent"] and row.get("task") == assignment["task"]
                     and row.get("report") == assignment["report"] and row.get("status") == "applied"), None)
    return {"agent": assignment["agent"], "report": assignment["report"],
            "base": task["base_revision"] if task else None,
            "since": (dispatch.get("result") or {}).get("at") if dispatch else None}


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
