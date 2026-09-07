"""Prove native display decoration from the completed assistant source.

The display allowlist is deliberately tiny: Codex's bullet and Grok's five
spaces. Neither is accepted without the same session's latest completed final
message ending in a bare marker. Rows are never joined. Source formats were
verified on Codex 0.153.2 and Grok 1.0.13 (Grok 4.6), with Herdr 0.8.2.
Unknown integrations, source formats and incomplete turns stay unconfirmed.

Live probes read only the session reported by Herdr. Owner recovery reads
archived evidence, adds a separate receipt, and changes no assignment, dispatch,
negative wait receipt, correction allowance or review verdict.
"""

import hashlib
import json
import os
import re
from fnmatch import fnmatchcase
from pathlib import Path

from . import recovery as ledger
from .errors import UsageError


DISPLAY_PREFIXES = {"codex": ("• ",), "grok": ("     ",)}
SESSION_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CONTAINER = re.compile(r"^ {0,3}(?:>|[-+*•][ \t]|[0-9]{1,9}[.)][ \t])")
RECOVERY_INPUTS = {"id", "dispatch", "report", "wait_receipt", "pane", "visible", "source"}
RECOVERY_ARTIFACTS = {"report", "wait_receipt", "pane", "visible", "source", "brief", "common"}


def bare_final(text, report):
    """The final source row is unindented and outside authored fenced code."""
    if not isinstance(text, str):
        return False
    rows = text.splitlines()
    if not rows or rows[-1] != "REPORT: " + report:
        return False
    fence, length, container = "", 0, False
    for row in rows[:-1]:
        match = FENCE.match(row)
        if match:
            run, tail = match.groups()
            if not fence:
                fence, length, container = run[0], len(run), False
            elif run[0] == fence and len(run) >= length and not tail.strip():
                fence = ""
            continue
        if fence:
            continue
        if not row.strip():
            container = False
        elif CONTAINER.match(row):
            container = True
    # Unmarked rows can be lazy paragraph continuations inside a list/quote.
    # A blank row ends that ambiguity for an unindented final marker.
    return not fence and not container


def decorated_row(visible, kind, report):
    """Return the complete observed row; arbitrary bullets/indentation fail."""
    expected = {prefix + "REPORT: " + report for prefix in DISPLAY_PREFIXES.get(kind, ())}
    for row in visible.splitlines():
        if row in expected:
            return row
        # Grok right-aligns a native clock after the message on wide panes.
        # The source proof still requires the bare path, with no clock text.
        if kind == "grok" and re.fullmatch(re.escape("     REPORT: " + report) + r" {2,}(?:1[0-2]|[1-9]):[0-5][0-9] [AP]M", row):
            return row
    return None


def _rows(body):
    try:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return None
    return rows if rows and all(isinstance(row, dict) for row in rows) else None


def codex_final(rows, session):
    """A final_answer plus matching task_complete in the latest started turn."""
    identities = [row.get("payload") for row in rows if row.get("type") == "session_meta"]
    if not identities or any(not isinstance(meta, dict) or meta.get("id") != session for meta in identities):
        return None
    turn, final, complete = None, None, False
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        kind = payload.get("type")
        if row.get("type") == "event_msg":
            if kind == "task_started":
                turn, final, complete = payload.get("turn_id"), None, False
            elif kind == "task_complete":
                complete = bool(turn and payload.get("turn_id") == turn and final
                                and payload.get("last_agent_message") == final)
            elif kind in ("turn_aborted", "error", "user_message"):
                final, complete = None, False
        elif row.get("type") == "response_item":
            if payload.get("role") == "user" or kind in ("function_call", "function_call_output"):
                final, complete = None, False
            elif payload.get("role") == "assistant":
                final, complete = None, False
                content = payload.get("content")
                if (payload.get("phase") == "final_answer" and isinstance(content, list)
                        and content and all(isinstance(item, dict) and item.get("type") == "output_text"
                                            and isinstance(item.get("text"), str) for item in content)):
                    final = "".join(item["text"] for item in content)
    return final if complete else None


