"""The durable records one foreman decision must load.

A foreman reset at every round boundary loses whatever it knew from carrying
the session (#483). What a decision needs is not a judgment call: the owner
records already link each task to its dispatches, briefs, report paths, review
receipts and recovery decisions. This joins those links per decision, so the
foreman loads what the decision depends on and nothing else.

Decisions and what each adds to the task core (task record, budget status,
and each open attention item in full):

- `plan` -- the task's queue entry and any developer reservation on it
- `brief` -- every brief and report of the current round, blocking review
  receipts, and the active correction plan and approach
- `gate` -- every brief, common file and report dispatched in the current
  round, the round being the task's latest developer assignment onward
- `diagnose` -- every report and review receipt across all rounds (superseded
  receipts included), the task's specialist assessments with their reports,
  checkpoints, diagnoses, approaches and correction plans
- `wake` -- keyed by enrollment, not task: that dispatch's brief, common and
  report, plus its task core

It is the must-load set, never a ceiling: a classifier may add lessons on top,
and nothing may remove an entry (#483 decision 2). Files are listed with
`present` so a missing report surfaces instead of vanishing. Read-only.
"""

from .chronology import latest_assignment, timestamp
from .foreman_queue import waiting
from .recovery import active_plans, current_approach, developer_reservations, task_statuses

LOAD_SET_SCHEMA_VERSION = 1
DECISIONS = ("plan", "brief", "gate", "diagnose", "wake")
OPEN_ATTENTION = frozenset({"open", "deferred"})


class _Files:
    """Ordered, de-duplicated file references with the reason each is loaded."""

    def __init__(self, exists):
        self.rows, self.seen, self.exists = [], set(), exists

    def add(self, path, why):
        if isinstance(path, str) and path and path not in self.seen:
            self.seen.add(path)
            self.rows.append({"path": path, "why": why, "present": self.exists(path)})


def _applied_at(assignments, index):
    return timestamp(assignments[index].get("at"), "Assignment {} chronology".format(index))


def _task_dispatches(store, assignments, task, since=None):
    """Applied dispatches of `task`, oldest first, optionally from `since` on."""
    rows = []
    for row in store["dispatches"]:
        index = row.get("assignment_index")
        if (row.get("task") != task or row.get("status") != "applied" or not isinstance(index, int)
                or not 0 <= index < len(assignments)):
            continue
        at = _applied_at(assignments, index)
        if since is None or at >= since:
            rows.append((at, row))
    rows.sort(key=lambda pair: (pair[0], pair[1].get("id", "")))
    return [row for _at, row in rows]


def _core(state, attention_entries, task):
    store = state["recovery"]
    return {"task": store["tasks"].get(task),
            "status": task_statuses(store, state["assignments"]).get(task),
            "attention": [entry for entry in attention_entries.values()
                          if entry.get("task") == task and entry["status"] in OPEN_ATTENTION]}


def _round_start(assignments, task):
    latest = latest_assignment(assignments, task=task, role="developer", status="applied")
    return None if latest is None else _applied_at(assignments, latest[0])


def _review_receipts(store, task):
    """Every review receipt recorded for the task, superseded ones included."""
    receipts = [row["report"] for row in store["dispatches"]
                if row.get("task") == task and isinstance(row.get("report"), dict)]
    receipts += [event["details"]["previous"] for event in store["events"]
                 if event.get("kind") == "review_superseded" and event.get("task") == task
                 and isinstance(event.get("details", {}).get("previous"), dict)]
    return receipts


def _add_dispatch_files(files, row, reports, *, briefs=True):
    label = "{} {}".format(row.get("role"), row.get("id"))
    if briefs:
        files.add(row.get("brief"), "brief for " + label)
        files.add(row.get("common"), "common brief for " + label)
    files.add(reports.get(row.get("id")), "report for " + label)


def build(state, reports, attention_entries, busy_tasks, decision, *, task=None, enrollment=None, exists):
    """Return the load set for one decision.

    `reports` maps an enrollment (dispatch) id to its report path, from the
    supervision owner. `busy_tasks` feeds the queue entry. `exists` is the
    file probe, injected so the join stays testable.
    """
    store, assignments = state["recovery"], state["assignments"]
    files, records = _Files(exists), {}
    if decision == "wake":
        row = next((item for item in store["dispatches"] if item.get("id") == enrollment), None)
        task = row.get("task") if row else None
        records["dispatch"] = row
        if row is not None:
            _add_dispatch_files(files, row, reports)
    start = _round_start(assignments, task) if task is not None else None
    if decision == "plan":
        records["queue"] = next((entry for entry in waiting(store, assignments, busy_tasks)["queue"]
                                 if entry["task"] == task), None)
        records["reserved_developer"] = sorted(agent for agent, held in developer_reservations(store, assignments).items()
                                               if held == task)
    elif decision in ("brief", "gate"):
        for row in _task_dispatches(store, assignments, task, since=start):
            _add_dispatch_files(files, row, reports)
        if decision == "brief":
            receipts = [row["report"] for row in store["dispatches"]
                        if row.get("task") == task and isinstance(row.get("report"), dict)
                        and row["report"].get("verdict") == "blocking"]
            for receipt in receipts:
                files.add(receipt.get("report"), "blocking review receipt at {}".format(receipt.get("head_revision")))
            records["correction_plan"] = next((plan for plan in reversed(active_plans(store)) if plan["task"] == task), None)
            records["approach"] = current_approach(store, task)
    elif decision == "diagnose":
        for row in _task_dispatches(store, assignments, task):
            _add_dispatch_files(files, row, reports, briefs=False)
        for receipt in _review_receipts(store, task):
            files.add(receipt.get("report"), "{} review receipt at {}".format(receipt.get("verdict"), receipt.get("head_revision")))
        records["assessments"] = [row for row in state["specialist_assessments"] if row.get("task") == task]
        for assessment in records["assessments"]:
            files.add(assessment.get("report"), "assessed {} report".format(assessment.get("role", "specialist")))
        for name in ("checkpoints", "diagnoses", "approaches", "plans"):
            records[name] = [row for row in store[name] if row.get("task") == task]
    return {"schema_version": LOAD_SET_SCHEMA_VERSION, "decision": decision, "task": task,
            "enrollment": enrollment, "round_start": start.isoformat() if start else None,
            "core": _core(state, attention_entries, task) if task is not None else None,
            "records": records, "files": files.rows}
