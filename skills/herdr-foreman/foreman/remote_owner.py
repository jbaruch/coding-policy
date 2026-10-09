"""Single-host remote foreman authority and durable effect fencing.

Only the trusted coding-policy controller constructs this owner. A transport
caller receives request/query methods, never the client or its routing config.
The controller lease is held across live identity checks and a single Herdr
invocation. All controllers of this deployment must share this owner store;
this is not a distributed lock or a multi-host scheduler.

An intent is persisted before any mutation. Losing the response leaves that
intent open across process restarts, blocking ALL subsequent mutations. Only
the policy foreman can reconcile it after observing the original identities.
No response, absent worker or elapsed timeout is proof of not-sent input.
Schema and trust boundary: ../references/remote-context.md.
"""

from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid
import re
import threading

from .command_context import (
    HerdrCommandContext, RemoteIndeterminateError, remote_command, remote_receipt, refuse,
)
from .state import save_state, state_lock, load_state_checked
from . import supervision, recovery


def loaded_policy(plugin_root):
    """Compute policy-byte identity; the trusted controller separately loads that policy."""
    root = Path(plugin_root).resolve()
    try:
        manifest = json.loads((root / ".tessl-plugin/plugin.json").read_text(encoding="utf-8"))
        files = sorted(path for directory in (root / "rules", root / "skills/herdr-foreman")
                       for path in directory.rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"})
        if not files or not any(path.parent == root / "rules" for path in files):
            raise refuse("loaded_policy_missing")
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(root)).encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return manifest["version"], digest.hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        raise refuse("loaded_policy_unreadable") from None


def _result(completed):
    if completed.returncode != 0:
        raise refuse("remote_identity_read_failed")
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise refuse("remote_identity_read_invalid") from None
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise refuse("remote_identity_read_invalid")
    return result


def live_identity(context, runner):
    """Authenticate the selected session and exact thin seat over saved-profile SSH."""
    receipt = remote_receipt(context)
    try:
        status = runner(context.argv("status", "server"))
        if status.returncode != 0:
            raise refuse("remote_server_not_ready")
        facts = {}
        for line in status.stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                if key in facts:
                    raise refuse("duplicate_remote_status_fact")
                facts[key] = value.strip()
        if (facts.get("status") != "running" or facts.get("endpoint_compatible") != "yes"
                or facts.get("socket") != "machine:{}/{}".format(
                    receipt.machine_profile_id, receipt.remote_session)):
            raise refuse("remote_session_changed")
        snapshot = _result(runner(context.argv("api", "snapshot"))).get("snapshot")
    except (OSError, subprocess.TimeoutExpired):
        raise refuse("remote_identity_transport_unavailable") from None
    if not isinstance(snapshot, dict):
        raise refuse("remote_snapshot_missing")
    workspaces, panes, agents = (snapshot.get(key) for key in ("workspaces", "panes", "agents"))
    if not isinstance(workspaces, list) or not isinstance(panes, list) or not isinstance(agents, list):
        raise refuse("remote_snapshot_invalid")
    if not all(all(isinstance(row, dict) for row in rows) for rows in (workspaces, panes, agents)):
        raise refuse("remote_snapshot_invalid")
    workspaces = [row for row in workspaces if row.get("workspace_id") == receipt.workspace_id]
    panes = [row for row in panes if row.get("pane_id") == receipt.foreman_pane_id]
    if (len(workspaces) != 1 or len(panes) != 1
            or panes[0].get("workspace_id") != receipt.workspace_id
            or panes[0].get("terminal_id") != receipt.foreman_terminal_id):
        raise refuse("remote_workspace_or_foreman_changed")
    # The workstation seat is a terminal anchor, not a competing LLM foreman.
    if any(row.get("pane_id") == receipt.foreman_pane_id for row in agents):
        raise refuse("remote_foreman_seat_has_another_brain")
    return snapshot


