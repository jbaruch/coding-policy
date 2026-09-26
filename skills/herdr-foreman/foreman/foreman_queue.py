"""Which open tasks are waiting for a seat.

The ledger records that a task is "queued" and nothing about which seat it
waits for. The foreman used to carry that in its conversation, and a foreman
reset at every round boundary loses it (#483). The owner records already hold
it, so this joins them rather than asking the foreman to keep a second copy.

A task is open while it has no `task_closed` event in force. An open task
waits for:

- `developer` -- it is registered and has no applied developer assignment
- `reviewer` / `tester` -- that responsibility has no applied assignment since
  the task's latest applied developer round

Only seats are listed. Gating, release and closure are the foreman's own
decisions within a round, never a seat, so this reports none of them.

A partitioned verifier stays listed while any of its seats are dispatched: the
records hold the dispatched slices but not the partition, so completeness is
the foreman's check against its validated partition. `dispatched_seats` names
what went out. A task with an active supervision enrollment is in flight and
omitted. Entries run oldest first by the event that opened the wait. Read-only.
"""

from datetime import timezone

from .chronology import latest_assignment, timestamp
from .tiers import SEAT_SEPARATOR, canonical_role
from .recovery import task_closure

QUEUE_SCHEMA_VERSION = 1
VERIFIERS = ("reviewer", "tester")


def _applied_at(assignments, index):
    return timestamp(assignments[index].get("at"), "Assignment {} chronology".format(index))


def _dispatched_seats(store, assignments, task, role, instant):
    """Seats of `role` applied for `task` after `instant`.

    Assignment rows name the responsibility and dispatch rows the seat, so a
    partitioned round is read from the dispatches.
    """
    seats = {row["role"] for row in store["dispatches"]
             if row.get("task") == task and row.get("status") == "applied"
             and isinstance(row.get("role"), str) and canonical_role(row["role"]) == role
             and isinstance(row.get("assignment_index"), int) and 0 <= row["assignment_index"] < len(assignments)
             and _applied_at(assignments, row["assignment_index"]) > instant}
    found = latest_assignment(assignments, task=task, role=role, status="applied")
    if not seats and found is not None and _applied_at(assignments, found[0]) > instant:
        seats.add(role)
    return sorted(seats)


def _utc(instant):
    return instant.astimezone(timezone.utc).isoformat()


def waiting(store, assignments, busy_tasks):
    """Return `{"schema_version", "queue": [...]}` for the open tasks waiting on a seat."""
    entries = []
    tasks = sorted(set(store["tasks"]) | {row["task"] for row in assignments if row.get("task")})
    for task in tasks:
        if task in busy_tasks or task_closure(store, assignments, task) is not None:
            continue
        latest = latest_assignment(assignments, task=task, role="developer", status="applied")
        if latest is None:
            if task in store["tasks"]:
                registered = timestamp(store["tasks"][task]["at"], "Task {!r} registration time".format(task))
                entries.append({"task": task, "waiting_for": ["developer"], "dispatched_seats": {},
                                "since": _utc(registered), "developer": None, "fix_round": None})
            continue
        developer_at = _applied_at(assignments, latest[0])
        seats = {role: _dispatched_seats(store, assignments, task, role, developer_at) for role in VERIFIERS}
        pending = [role for role in VERIFIERS
                   if not seats[role] or any(SEAT_SEPARATOR in seat for seat in seats[role])]
        if not pending:
            continue
        entries.append({"task": task, "waiting_for": pending,
                        "dispatched_seats": {role: seats[role] for role in VERIFIERS if seats[role]},
                        "since": _utc(developer_at), "developer": latest[1].get("agent"),
                        "fix_round": latest[1].get("fix_round") or 0})
    entries.sort(key=lambda entry: (entry["since"], entry["task"]))
    return {"schema_version": QUEUE_SCHEMA_VERSION, "queue": entries}