def grok_final(rows, session):
    """Join native chunks within one message stream, never rendered rows."""
    final, prompt, stream, complete = "", None, None, False
    for row in rows:
        params = row.get("params")
        if not isinstance(params, dict) or params.get("sessionId") != session:
            return None
        update, meta = params.get("update"), params.get("_meta", {})
        if not isinstance(update, dict) or not isinstance(meta, dict):
            return None
        kind = update.get("sessionUpdate")
        if kind in ("user_message_chunk", "tool_call", "tool_call_update"):
            final, stream, complete = "", None, False
        elif kind == "hook_execution" and update.get("event_name") == "user_prompt_submit":
            final, stream, complete = "", None, False
        elif kind == "agent_message_chunk":
            content = update.get("content")
            if (not isinstance(content, dict) or content.get("type") != "text"
                    or not isinstance(content.get("text"), str) or not meta.get("promptId")
                    or not isinstance(meta.get("streamStartMs"), int)):
                return None
            current = (meta["promptId"], meta["streamStartMs"])
            if stream != current:
                final, stream = "", current
            prompt, complete = meta["promptId"], False
            final += content["text"]
        elif kind == "turn_completed":
            complete = bool(final and update.get("prompt_id") == prompt and update.get("stop_reason") == "end_turn")
        elif kind in ("turn_cancelled", "turn_failed", "error"):
            final, complete = "", False
    return final if complete else None


def source_final(body, kind, session):
    rows = _rows(body)
    if rows is None:
        return None
    parser = {"codex": codex_final, "grok": grok_final}.get(kind)
    return parser(rows, session) if parser else None


def source_prompt(body, kind):
    """Read the actual latest user message; quoted assistant instructions fail."""
    rows = _rows(body)
    if rows is None:
        return None
    prompt, in_chunks = None, False
    for row in rows:
        if kind == "codex":
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                return None
            if row.get("type") == "response_item" and payload.get("role") == "user":
                content = payload.get("content")
                if not isinstance(content, list) or not all(isinstance(item, dict) and item.get("type") == "input_text"
                        and isinstance(item.get("text"), str) for item in content):
                    return None
                prompt = "".join(item["text"] for item in content)
        else:
            params = row.get("params", {})
            update = params.get("update", {}) if isinstance(params, dict) else {}
            if not isinstance(update, dict):
                return None
            if update.get("sessionUpdate") == "user_message_chunk":
                content = update.get("content", {})
                if not isinstance(content, dict) or content.get("type") != "text" or not isinstance(content.get("text"), str):
                    return None
                prompt = (prompt or "") + content["text"] if in_chunks else content["text"]
                in_chunks = True
            else:
                in_chunks = False
    return prompt


def native_identity(info):
    ref = info.get("agent_session")
    if (not isinstance(ref, dict) or not isinstance(ref.get("agent"), str) or ref["agent"] not in DISPLAY_PREFIXES
            or ref.get("source") != "herdr:" + ref["agent"] or ref.get("kind") != "id"
            or not isinstance(ref.get("value"), str) or not SESSION_ID.fullmatch(ref["value"])):
        return None
    return {key: ref[key] for key in ("source", "agent", "kind", "value")}


def source_root(kind):
    if kind == "codex":
        return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    return Path.home() / ".grok" / "sessions"


def source_path(identity):
    """Resolve the official ID without glob's suppression of directory errors."""
    session, kind = identity["value"], identity["agent"]
    root, matches = source_root(kind), []
    depth_limit = 3 if kind == "codex" else 2

    def fail(error):
        raise error

    for directory, directories, files in os.walk(root, onerror=fail):
        path = Path(directory)
        depth = len(path.relative_to(root).parts)
        if depth == depth_limit:
            directories[:] = []
            if kind == "codex":
                matches.extend(path / name for name in files if fnmatchcase(name, "rollout-*-" + session + ".jsonl"))
            elif path.name == session and "updates.jsonl" in files:
                matches.append(path / "updates.jsonl")
        elif kind == "grok" and depth == 1:
            directories[:] = [name for name in directories if name == session]
    return matches[0] if len(matches) == 1 else None


def read_native_source(path):
    """Missing files are expected; unreadable or invalid source is a failure."""
    try:
        content = path.read_bytes()
        return content, content.decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read native transcript {}: {}. Restore readable UTF-8 transcript bytes and file permissions before retrying report verification.".format(path, exc), {}) from None


