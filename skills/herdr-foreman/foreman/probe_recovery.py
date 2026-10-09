"""Owned retained disposable probes; schema/commands: references/probe-recovery.md.

The CLI holds the selected state's transaction lock through measure/resolve.
First-start pre-input dialogs and failed owned cleanup create gated rows. Resolution sends
no keys, rechecks original native/process/tier evidence, and records closure
after the actual pane closes. Unknown/corrupt gate documents refuse work.
"""

import hashlib
import json
import re
import shlex
from pathlib import Path

from . import runnable
from .composer import ensure_ready
from .config import assignment_worker, default_config_path
from .errors import ConfigError, HerdrError, StateError, UsageError, owner_recovery
from .herdr import READY_STATES, error_code
from .launch import require_empty_shell, verify_running
from .state import save_state
from .supervision import read_json, timestamp
from .tiers import parse_tiers

SCHEMA_VERSION = 2


def config_digest(template):
    value = {**template.as_dict(), "window_group": template.window_group}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def store_path(state_path):
    return Path(str(Path(state_path).expanduser().resolve()) + ".probes.json")


def load(state_path):
    path = store_path(state_path)
    data = read_json(path)
    if data is None:
        return {"schema_version": SCHEMA_VERSION, "records": []}
    if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 1:
        _validate(data, path, version=1)
        data = {**data, "schema_version": SCHEMA_VERSION, "records": [
            {**row, "schema_version": SCHEMA_VERSION, "phase": "startup"} for row in data["records"]]}
        _validate(data, path)
        save_state(path, data)
    _validate(data, path)
    return data


