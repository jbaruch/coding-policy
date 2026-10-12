"""Codex bootstrap transport evidence and owned-shell retirement (#756).

The CLI holds the state transaction lock. See references/bootstrap-recovery.md
for the input/output contract and state-schema.md for this owner's sidecar.
No terminal input, launch, native continuity or correction authority is issued.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import lifecycle, recovery, supervision
from .assign import assignment_text, tiered_prompt
from .composer import strip_ansi
from .errors import HerdrError, StateError, UsageError
from .herdr import error_code
from .launch import require_empty_shell
from .state import save_state

SCHEMA_VERSION = 1
BOOTSTRAP_ERROR = "Error: account/read failed during TUI bootstrap:"


def store_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".bootstrap.json")


def load(state_path):
    path = store_path(state_path)
    data = supervision.read_json(path)
    if data is None:
        return {"schema_version": SCHEMA_VERSION, "origins": [], "outcomes": []}
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != SCHEMA_VERSION
            or not isinstance(data.get("origins"), list) or not isinstance(data.get("outcomes"), list)):
        raise StateError("Unsupported bootstrap evidence in {}; preserve it and update the owner before recovery.".format(path), {})
    for rows, fields in ((data["origins"], ("dispatch", "at", "pane_id", "process", "shell_pid")),
                         (data["outcomes"], ("dispatch", "at", "input", "evidence", "origin", "historical_status", "pane_sha256", "closure"))):
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("schema_version")) is not int
                    or row["schema_version"] != SCHEMA_VERSION or any(key not in row for key in fields)
                    or not isinstance(row["dispatch"], str) or not row["dispatch"]
                    or not isinstance(row["at"], str) or not row["at"]):
                raise StateError("Malformed bootstrap evidence in {}; restore the original owner record before recovery.".format(path), {})
            supervision.timestamp(row["at"])
            if rows is data["origins"]:
                _validate_origin(row)
            elif (not isinstance(row["input"], dict) or not isinstance(row["evidence"], dict)
                    or not isinstance(row["origin"], dict)
                    or row["historical_status"] not in ("sending", "sent_but_not_started", "applied", "reserved")
                    or not isinstance(row["pane_sha256"], str) or len(row["pane_sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in row["pane_sha256"])
                    or row["closure"] is not None and not _valid_closure(row["closure"], row["origin"].get("pane_id"))):
                raise StateError("Malformed bootstrap outcome in {}; restore the original owner record before recovery.".format(path), {})
            else:
                recovery.validate_receipt(row["evidence"])
                _validate_origin(row["origin"])
    origins = [(row["dispatch"], row["pane_id"], row["process"]["pid"]) for row in data["origins"]]
    outcomes = [(row["dispatch"], row["origin"]["pane_id"], row["origin"]["process"]["pid"]) for row in data["outcomes"]]
    if len(set(origins)) != len(origins) or len(set(outcomes)) != len(outcomes):
        raise StateError("Duplicate bootstrap launch/outcome identities in {}; restore the unambiguous original owner evidence.".format(path), {})
    return data


def _valid_closure(closure, pane):
    return (isinstance(closure, dict) and set(closure) == {"pane_id", "agent", "closed", "replayed"}
            and closure["pane_id"] == pane and isinstance(closure["agent"], str) and bool(closure["agent"])
            and closure["closed"] is True and type(closure["replayed"]) is bool)


def _validate_origin(row):
    process = row.get("process")
    if (type(row.get("shell_pid")) is not int or row["shell_pid"] <= 0
            or not isinstance(row.get("pane_id"), str) or not row["pane_id"]
            or not isinstance(process, dict) or process.get("source") != "process_argv"
            or type(process.get("pid")) is not int or process["pid"] <= 0
            or process["pid"] == row["shell_pid"] or process.get("pane_id") != row["pane_id"]
            or not isinstance(process.get("argv"), list) or not process["argv"]
            or any(not isinstance(arg, str) for arg in process["argv"])):
        raise StateError("Original bootstrap process/shell proof is incomplete; preserve the pane and restore its original launch evidence.", {})
    if "evidence" in row:
        recovery.validate_receipt(row["evidence"])


def remember(state_path, dispatch, pane, shell, process, at):
    """Persist the launch root before prompt input; append across transport retries."""
    supervision.timestamp(at)
    row = {"schema_version": SCHEMA_VERSION, "dispatch": dispatch, "at": at,
           "pane_id": pane, "shell_pid": shell, "process": deepcopy(process)}
    _validate_origin(row)
    data = load(state_path)
    matching = [item for item in data["origins"] if (item["dispatch"], item["pane_id"], item["process"]["pid"])
                == (dispatch, pane, process["pid"])]
    if matching and matching[0] != row:
        raise StateError("Bootstrap launch identity already names different provenance; preserve the original receipt before sending input.", {})
    if not matching:
        data["origins"].append(row)
        save_state(store_path(state_path), data)


def _source(state, state_path, identifier):
    row = next((item for item in state["recovery"]["dispatches"] if item["id"] == identifier), None)
    if row is None or row.get("provider") != "codex" or not isinstance(row.get("worker_kind"), str):
        raise UsageError("Bootstrap recovery needs the original assignment-scoped Codex dispatch; inspect owner state for its identity.", {})
    context = row.get("context_before_send") or {}
    observed = row.get("observed_before") or {}
    result = row.get("result") or {}
    if (row["status"] not in recovery.PENDING_STATUSES | {"applied", "not_sent"}
            or row.get("report") is not None or row.get("refusal") is not None
            or "context_session" not in observed or observed["context_session"] is not None
            or "context_session" not in context or context["context_session"] is not None
            or result.get("started") is True or result.get("context_session") is not None
            or row["status"] == "applied" and result.get("started") is not False):
        raise UsageError("Dispatch has native work or lacks explicit no-session observations; preserve it and recover its actual report/transport evidence.", {})
    history = [item for item in state["assignments"] if item.get("agent") == row["agent"]]
    if (any(item.get("context_session") is not None for item in history)
            or len(history) > (1 if result else 0)):
        raise UsageError("Worker history contains other or native work; preserve its pane and resolve that work before bootstrap recovery.", {})
    supervised = supervision.load(state_path)
    member = next((item for item in supervised["members"] if item["id"] == identifier), None)
    if member is None:
        raise UsageError("Original supervision enrollment is missing; restore its owner evidence before bootstrap recovery.", {})
    observations = list(member["observed"].items()) + [
        (event["kind"].removesuffix("_observed"), event["data"])
        for event in supervised["events"] if event["member"] == identifier]
    if any(isinstance(value, dict) and (
            key in {"identity", "identity_changed", "identity_unverified"} and value.get("native_session") is not None
            or key == "report" and value.get("present") is True
            or key == "lifecycle" and value.get("status") in ("working", "done"))
           for key, value in observations):
        raise UsageError("Supervision recorded possible native work or a report; preserve its evidence and reconcile that work before bootstrap cleanup.", {})
    binding = supervision.expected_assignment(member)
    if (binding["agent"] != row["agent"] or binding["task"] != row["task"]
            or binding["pane_id"] != observed.get("pane_id") or binding.get("native_session") is not None
            or not binding.get("report") or Path(binding["report"]).exists() or Path(binding["report"]).is_symlink()):
        raise UsageError("Original pane/native/report binding changed or report work exists; preserve it and inspect the enrolled report.", {})
    return row, member, binding


def _prompt(row):
    tier = (row.get("context_before_send") or {}).get("tier")
    if not isinstance(tier, dict) or not isinstance(tier.get("model"), str):
        raise UsageError("Original prompt tier is missing; restore its frozen dispatch evidence before recovery.", {})
    prompt, digest = tiered_prompt(assignment_text(row["role"], row["common"], row["brief"]),
                                  tier, row["common"], row["brief"])
    if digest != tier.get("prompt_hash"):
        raise UsageError("Frozen prompt inputs changed; restore the exact original COMMON/brief bytes before recovery.", {})
    return prompt, tier.get("verified")


def _draft(body, prompt):
    # Match literal provider output, not a natural-language assessment. Hard
    # terminal wraps may split a word or a path at any column.
    flat = "".join(strip_ansi(body).split())
    expected = "".join(prompt.split())
    prefix = "Unsentdraft:"
    suffix = "".join(BOOTSTRAP_ERROR.split())
    if flat.count(prefix) != 1 or prefix + expected + suffix not in flat:
        raise UsageError("Evidence must show the complete frozen prompt under Codex's Unsent draft heading immediately followed by its account/read bootstrap error; preserve unknown transport.", {})


def _archived_origin(data, row, process, pane, at):
    """Bind a pre-exit process-info archive to legacy owner process evidence."""
    evidence, body = recovery.receipt(data["origin_evidence"])
    try:
        info = json.loads(body)
    except json.JSONDecodeError:
        raise UsageError("Original process evidence must be saved Herdr pane process-info JSON; preserve the pane and supply the actual pre-exit archive.", {}) from None
    if isinstance(info, dict) and "result" in info:
        info = info["result"]
    if isinstance(info, dict) and "process_info" in info:
        info = info["process_info"]
    if (not isinstance(info, dict) or info.get("pane_id") != pane
            or not isinstance(process, dict) or not isinstance(info.get("foreground_processes"), list)
            or len(info["foreground_processes"]) != 1):
        raise UsageError("Original process archive lacks this dispatch's single running Codex process; preserve the pane and inspect its pre-exit evidence.", {})
    foreground = info["foreground_processes"][0]
    if (not isinstance(foreground, dict) or foreground.get("pid") != process.get("pid")
            or foreground.get("argv") != process.get("argv")):
        raise UsageError("Original process archive differs from the owner-recorded PID/argv; preserve the pane and recover the actual launch evidence.", {})
    origin = {"schema_version": SCHEMA_VERSION, "dispatch": row["id"], "at": at,
              "pane_id": pane, "process": deepcopy(process), "shell_pid": info.get("shell_pid"),
              "evidence": evidence}
    _validate_origin(origin)
    return origin


def reconcile(state_path, state, data, at, client):
    """Record one no-turn outcome, then close only the original empty shell.

    Historical applied rows stay immutable and consume their existing budget.
    Pending sends become not_sent through the ordinary owner reconciliation.
    Repeating identical input retries cleanup after a crash, never dispatch.
    """
    supervision.timestamp(at)
    required = {"dispatch", "reason", "authorization", "evidence"}
    if (not isinstance(data, dict) or set(data) not in (required, required | {"origin_evidence"})):
        raise UsageError("Bootstrap recovery requires dispatch, reason, authorization and saved pane evidence; see references/bootstrap-recovery.md.", {})
    recovery.authorization(data["authorization"])
    recovery.text(data["reason"], "bootstrap recovery reason")
    row, member, binding = _source(state, state_path, data["dispatch"])
    prompt, process = _prompt(row)
    evidence, body = recovery.receipt(data["evidence"])
    _draft(body, prompt)
    saved = load(state_path)
    prior = next((item for item in saved["outcomes"] if item["dispatch"] == row["id"]
                  and item["origin"]["pane_id"] == binding["pane_id"] and item["origin"]["process"] == process), None)
    if row["status"] == "not_sent" and prior is None:
        raise UsageError("This dispatch already has another not_sent outcome; replay its original owner cleanup instead of creating a bootstrap outcome.", {})
    if prior is not None and (prior["input"] != data or prior["evidence"] != evidence):
        raise UsageError("Bootstrap outcome is already recorded; replay identical input with unchanged evidence to finish cleanup.", {})
    origins = [item for item in saved["origins"] if item["dispatch"] == row["id"]
               and item["pane_id"] == binding["pane_id"] and item["process"] == process]
    if not origins and "origin_evidence" in data:
        origin = _archived_origin(data, row, process, binding["pane_id"], at)
        origins.append(origin)
        saved["origins"].append(origin)
    if not origins:
        raise UsageError("No original root-shell proof was recorded for this dispatch. Preserve the surviving pane; a current shell or missing agent cannot establish launch ownership. Inspect the original launch evidence without editing owner state.", {})
    origin = origins[-1]
    if "origin_evidence" in data:
        current_origin = _archived_origin(data, row, process, binding["pane_id"], origin["at"])
        if current_origin != origin:
            raise UsageError("Archived launch evidence changed; restore the exact original bytes before retrying recovery.", {})
    if prior is not None and prior["origin"] != origin:
        raise UsageError("Original bootstrap launch evidence changed; preserve the surface and restore its owner record.", {})

    if prior is not None and prior["closure"] is not None:
        if prior["closure"]["agent"] != row["agent"]:
            raise StateError("Completed bootstrap receipt names another agent; restore the original closure evidence before accepting retirement.", {})
        return {**prior, "outcome": "not_sent", "retry_authorized": False, "history_preserved": True}

    def observe():
        _source(state, state_path, row["id"])
        current_prompt, current_process = _prompt(row)
        if (current_prompt, current_process) != (prompt, process):
            raise UsageError("Original prompt/process evidence changed; preserve the pane and restore the original dispatch.", {})
        current_receipt, _ = recovery.receipt(data["evidence"])
        if current_receipt != evidence:
            raise UsageError("Saved pane evidence changed; restore the original bytes before retrying recovery.", {})
        if "origin_evidence" in data and _archived_origin(data, row, process, binding["pane_id"], origin["at"]) != origin:
            raise UsageError("Archived launch evidence changed; restore the original bytes before cleanup.", {})
        try:
            client.agent_get(row["agent"])
        except HerdrError as exc:
            if error_code(exc) != "agent_not_found":
                raise
        else:
            raise UsageError("The worker identity is live again; preserve it and reconcile its current native work before cleanup.", {})
        try:
            info = client.pane_process_info(binding["pane_id"])
        except HerdrError as exc:
            if error_code(exc) == "pane_not_found" and prior is not None:
                return
            raise
        if (info.get("pane_id") != origin["pane_id"] or info.get("shell_pid") != origin["shell_pid"]):
            raise UsageError("The pane no longer holds the original root shell; preserve this reused surface and inspect its owner.", {})
        require_empty_shell(client, binding["pane_id"], info)
        # The name can disappear while another runtime owns the same pane.
        if any(item.get("pane_id") == binding["pane_id"] for item in client.agent_list()):
            raise UsageError("Another agent owns the original pane; preserve it and resolve that work before cleanup.", {})
        visible = client.pane_read(binding["pane_id"], 2000)
        _draft(visible, prompt)
        digest = hashlib.sha256(strip_ansi(visible).encode("utf-8")).hexdigest()
        expected = prior["pane_sha256"] if prior is not None else hashlib.sha256(strip_ansi(body).encode("utf-8")).hexdigest()
        if digest != expected:
            raise UsageError("The pane changed from the saved bootstrap failure evidence; preserve the surface and inspect any intervening work before cleanup.", {})
        return digest

    pane_digest = observe()
    if prior is None:
        prior = {"schema_version": SCHEMA_VERSION, "dispatch": row["id"], "at": at,
                 "input": deepcopy(data), "evidence": evidence, "origin": deepcopy(origin),
                 "historical_status": row["status"], "pane_sha256": pane_digest, "closure": None}
        saved["outcomes"].append(prior)
        save_state(store_path(state_path), saved)
    if row["status"] in recovery.PENDING_STATUSES:
        recovery.reconcile(state["recovery"], state["assignments"],
                           {**{key: data[key] for key in required}, "outcome": "not_sent"}, at, None, missing_bootstrap=True)
        recovery.validate_store(state["recovery"], state["assignments"])
        save_state(state_path, state)
    closure = lifecycle.close(client, row["agent"], binding["pane_id"],
                              expected_shell=origin["shell_pid"], before_close=observe)
    if member["active"]:
        supervision.resolve(state_path, {"id": row["id"], "outcome": "Codex bootstrap not_sent: " + data["reason"],
                                        "evidence": [str(store_path(state_path)), evidence["path"]]}, at)
    prior["closure"] = closure
    save_state(store_path(state_path), saved)
    return {**prior, "outcome": "not_sent", "retry_authorized": False,
            "history_preserved": True}