def pane_identity(pane, expected, identity):
    return (pane.get("pane_id") == expected and pane.get("agent_session") == identity
            and isinstance(pane.get("terminal_id"), str) and bool(pane["terminal_id"])
            and type(pane.get("revision")) is int and pane["revision"] >= 0
            and isinstance(pane.get("scroll"), dict) and pane["scroll"].get("offset_from_bottom") == 0
            and pane.get("agent_status") in ("idle", "done"))


def probe(client, agent, pane_id, report, visible, lines):
    """Read-only fallback for a candidate native row; no usable proof is false."""
    result = {"found": False, "reason": "native_marker_unconfirmed"}
    info = client.agent_get(agent)
    identity = native_identity(info)
    if (identity is None or info.get("pane_id") != pane_id
            or info.get("agent_status") not in ("idle", "done")
            or not decorated_row(visible, identity["agent"], report)):
        return result
    before = client.pane_get(pane_id)
    if not pane_identity(before, pane_id, identity):
        return result
    try:
        path = source_path(identity)
    except FileNotFoundError:
        return {**result, "reason": "native_source_unavailable"}
    except OSError as exc:
        raise UsageError("Cannot locate native transcript under {}: {}. Restore readable session directories and search permissions before retrying report verification.".format(source_root(identity["agent"]), exc), {}) from None
    if path is None:
        return {**result, "reason": "native_source_unavailable"}
    source = read_native_source(path)
    if source is None:
        return {**result, "reason": "native_source_unavailable"}
    source_bytes, body = source
    final = source_final(body, identity["agent"], identity["value"])
    if not bare_final(final, report) or not Path(report).is_file():
        return result
    after_visible = client.pane_read(pane_id, lines=lines).rstrip("\n")
    after = client.pane_get(pane_id)
    after_info = client.agent_get(agent)
    if (after_visible != visible or after != before or native_identity(after_info) != identity
            or after_info.get("pane_id") != pane_id or after_info.get("agent_status") not in ("idle", "done")):
        return result
    verified_source = read_native_source(path)
    if verified_source is None:
        return {**result, "reason": "native_source_unavailable"}
    if verified_source[0] != source_bytes or not Path(report).is_file():
        return result
    return {"found": True, "basis": "native_final_source", "native_session": identity,
            "source": {"path": str(path), "sha256": hashlib.sha256(source_bytes).hexdigest()}}


def _json(body, label):
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        raise UsageError("{} must preserve the original JSON evidence; restore its bytes.".format(label), {}) from None
    if not isinstance(value, dict):
        raise UsageError("{} must contain a JSON object.".format(label), {})
    return value