def _validate(data, path, *, version=SCHEMA_VERSION):
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != version
            or not isinstance(data.get("records"), list)):
        raise StateError("Unsupported probe gate {}. Preserve it and restore supported owner evidence before measuring or resolving probes.".format(path), {})
    names = set()
    for row in data["records"]:
        if (not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] != version
                or version == SCHEMA_VERSION and row.get("phase") not in {"startup", "cleanup"}
                or row.get("status") not in {"pending", "closed"}
                or any(not isinstance(row.get(key), str) or not row[key].strip() for key in
                       ("agent", "pane_id", "worker_kind", "kind", "at", "config_path"))
                or not isinstance(row.get("window_group"), str)
                or not isinstance(row.get("tier"), dict)
                or not isinstance(row.get("config_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["config_sha256"])
                or "native" not in row or row["native"] is not None and not isinstance(row["native"], dict)
                or not isinstance(row.get("process"), dict)
                or row["process"].get("source") != "process_argv"
                or type(row["process"].get("pid")) is not int or row["process"]["pid"] <= 0
                or row["process"].get("pane_id") != row["pane_id"]
                or "closure" not in row
                or row["status"] == "pending" and row["closure"] is not None
                or row["status"] == "closed" and (not isinstance(row["closure"], dict) or row["closure"].get("closed") is not True)
                or row["agent"] in names):
            raise StateError("Malformed probe gate {}. Preserve it and restore the original owner record; do not discard its retained surfaces.".format(path), {})
        timestamp(row["at"])
        try:
            parse_tiers({"coordination": row["tier"]}, row["kind"], no_effort_rows=frozenset({"coordination"}))
        except ConfigError as exc:
            raise StateError("Malformed original tier in probe gate {}. Preserve the retained pane and restore the original owner evidence before measuring or resolving.".format(path), {}) from exc
        native = row["native"]
        if native is not None and (native.get("source") != "herdr:" + row["kind"] or native.get("agent") != row["kind"]
                or native.get("kind") not in {"id", "path"} or not isinstance(native.get("value"), str) or not native["value"].strip()):
            raise StateError("Malformed original native-session observation in probe gate {}. Preserve it and restore actual owner evidence.".format(path), {})
        names.add(row["agent"])


def pending(state_path, template):
    return next((row for row in load(state_path)["records"] if row["status"] == "pending"
        and (row["worker_kind"] == template.name or template.window_group and row["window_group"] == template.window_group)), None)


def _operation(state_path, row, herdr_bin=None):
    operation = "resolve-probe --state " + shlex.quote(str(state_path)) + " --config " + shlex.quote(row["config_path"]) + " --agent " + shlex.quote(row["agent"])
    if isinstance(herdr_bin, str) and herdr_bin:
        operation += " --herdr-bin " + shlex.quote(herdr_bin)
    return runnable.command(operation)


def diagnostic(state_path, row, *, herdr_bin=None):
    cleanup = row["phase"] == "cleanup"
    error = HerdrError(("Disposable probe {} in {} remains retained after unproved cleanup."
        if cleanup else "Fresh startup probe {} in {} remains retained; no usage command was sent.").format(row["agent"], row["pane_id"]), {})
    return owner_recovery(error, "probe_cleanup_unproved" if cleanup else "startup_dialog_pending",
        _operation(state_path, row, herdr_bin),
        "Read the retained native pane and follow Runtime Dialogs under existing task authority. After the native surface clears, the named owner command proves the original target and empty composer before cleanup. Repeat normal measure only after that resolution.")


def retain(state_path, template, probe, pane, tier, observation, at, *, config_path=None, phase="startup"):
    if state_path is None or not isinstance(observation, tuple) or len(observation) != 3:
        raise UsageError("Retaining a startup probe requires the normal measure owner's state and original native/process evidence. Repeat through the public measure command.", {})
    if observation[0] != pane:
        raise HerdrError("Probe moved before reservation; preserve its actual identity and inspect the native pane.", {})
    data = load(state_path)
    row = {"schema_version": SCHEMA_VERSION, "at": at, "status": "pending", "phase": phase,
        "agent": probe.name, "pane_id": pane, "worker_kind": template.name, "kind": probe.kind,
        "config_path": str(Path(config_path or default_config_path()).expanduser().resolve()),
        "config_sha256": config_digest(template),
        "window_group": template.window_group or "", "tier": tier,
        "native": observation[1], "process": observation[2], "closure": None}
    data["records"].append(row)
    _validate(data, store_path(state_path))
    save_state(store_path(state_path), data)
    return row


def resolve(state_path, name, templates, client, *, config_path=None):
    from . import lifecycle
    data = load(state_path)
    row = next((item for item in data["records"] if item["agent"] == name), None)
    if row is None:
        raise UsageError("No owner-recorded startup probe names {!r}; inspect the original measure receipt, not an assignment pane.".format(name), {})
    if row["status"] == "closed":
        return {**row, "replayed": True}
    if config_path is not None and Path(config_path).expanduser().resolve() != Path(row["config_path"]).expanduser().resolve():
        raise UsageError("Use the retained probe's original --config {} before resolving it; no pane was closed.".format(row["config_path"]), {})
    template = next((worker for worker in templates if worker.name == row["worker_kind"] and worker.kind == row["kind"] and worker.assignment_scoped), None)
    if template is None:
        raise UsageError("Restore the retained probe's original worker-kind config before resolving it; the pane is preserved.", {})
    if config_digest(template) != row["config_sha256"]:
        raise UsageError("Retained probe's worker config changed. Restore its original launch/composer configuration before resolving; no native input or closure occurred.", {})
    worker = assignment_worker(template, name)
    def observe():
        try:
            live = client.agent_get(name)
        except HerdrError as exc:
            if error_code(exc) != "agent_not_found":
                raise
            try:
                client.pane_get(row["pane_id"])
            except HerdrError as pane_error:
                if error_code(pane_error) != "pane_not_found":
                    raise
            else:
                require_empty_shell(client, row["pane_id"])
            return None
        process = verify_running(client, worker, row["pane_id"], row["tier"])
        if (live.get("pane_id") != row["pane_id"] or live.get("agent") != row["kind"]
                or live.get("name") != name or live.get("agent_session") != row["native"]
                or process != row["process"] or live.get("agent_status") not in READY_STATES):
            raise HerdrError("Retained probe's original native/process/tier/readiness proof differs; preserve it and resolve only its actual startup dialog.", {})
        return (row["pane_id"], row["native"], process)
    try:
        if observe() is not None:
            ensure_ready(client, worker, row["pane_id"], startup_observe=observe)
        closure = lifecycle.close(client, name, row["pane_id"], before_close=observe)
    except (HerdrError, UsageError) as exc:
        raise owner_recovery(exc, "probe_cleanup_unproved",
            _operation(state_path, row, getattr(client, "binary", None)),
            "Read the actual retained native pane. Preserve any draft, changed identity/tier, working or unresolved dialog; repeat this guarded owner operation only after the original empty target is proved.") from exc
    row.update(status="closed", closure=closure)
    save_state(store_path(state_path), data)
    return row
