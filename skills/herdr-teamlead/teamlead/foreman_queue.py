"""Which open tasks are waiting for the foreman's next seat.

The ledger records that a task is "queued" and nothing about what it waits
for. The foreman used to carry that in its conversation, and a foreman reset
at every round boundary loses it (#483). Everything that decides the stage is
already in the owner records, so this joins them rather than asking the
foreman to write a second copy that could drift.

Stages, read from each open task's latest applied developer round:

- `reviewer` / `tester` -- that verifier has no assignment since the round
- `gate` -- both verifiers were dispatched and no approved report is recorded
  for the round's developer dispatch
- `release` -- an approved report is recorded and no release followed
- `close` -- a release followed and the task has no `task_closed` event

A task with an active supervision enrollment is in flight, not waiting, and is
omitted. Entries are ordered oldest first by the event that opened the wait;
the foreman may take them in another order. Read-only.
"""

from datetime import timezone

from .chronology import latest_assignment, timestamp
from .recovery import task_closure

QUEUE_SCHEMA_VERSION = 1
VERIFIERS = ("reviewer", "tester")


def _after(assignments, task, role, instant):
    """The latest applied `role` row for `task` strictly after `instant`, or None."""
    found = latest_assignment(assignments, task=task, role=role, status="applied")
    if found is None:
        return None
    at = timestamp(found[1].get("at"), "Assignment {} chronology".format(found[0]))
    return (at, found[1]) if at > instant else None


def _approved(store, task, developer_at):
    """Whether an approved report is recorded for a developer dispatch of this round."""
    for row in store["dispatches"]:
        report = row.get("report")
        if (row.get("task") == task and row.get("role") == "developer" and row.get("status") == "applied"
                and isinstance(report, dict) and report.get("verdict") == "approved"
                and timestamp(report.get("at"), "Report receipt time") > developer_at):
            return True
    return False


def waiting(store, assignments, busy_tasks):
    """Return `{"schema_version", "queue": [...]}` for the open tasks waiting on a seat."""
    entries = []
    tasks = sorted({row["task"] for row in assignments
                    if row.get("role") == "developer" and row.get("status") == "applied" and row.get("task")})
    for task in tasks:
        if task in busy_tasks or task_closure(store, assignments, task) is not None:
            continue
        latest = latest_assignment(assignments, task=task, role="developer", status="applied")
        if latest is None:
            continue
        index, developer = latest
        developer_at = timestamp(developer.get("at"), "Assignment {} chronology".format(index))
        verified = {role: _after(assignments, task, role, developer_at) for role in VERIFIERS}
        missing = [role for role in VERIFIERS if verified[role] is None]
        delivered = [found[0] for found in verified.values() if found is not None]
        release = _after(assignments, task, "release", developer_at)
        if release is not None:
            stage, since = "close", release[0]
        elif missing:
            stage, since = missing[0], developer_at
        elif _approved(store, task, developer_at):
            stage, since = "release", max(delivered)
        else:
            stage, since = "gate", max(delivered)
        entries.append({"task": task, "waiting_for": stage, "also_missing": missing[1:],
                        "since": since.astimezone(timezone.utc).isoformat(), "developer": developer.get("agent"),
                        "fix_round": developer.get("fix_round") or 0})
    entries.sort(key=lambda entry: (entry["since"], entry["task"]))
    return {"schema_version": QUEUE_SCHEMA_VERSION, "queue": entries}
