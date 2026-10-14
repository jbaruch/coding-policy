"""Recover an initial, owner-closed no-work assignment without rewriting history.

Recovery store v18 owns schema-1 receipts and their reserved retry identities.
The CLI holds the state transaction lock. Read-time validation is structural;
recovery and every new retry recheck saved evidence, Git and runtime absence.
"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import NoReturn

from . import chronology, recovery, runnable, supervision
from .errors import HerdrError, UsageError
from .herdr import error_code

INPUT_FIELDS = frozenset({"dispatch", "reason", "authorization", "evidence", "closure", "ledger"})
WAIT_FIELDS = frozenset({"agent", "state", "report_path", "found", "elapsed_seconds", "stall"})
ZERO_FIELDS = ("staged", "modified", "untracked", "unpushed_commits", "dispatch_commits")
GIT_MARKERS = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
               "rebase-merge", "rebase-apply", "sequencer")


def refuse(message) -> NoReturn:
    raise UsageError(message + " Preserve the original task and evidence; see references/no-work-recovery.md.", {})


def source(store, identifier):
    row = next((item for item in store["dispatches"] if item["id"] == identifier), None)
    if (row is None or row["status"] != "applied" or row["role"] != "developer"
            or row["fix_round"] is not None or not isinstance(row.get("worker_kind"), str)
            or row.get("report") is not None or row.get("refusal") is not None):
        refuse("Recovery requires an applied assignment-scoped initial developer with no review or refusal receipt.")
    return row


def eligible(store, assignments, task, fix_round):
    if fix_round is not None:
        return None
    latest = chronology.latest_assignment(assignments or [], task=task, role="developer", status="applied")
    if latest is None:
        return None
    return next((row for row in (store or {}).get("no_work_recoveries", [])
                 if row["task"] == task and row["assignment_index"] == latest[0]), None)


def _input(data):
    if not isinstance(data, dict) or set(data) != INPUT_FIELDS:
        refuse("Recovery requires dispatch, reason, authorization, evidence, closure and ledger.")
    for key in INPUT_FIELDS - {"authorization"}:
        recovery.text(data[key], key)
    recovery.authorization(data["authorization"])


def _json(path):
    evidence, body = recovery.receipt(path)
    try:
        return evidence, json.loads(body)
    except json.JSONDecodeError:
        refuse("Saved evidence must be the original owner JSON output.")


def _git(tree, *args):
    try:
        result = subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        refuse("Cannot inspect the original checkout: {}.".format(exc))
    if result.returncode:
        refuse("Git cannot verify the original checkout ({}): {}.".format(args[0], result.stderr.strip()))
    return result.stdout.strip()


def _clean_checkout(tree, base):
    if not isinstance(tree, str) or not Path(tree).is_absolute():
        refuse("No-work evidence must name the original absolute checkout.")
    gitdir = Path(_git(tree, "rev-parse", "--absolute-git-dir"))
    if any((gitdir / marker).exists() for marker in GIT_MARKERS):
        refuse("The original checkout has an operation in progress.")
    if (_git(tree, "status", "--porcelain", "--untracked-files=all")
            or _git(tree, "rev-parse", "HEAD") != base
            or _git(tree, "rev-list", "--count", "HEAD", "--not", "--remotes") != "0"):
        refuse("The original checkout contains changed, untracked or committed work.")


def _absent(client, binding):
    for action, target, missing in ((client.agent_get, binding["agent"], "agent_not_found"),
                                    (client.pane_get, binding["pane_id"], "pane_not_found")):
        try:
            action(target)
        except HerdrError as exc:
            if error_code(exc) != missing:
                raise
        else:
            refuse("The original worker or pane exists; absence is required before a fresh retry.")


def verify(state_path, state, data, client):
    """Read actual owner closure and no-work evidence; perform no runtime mutation."""
    from . import members

    _input(data)
    store = state["recovery"]
    row = source(store, data["dispatch"])
    task = recovery.task_record(store, row["task"])
    supervised = supervision.load(state_path)
    member = next((item for item in supervised["members"] if item["id"] == row["id"]), None)
    if member is None or member["active"] or member["resolution"] is None:
        refuse("Close the unavailable assignment through `{}` before recovering it.".format(runnable.command("close-member")))
    binding = supervision.expected_assignment(member)
    if (binding["agent"] != row["agent"] or binding["task"] != row["task"]
            or not binding["pane_id"] or not binding["report"]):
        refuse("Original supervision identity is incomplete or changed.")
    report = Path(binding["report"])
    if report.exists() or report.is_symlink():
        refuse("The original report now exists; recover its actual work.")
    reports = [member["observed"].get("report")] + [
        event["data"] for event in supervised["events"]
        if event["member"] == row["id"] and event["kind"] == "report_observed"]
    if any(isinstance(value, dict) and value.get("present") is True for value in reports):
        refuse("Supervision recorded a report; recover that evidence even if its file disappeared.")
    if any(item["active"] and item["assignment"]["task"] == row["task"] for item in supervised["members"]):
        refuse("The task still has an active supervised assignment.")
    if any(item["task"] == row["task"] and item["status"] in recovery.PENDING_STATUSES for item in store["dispatches"]):
        refuse("The task has an unresolved dispatch outcome.")
    if recovery.task_closure(store, state["assignments"], row["task"]) is not None:
        refuse("The original task is closed; recovery cannot reopen it.")
    if any(item.get("dispatch") == row["id"] for item in state["specialist_assessments"]):
        refuse("The original assignment has assessed work; preserve that outcome.")

    close_evidence, closed = _json(data["closure"])
    if (not isinstance(closed, dict) or type(closed.get("schema_version")) is not int
            or closed["schema_version"] != 1 or closed.get("enrollment") != row["id"]
            or closed.get("decision") != "unavailable" or closed.get("resolved") != member):
        refuse("Closure output does not match the owner's unavailable enrollment resolution.")
    closure = closed.get("pane_closure")
    if (not isinstance(closure, dict) or closure.get("closed") is not True
            or closure.get("agent") != binding["agent"] or closure.get("pane_id") != binding["pane_id"]
            or type(closure.get("replayed")) is not bool):
        refuse("Closure output does not prove retirement of the original pane.")
    ledger_evidence, _ = recovery.receipt(data["ledger"])
    resolution = member["resolution"]
    if not any(item.get("path") == ledger_evidence["path"] and item.get("sha256") == ledger_evidence["sha256"]
               for item in resolution["evidence"]):
        refuse("The ledger differs from the evidence the closure owner resolved.")
    event = members.assessed_event(data["ledger"], binding, state_path)
    header, _ = members.ledger_events(data["ledger"])
    if (event["decision"] != "unavailable" or event["role"] != "developer"
            or event["id"] != closed.get("ledger_event") or header["base_revision"] != task["base_revision"]
            or resolution["outcome"] != "Task ledger event {}: unavailable".format(event["id"])):
        refuse("The ledger does not bind this unavailable developer to its original task and base.")

    wait_evidence, wait = _json(data["evidence"])
    if (not isinstance(wait, dict) or set(wait) != WAIT_FIELDS or wait["found"] is not False
            or wait["agent"] not in (binding["agent"], binding["pane_id"])
            or wait["report_path"] != binding["report"] or wait["state"] not in ("idle", "done")
            or type(wait["elapsed_seconds"]) is not int or wait["elapsed_seconds"] <= 0):
        refuse("Supply the complete spent wait-report stall output for this worker and report.")
    stall = wait["stall"]
    proof = stall.get("evidence") if isinstance(stall, dict) else None
    if (not isinstance(stall, dict) or set(stall) != {"class", "evidence"} or stall["class"] != "no_work"
            or not isinstance(proof, dict) or set(proof) != {*ZERO_FIELDS, "worktree", "base", "readable", "mid_operation"}
            or proof["base"] != task["base_revision"] or proof["readable"] is not True
            or proof["mid_operation"] is not False
            or any(type(proof[key]) is not int or proof[key] != 0 for key in ZERO_FIELDS)):
        refuse("The spent wait does not prove no_work against the original base.")
    # Frozen prompt bytes bind the otherwise caller-supplied checkout path.
    common = recovery.receipt(row["common"])[1]
    brief = recovery.receipt(row["brief"])[1]
    if not isinstance(proof["worktree"], str) or proof["worktree"] not in common + brief:
        refuse("The no-work checkout is not named in the original frozen assignment.")
    if recovery.brief_identity({"common": row["common"], "developer": row["brief"]}, "developer", binding["report"]) != row.get("brief_identity"):
        refuse("The original frozen brief changed.")
    if row.get("oracle") is not None:
        oracle = row["oracle"]
        if recovery.receipt(oracle["path"])[0]["sha256"] != oracle["sha256"]:
            refuse("The original oracle artifact changed.")
    _absent(client, binding)
    _clean_checkout(proof["worktree"], task["base_revision"])
    return row, binding, {"wait": wait_evidence, "closure": close_evidence, "ledger": ledger_evidence}


def record(state_path, state, data, at, client):
    """Authorize one replacement initial assignment; identical recovery replays."""
    _input(data)
    store = state["recovery"]
    prior = next((item for item in store["no_work_recoveries"] if item["id"] == data["dispatch"]), None)
    if prior is not None:
        if prior["input"] != data:
            refuse("This recovery identity already holds different evidence or authorization.")
        # A completed replacement need not leave the checkout untouched. Replay
        # returns the durable receipt, never creates a new retry permission.
        validate(store, state["assignments"])
        return deepcopy(prior)
    row, binding, evidence = verify(state_path, state, data, client)
    if recovery.confirmed_fix(state["assignments"], row["task"]) != 0:
        refuse("A task with source corrections cannot recover an initial assignment.")
    latest = chronology.latest_assignment(state["assignments"], task=row["task"], role="developer", status="applied")
    if latest is None or latest[0] != row["assignment_index"]:
        refuse("Recover only the task's latest initial developer assignment.")
    chronology.timestamp(at, "No-work recovery")
    closed = supervision.load(state_path)["members"]
    resolved = next(item["resolution"] for item in closed if item["id"] == row["id"])
    if (chronology.timestamp(at, "No-work recovery") <= chronology.timestamp(latest[1]["at"], "Original assignment")
            or chronology.timestamp(at, "No-work recovery") < chronology.timestamp(resolved["at"], "Owner closure")):
        refuse("Recovery must follow the original assignment and owner closure.")
    result = {"schema_version": 1, "id": row["id"], "task": row["task"], "at": at,
              "assignment_index": row["assignment_index"], "input": deepcopy(data), "evidence": evidence,
              "source_sha256": recovery.canonical_digest(row), "task_sha256": recovery.canonical_digest(store["tasks"][row["task"]]),
              "binding": binding, "retries": []}
    store["no_work_recoveries"].append(result)
    return deepcopy(result)


def prepare(state_path, state, task, fix_round, identity, report, oracle, client):
    """Recheck recovery evidence before apply creates or sends to any new pane."""
    saved = eligible(state["recovery"], state["assignments"], task, fix_round)
    if saved is None:
        return
    row, binding, evidence = verify(state_path, state, saved["input"], client)
    if evidence != saved["evidence"] or binding != saved["binding"]:
        refuse("No-work recovery evidence changed before the retry.")
    if identity != row["brief_identity"] or oracle != row.get("oracle"):
        refuse("Retry the original brief and oracle unchanged, with a fresh report path.")
    if not isinstance(report, str) or Path(report).resolve() == Path(binding["report"]).resolve():
        refuse("A no-work replacement needs a fresh report path.")


def reserve(store, row, at):
    """Bind one live retry to its receipt, inside the dispatch reservation save."""
    if row["role"] != "developer" or row["fix_round"] is not None:
        return
    prior = [item for item in store["dispatches"] if item["task"] == row["task"]
             and item["role"] == "developer" and item["status"] == "applied"]
    if not prior:
        return
    # Dispatch arrays can contain imported history; append position is not time.
    if not store.get("no_work_recoveries"):
        return
    times = [(chronology.timestamp((item.get("result") or {}).get("at", item["at"]), "Applied dispatch"), item)
             for item in prior]
    latest_at = max(instant for instant, _ in times)
    latest = [item for instant, item in times if instant == latest_at]
    if len(latest) != 1:
        refuse("Initial assignment chronology is ambiguous; recover its original event times.")
    previous = latest[0]
    saved = next((item for item in store["no_work_recoveries"] if item["id"] == previous["id"]), None)
    if saved is None:
        return  # The normal history/native-refusal gates own other assignments.
    if chronology.timestamp(at, "Retry reservation") < chronology.timestamp(saved["at"], "Recovery"):
        refuse("The retry reservation must follow its recovery receipt.")
    if row.get("brief_identity") != previous.get("brief_identity") or row.get("oracle") != previous.get("oracle"):
        refuse("The replacement changes the original brief or oracle.")
    if row["agent"] == previous["agent"] or not isinstance(row.get("worker_kind"), str):
        refuse("The replacement must use a fresh assignment-scoped worker identity.")
    for identifier in saved["retries"]:
        previous = next(item for item in store["dispatches"] if item["id"] == identifier)
        if previous["status"] != "not_sent":
            refuse("The authorized no-work retry is already reserved, uncertain or applied.")
    if row["id"] not in saved["retries"]:
        saved["retries"].append(row["id"])


def follows(store, previous, row):
    return any(item["id"] == previous["id"] and row["id"] in item["retries"]
               for item in store["no_work_recoveries"])


def validate(store, assignments):
    """Validate durable relationships without treating old evidence as live state."""
    for saved in store["no_work_recoveries"]:
        required = {"schema_version", "id", "task", "at", "assignment_index", "input", "evidence",
                    "source_sha256", "task_sha256", "binding", "retries"}
        if not isinstance(saved, dict) or set(saved) != required or type(saved["schema_version"]) is not int or saved["schema_version"] != 1:
            refuse("Unsupported no-work recovery record.")
        _input(saved["input"])
        row = source(store, saved["id"])
        if (saved["input"]["dispatch"] != row["id"] or saved["task"] != row["task"]
                or type(saved["assignment_index"]) is not int or saved["assignment_index"] != row["assignment_index"]
                or recovery.canonical_digest(row) != saved["source_sha256"]
                or recovery.canonical_digest(store["tasks"][row["task"]]) != saved["task_sha256"]):
            refuse("No-work recovery lost its original dispatch, assignment or task binding.")
        chronology.timestamp(saved["at"], "No-work recovery")
        index = saved["assignment_index"]
        if (not 0 <= index < len(assignments)
                or chronology.timestamp(saved["at"], "No-work recovery") <= chronology.timestamp(assignments[index]["at"], "Original assignment")):
            refuse("No-work recovery must follow its original assignment.")
        if not isinstance(saved["evidence"], dict) or set(saved["evidence"]) != {"wait", "closure", "ledger"}:
            refuse("No-work recovery lacks its evidence digests.")
        for value in saved["evidence"].values():
            recovery.validate_receipt(value)
        binding = saved["binding"]
        if (not isinstance(binding, dict) or binding.get("id") != row["id"]
                or binding.get("agent") != row["agent"] or binding.get("task") != row["task"]
                or not isinstance(binding.get("report"), str) or not Path(binding["report"]).is_absolute()
                or not isinstance(binding.get("pane_id"), str) or not binding["pane_id"]):
            refuse("No-work recovery lost its supervision binding.")
        retries = saved["retries"]
        if not isinstance(retries, list) or any(not isinstance(item, str) for item in retries) or len(set(retries)) != len(retries):
            refuse("No-work retry identities are malformed or duplicated.")
        used = 0
        for identifier in retries:
            retry = next((item for item in store["dispatches"] if item["id"] == identifier), None)
            if (retry is None or retry["id"] == row["id"] or retry["task"] != row["task"]
                    or retry["role"] != "developer" or retry["fix_round"] is not None
                    or retry["agent"] == row["agent"] or not isinstance(retry.get("worker_kind"), str)
                    or retry.get("brief_identity") != row.get("brief_identity") or retry.get("oracle") != row.get("oracle")
                    or chronology.timestamp(retry["at"], "Retry") < chronology.timestamp(saved["at"], "Recovery")):
                refuse("No-work retry changed its original assignment contract.")
            used += retry["status"] != "not_sent"
        if used > 1:
            refuse("No-work recovery permits only one reserved, uncertain or applied replacement.")