def recover(store, assignments, data, at):
    """Append delivery proof for an applied dispatch whose old wait was negative."""
    if not isinstance(data, dict) or set(data) != RECOVERY_INPUTS:
        raise UsageError("recover-report requires id, dispatch, report, wait_receipt, pane, visible and source; preserve the original evidence.", {})
    ledger.text(data["id"], "recovery id")
    dispatch = ledger._item(store["dispatches"], data["dispatch"], "dispatch")
    if dispatch["status"] != "applied":
        raise UsageError("Recover delivery only for a confirmed applied dispatch; reconcile uncertain transport first.", {})
    index = dispatch["assignment_index"]
    assignment = assignments[index]
    receipts, bodies = {}, {}
    for key in ("report", "wait_receipt", "pane", "visible", "source"):
        receipts[key], bodies[key] = ledger.receipt(data[key])
    receipts["brief"], brief = ledger.receipt(dispatch.get("brief"))
    receipts["common"], _common = ledger.receipt(dispatch.get("common"))
    if "REPORT: " + data["report"] not in brief.splitlines():
        raise UsageError("The original dispatch brief does not assign this report path; restore the matching brief and report.", {})
    negative = _json(bodies["wait_receipt"], "negative wait receipt")
    envelope = _json(bodies["pane"], "archived pane").get("result")
    if not isinstance(envelope, dict):
        raise UsageError("Archived pane JSON needs the original result object; restore the pane get evidence.", {})
    pane = envelope.get("pane")
    identity = native_identity(pane) if isinstance(pane, dict) else None
    original_identity = native_identity({"agent_session": assignment.get("context_session")})
    if assignment.get("context_session") is not None and original_identity is None:
        raise UsageError("The original native-session proof uses an unsupported shape; preserve it and update the evidence adapter before recovery.", {})
    # Old nondeveloper dispatches deliberately recorded null continuity. Bind
    # their actual native user prompt to the saved dispatch instead of filling
    # that historical null with a later observation.
    from .assign import assignment_text, tiered_prompt
    prompt = assignment_text(dispatch["role"], dispatch["common"], dispatch["brief"])
    tier = dispatch["result"].get("tier")
    if tier is not None:
        prompt, prompt_hash = tiered_prompt(prompt, tier, dispatch["common"], dispatch["brief"])
        if tier.get("prompt_hash") != prompt_hash:
            raise UsageError("Original briefing bytes differ from the dispatch's recorded prompt hash; restore them before recovery.", {})
    if (negative.get("found") is not False or negative.get("agent") != dispatch["agent"]
            or negative.get("report_path") != data["report"] or negative.get("state") not in ("idle", "done")
            or "marker unconfirmed" not in str(negative.get("reason", ""))):
        raise UsageError("Recovery requires this completed dispatch's original negative marker-unconfirmed wait receipt; refusals are not delivery.", {})
    if (identity is None or original_identity is not None and identity != original_identity
            or not isinstance(pane, dict) or not pane_identity(pane, dispatch["result"].get("pane_id"), identity)
            or not decorated_row(bodies["visible"], identity["agent"], data["report"])
            or source_prompt(bodies["source"], identity["agent"]) != prompt
            or not bare_final(source_final(bodies["source"], identity["agent"], identity["value"]), data["report"])):
        raise UsageError("Archived pane and completed native source do not prove this report's bare final marker; preserve the negative receipt.", {})
    prior = next((row for row in store["delivery_recoveries"] if row["id"] == data["id"]), None)
    if prior:
        if prior["input"] != data or prior["receipts"] != receipts:
            raise UsageError("Recovery identity names different evidence; preserve the original receipt and resolve the conflict.", {})
        return prior
    if any(row["dispatch"] == data["dispatch"] for row in store["delivery_recoveries"]):
        raise UsageError("This dispatch already has delivery recovery; reuse its original recovery identity.", {})
    record = {"schema_version": 1, "id": data["id"], "at": at, "task": dispatch["task"],
              "dispatch": data["dispatch"], "assignment_index": index, "input": data,
              "receipts": receipts, "native_session": identity, "found": True,
              "basis": "archived_native_final_source", "native_session_proof": None,
              "grants_review_approval": False}
    store["delivery_recoveries"].append(record)
    ledger._event(store, at, "report_delivery_recovered", dispatch["task"], {"recovery": data["id"], "dispatch": data["dispatch"]})
    return record


def validate_recoveries(store, assignments):
    dispatches = set()
    for row in store["delivery_recoveries"]:
        dispatch = ledger._item(store["dispatches"], row["dispatch"], "dispatch")
        index = row["assignment_index"]
        if (dispatch["status"] != "applied" or index != dispatch["assignment_index"]
                or row["task"] != dispatch["task"] or row["found"] is not True
                or row["grants_review_approval"] is not False or row["basis"] != "archived_native_final_source"
                or row["native_session_proof"] is not None
                or native_identity({"agent_session": row["native_session"]}) is None
                or assignments[index].get("context_session") is not None and
                row["native_session"] != native_identity({"agent_session": assignments[index]["context_session"]})):
            raise UsageError("Delivery recovery differs from its preserved dispatch; restore the owner-written ledger.", {})
        if (not isinstance(row["input"], dict) or set(row["input"]) != RECOVERY_INPUTS
                or row["input"]["id"] != row["id"] or row["input"]["dispatch"] != row["dispatch"]
                or not isinstance(row["receipts"], dict) or set(row["receipts"]) != RECOVERY_ARTIFACTS
                or row["dispatch"] in dispatches):
            raise UsageError("Delivery recovery must preserve its unique dispatch and complete evidence receipts.", {})
        dispatches.add(row["dispatch"])
        for receipt in row["receipts"].values():
            ledger.validate_receipt(receipt)
        for key in RECOVERY_ARTIFACTS:
            path = dispatch.get(key) if key in ("common", "brief") else row["input"][key]
            if row["receipts"][key]["path"] != path:
                raise UsageError("Delivery recovery artifact paths differ from their original inputs; restore its owner-written record.", {})