class RemoteForemanOwner:
    """Privileged owner; service transports expose only `task_request`/`task_query`."""

    def __init__(self, *, context, store_path, plugin_root, state_path=None):
        if not isinstance(context, HerdrCommandContext) or context.mode != "attested-remote":
            raise refuse("remote_owner_needs_attested_context")
        self.context = context
        self.path = Path(store_path)
        self.plugin_root = Path(plugin_root)
        self._thread_lock = threading.RLock()
        self.state_path = (Path(state_path) if state_path is not None else self.path.parent / "foreman-state.json").resolve()
        if not self.path.is_absolute():
            raise refuse("remote_owner_store_not_absolute")
        self._loaded_policy = loaded_policy(self.plugin_root)
        receipt = remote_receipt(context)
        if self._loaded_policy != (receipt.policy_version, receipt.policy_digest):
            raise refuse("loaded_policy_receipt_changed")

    def _private_store(self):
        try:
            directory = self.path.parent.lstat()
            if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                    or stat.S_IMODE(directory.st_mode) & 0o077):
                raise refuse("remote_owner_store_not_private")
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077):
                    raise refuse("remote_owner_record_not_private")
        except OSError:
            raise refuse("remote_owner_store_unavailable") from None

    @contextmanager
    def _lease(self):
        """Parallel observations share one controller lease, not competing OS handles."""
        with self._thread_lock:
            self._private_store()
            with state_lock(self.path):
                yield

    def _load(self):
        self._private_store()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise refuse("remote_owner_record_missing_or_unreadable") from None
        if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
                or document.get("schema_version") != 1
                or set(document) != {"schema_version", "attestation", "state_path", "owner_store_path", "pending", "reconciliations", "requests"}
                or document.get("attestation") != asdict(remote_receipt(self.context))
                or document.get("state_path") != str(self.state_path)
                or document.get("owner_store_path") != str(self.path.resolve())
                or not isinstance(document.get("reconciliations"), list)
                or not isinstance(document.get("requests"), list)
                or (document.get("pending") is not None and not isinstance(document["pending"], dict))):
            raise refuse("remote_owner_receipt_or_schema_changed")
        pending = document["pending"]
        if pending is not None and (set(pending) != {"schema_version", "id", "operation", "status", "argv_digest"}
                or type(pending.get("schema_version")) is not int or pending["schema_version"] != 1
                or pending.get("status") != "indeterminate" or not self._hex(pending.get("id"), 32)
                or not self._hex(pending.get("argv_digest"), 64)
                or not isinstance(pending.get("operation"), str) or not pending["operation"].strip()):
            raise refuse("remote_pending_intent_invalid")
        for row in document["reconciliations"]:
            if (not isinstance(row, dict) or set(row) != {"schema_version", "operation_id", "outcome", "evidence_digest"}
                    or type(row.get("schema_version")) is not int or row["schema_version"] != 1
                    or row.get("outcome") not in {"applied", "not_applied"}
                    or not self._hex(row.get("operation_id"), 32) or not self._hex(row.get("evidence_digest"), 64)):
                raise refuse("remote_reconciliation_record_invalid")
        requests, tasks = set(), set()
        for row in document["requests"]:
            if (not isinstance(row, dict) or set(row) != {"schema_version", "request_id", "task", "task_id", "status"}
                    or type(row.get("schema_version")) is not int or row["schema_version"] != 1
                    or row.get("status") != "queued" or not self._hex(row.get("task_id"), 32)
                    or not isinstance(row.get("request_id"), str) or not row["request_id"].strip()
                    or not isinstance(row.get("task"), str) or not row["task"].strip()
                    or row["request_id"] in requests or row["task_id"] in tasks):
                raise refuse("remote_task_request_record_invalid")
            requests.add(row["request_id"])
            tasks.add(row["task_id"])
        if loaded_policy(self.plugin_root) != self._loaded_policy:
            raise refuse("loaded_policy_changed_after_attachment")
        return document

    @staticmethod
    def _hex(value, length):
        return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{%d}" % length, value) is not None

    def initialize(self, runner, *, at):
        """Explicit trusted-controller bootstrap; never automatically replaces a lease."""
        self._private_store()
        with self._lease():
            live_identity(self.context, runner)
            if self.path.exists():
                self._load()
            else:
                # Reuse the supervision owner's cross-identity discovery guard
                # before writing anything that could erase an uncertain effect.
                if supervision.dispatch_binding(self.state_path, root=self.path.parent / "bindings") is not None:
                    raise refuse("remote_owner_record_lost_restore_original_intents_and_requests")
                save_state(self.path, {"schema_version": 1, "attestation": asdict(remote_receipt(self.context)),
                                      "state_path": str(self.state_path),
                                      "owner_store_path": str(self.path.resolve()),
                                      "pending": None, "reconciliations": [], "requests": []})
            existing = supervision.load(self.state_path)["binding"]
            expected = supervision.remote_identity(self.context.attestation, self.plugin_root,
                                                   owner_store_path=self.path)
            if existing is not None and existing["identity"] != expected:
                raise refuse("remote_supervision_binding_changed")
            supervision.bind_attested_remote(self.state_path, self.context.attestation, self.plugin_root, at,
                                               owner_store_path=self.path,
                                               root=self.path.parent / "bindings")

    def preflight(self, runner, *, allow_indeterminate=False):
        """First-class live round check; native HERDR_ENV and process probes stay local."""
        self._private_store()
        with self._lease():
            document = self._load()
            live_identity(self.context, runner)
            self._binding()
            if document["pending"] is not None and not allow_indeterminate:
                raise self._indeterminate(document["pending"])
            return {"ready": document["pending"] is None, "mode": "attested-remote",
                    "attestation": asdict(remote_receipt(self.context)), "state_path": str(self.state_path),
                    "pending_operation": document["pending"]["id"] if document["pending"] else None}

    def _binding(self):
        binding = supervision.load(self.state_path)["binding"]
        if (binding is None or binding["schema_version"] != 2
                or binding["identity"] != supervision.remote_identity(self.context.attestation, self.plugin_root,
                                                                      owner_store_path=self.path)):
            raise refuse("remote_supervision_binding_changed")

    def _indeterminate(self, pending):
        return RemoteIndeterminateError(
            "Remote effect {} has no certain outcome. Query the SAME remote identities and task "
            "evidence, then have the policy foreman record reconciliation through its remote owner API; do not start, "
            "prompt, send, close, retarget or replace it as a fresh mutation.".format(pending["id"]),
            {"operation_id": pending["id"], "outcome": "indeterminate", "operation": pending["operation"]})

    def invoke(self, context, command, mutating, runner):
        if context != self.context:
            raise refuse("owner_command_context_changed")
        _, effect = remote_command(context, context.argv(*command))
        if effect != mutating:
            raise refuse("remote_effect_classification_changed")
        operation = tuple(command[:2])
        foreman = remote_receipt(context).foreman_pane_id
        target = command[2] if len(command) > 2 else None
        if (operation in {("pane", "close"), ("pane", "send-text"), ("pane", "send-keys")}
                and target == foreman):
            raise refuse("remote_thin_foreman_seat_is_not_a_worker")
        if operation == ("agent", "start") and "--pane" in command:
            index = command.index("--pane") + 1
            if index < len(command) and command[index] == foreman:
                raise refuse("another_foreman_brain_forbidden")
        self._private_store()
        with self._lease():
            document = self._load()
            self._binding()
            if mutating and document["pending"] is not None:
                raise self._indeterminate(document["pending"])
            live_identity(context, runner)
            if not mutating:
                return runner(context.argv(*command))
            pending = {"schema_version": 1, "id": uuid.uuid4().hex,
                       "operation": " ".join(command[:2]), "status": "indeterminate",
                       "argv_digest": hashlib.sha256(json.dumps(command).encode("utf-8")).hexdigest()}
            document["pending"] = pending
            save_state(self.path, document)
            try:
                result = runner(context.argv(*command))
            except (OSError, subprocess.TimeoutExpired):
                raise self._indeterminate(pending) from None
            if result.returncode != 0:
                # A disconnected CLI can lose a successful server response. Even
                # structured failures remain uncertain here; no retry classifier
                # sees them as a pre-input provider refusal.
                raise self._indeterminate(pending)
            if tuple(command[:2]) not in {("pane", "send-text"), ("pane", "send-keys"), ("agent", "send-keys")}:
                try:
                    response = json.loads(result.stdout)
                except (ValueError, TypeError):
                    raise self._indeterminate(pending) from None
                if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
                    raise self._indeterminate(pending)
            document["pending"] = None
            save_state(self.path, document)
            return result

    def reconcile(self, operation_id, *, outcome, evidence, runner):
        """Trusted foreman decision after SAME-identity observation; no automatic replay."""
        if outcome not in {"applied", "not_applied"} or not isinstance(evidence, str) or not evidence.strip():
            raise refuse("remote_reconciliation_needs_observed_evidence")
        self._private_store()
        with self._lease():
            document = self._load()
            self._binding()
            pending = document["pending"]
            if pending is None or pending.get("id") != operation_id:
                raise refuse("remote_reconciliation_intent_changed")
            live_identity(self.context, runner)
            document["reconciliations"].append({"schema_version": 1, "operation_id": operation_id,
                                               "outcome": outcome,
                                               "evidence_digest": hashlib.sha256(evidence.encode("utf-8")).hexdigest()})
            document["pending"] = None
            save_state(self.path, document)

    def task_request(self, payload):
        """Untrusted caller ingress. No execution/model/prompt/routing options accepted."""
        if (not isinstance(payload, dict) or set(payload) != {"request_id", "task"}
                or any(not isinstance(payload[key], str) or not payload[key].strip()
                       for key in ("request_id", "task"))
                or len(payload["request_id"]) > 128 or len(payload["task"]) > 65536):
            raise refuse("task_request_fields_invalid")
        self._private_store()
        with self._lease():
            document = self._load()
            self._binding()
            for row in document["requests"]:
                if row["request_id"] == payload["request_id"]:
                    if row["task"] != payload["task"]:
                        raise refuse("task_request_identity_conflict")
                    return {"task_id": row["task_id"], "status": row["status"]}
            row = {"schema_version": 1, **payload, "task_id": uuid.uuid4().hex, "status": "queued"}
            document["requests"].append(row)
            save_state(self.path, document)
            return {"task_id": row["task_id"], "status": row["status"]}

    def task_query(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"task_id"} or not isinstance(payload["task_id"], str):
            raise refuse("task_query_fields_invalid")
        document = self._load()
        rows = [row for row in document["requests"] if row["task_id"] == payload["task_id"]]
        if len(rows) != 1:
            raise refuse("task_not_found")
        task_id = rows[0]["task_id"]
        status = rows[0]["status"]
        if self.state_path.exists():
            state, usable = load_state_checked(self.state_path, persist_migration=False)
            if not usable:
                raise refuse("remote_policy_task_state_unreadable")
            if task_id in state["recovery"]["tasks"]:
                status = ("closed" if recovery.task_closure(state["recovery"], state["assignments"], task_id)
                          else recovery.task_statuses(state["recovery"], state["assignments"])[task_id]["status"])
        return {"task_id": task_id, "status": status}

    def queued_tasks(self):
        """Privileged policy foreman reads task DATA; callers cannot replace its instructions."""
        return [{"task_id": row["task_id"], "task": row["task"]} for row in self._load()["requests"]
                if self.task_query({"task_id": row["task_id"]})["status"] == "queued"]
