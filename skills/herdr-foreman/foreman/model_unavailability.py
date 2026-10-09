"""Native terminal model/account failures, separate from model capability.

Source contracts verified on Codex 0.160.1 and Claude Code 2.1.295 (#724).
Renew the supported versions and source fixtures quarterly and when a runtime
changes this format. Unknown runtime formats and providers stay unconfirmed;
the legacy provider-refusal path remains independent. No rendered sentence or
assistant quotation supplies authority. These are unavailable attempts, never
delivered reports or permission to change an operator's model pin.
"""

import hashlib
import json
import re
import time
from pathlib import Path

from . import claude_native, composer, report_delivery
from .errors import UsageError
from .chronology import timestamp
from .capabilities import INTERVAL


SUPPORTED_VERSIONS = {"codex": frozenset({"0.160.1"}), "claude": frozenset({"2.1.295"})}
CONFIRM_SECONDS = 5
SOURCE_MAX_BYTES = 32 * 1024 * 1024
MODEL_ID = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}"
# Exact native error contracts, not a classifier of arbitrary prose.
CODEX_UNSUPPORTED = re.compile(
    r"The '(" + MODEL_ID + r")' model is not supported when using Codex with a ChatGPT account\.\Z")
CLAUDE_NOT_FOUND = re.compile(
    r"There's an issue with the selected model \((" + MODEL_ID + r")\)\. "
    r"It may not exist or you may not have access to it\. Run /model to pick a different model\.\Z")
CODEX_CONTEXT_KINDS = (
    ["agents_md.instructions", "environments.environment_context"],
    ["agents_md.instructions"], ["environments.environment_context"],
)
CODEX_DEVELOPER_KINDS = (
    ["host_skills.instructions", "permissions.instructions", "collaboration_mode.instructions"],
    ["hooks.additional_context"],
)
PROOF_FIELDS = frozenset({"schema_version", "kind", "model", "code", "account_scope", "cli_version",
    "prompt_sha256", "native_session", "pane_id", "terminal_id", "revision", "source"})


def launch_scope(agent, worker_kind=None):
    """Freeze operator-declared launch/account grouping, not authenticated ID."""
    return {"schema_version": 1, "kind": agent.kind, "window_group": agent.window_group,
            "worker_kind": worker_kind or agent.name}


def validate_scope(scope):
    if (not isinstance(scope, dict) or set(scope) != {"schema_version", "kind", "window_group", "worker_kind"}
            or type(scope["schema_version"]) is not int or scope["schema_version"] != 1
            or not isinstance(scope["kind"], str) or scope["kind"] not in {"codex", "claude", "grok"}
            or not isinstance(scope["window_group"], str)
            or not isinstance(scope["worker_kind"], str) or not scope["worker_kind"].strip()):
        raise UsageError("Unsupported original launch scope; restore its owner-recorded configured account/worker binding, never today's config.", {})


def validate_proof(proof):
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS
            or type(proof["schema_version"]) is not int or proof["schema_version"] != 1
            or not isinstance(proof["kind"], str) or proof["kind"] not in SUPPORTED_VERSIONS
            or not isinstance(proof["cli_version"], str) or proof["cli_version"] not in SUPPORTED_VERSIONS[proof["kind"]]
            or not isinstance(proof["model"], str) or re.fullmatch(MODEL_ID, proof["model"]) is None
            or (proof["code"], proof["account_scope"]) != {
                "codex": ("model_account_unsupported", "chatgpt"), "claude": ("model_not_found", "unknown")}[proof["kind"]]
            or not isinstance(proof["prompt_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", proof["prompt_sha256"]) is None
            or not isinstance(proof["native_session"], dict)
            or report_delivery.native_identity({"agent_session": proof["native_session"]}) is None
            or report_delivery.native_identity({"agent_session": proof["native_session"]}) != proof["native_session"]
            or proof["native_session"]["agent"] != proof["kind"]
            or any(not isinstance(proof[key], str) or not proof[key].strip() for key in ("pane_id", "terminal_id"))
            or type(proof["revision"]) is not int or proof["revision"] < 0):
        raise UsageError("Native unavailability needs the complete supported source/session/viewport proof; preserve the original wait receipt.", {})
    source = proof["source"]
    if (not isinstance(source, dict) or set(source) != {"schema_version", "path", "sha256", "size"}
            or type(source["schema_version"]) is not int or source["schema_version"] != 1
            or not isinstance(source["path"], str) or not Path(source["path"]).is_absolute()
            or not isinstance(source["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
            or type(source["size"]) is not int or not 0 < source["size"] <= SOURCE_MAX_BYTES):
        raise UsageError("Native unavailability needs an absolute, size/digest-bound source receipt; restore the original evidence.", {})


def verify_refusal(proof, dispatch, binding, provider, report):
    """First recording verifies immutable dispatch inputs and actual source bytes."""
    from .assign import assignment_text, tiered_prompt
    from .recovery import receipt
    validate_proof(proof)
    tier = (dispatch.get("result") or {}).get("tier")
    expected_native = binding.get("native_session") if isinstance(binding, dict) else None
    expected_native = report_delivery.native_identity({"agent_session": expected_native}) if expected_native is not None else None
    if (not isinstance(binding, dict) or binding.get("pane_id") != proof["pane_id"]
            or binding.get("native_session") is not None and expected_native != proof["native_session"]
            or provider != proof["kind"] or not isinstance(tier, dict) or tier.get("model") != proof["model"]):
        raise UsageError("Native model refusal differs from this dispatch's original model, provider, pane or enrolled session; preserve both attempts.", {})
    _brief_receipt, brief = receipt(dispatch.get("brief"))
    _common_receipt, common = receipt(dispatch.get("common"))
    if "REPORT: " + report not in brief.splitlines():
        raise UsageError("Original brief does not bind this refusal's report; restore the dispatched bytes.", {})
    contents = {dispatch["brief"]: brief.encode("utf-8"), dispatch["common"]: common.encode("utf-8")}
    prompt, prompt_hash = tiered_prompt(assignment_text(dispatch["role"], dispatch["common"], dispatch["brief"]),
                                      tier, dispatch["common"], dispatch["brief"], contents)
    if (tier.get("prompt_hash") != prompt_hash
            or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != proof["prompt_sha256"]):
        raise UsageError("Native refusal does not match the original sent prompt and briefing bytes; restore the original dispatch evidence.", {})
    try:
        path = report_delivery.source_path(proof["native_session"])
    except OSError as exc:
        raise UsageError("Cannot locate the refused native source: {}. Restore directory search access before recording.".format(exc), {}) from None
    if path is None or str(path) != proof["source"]["path"]:
        raise UsageError("Refusal source is not this session's unique official native log; preserve the receipt and restore its source.", {})
    source = _source(path)
    if (source is None or len(source[0]) != proof["source"]["size"]
            or hashlib.sha256(source[0]).hexdigest() != proof["source"]["sha256"]
            or source_error(source[1], proof["kind"], proof["native_session"]["value"]) != {
                key: proof[key] for key in ("kind", "model", "code", "account_scope", "cli_version", "prompt_sha256")}):
        raise UsageError("Native refusal source changed or does not prove this model error; save a fresh real wait receipt before recording.", {})


def exclusion(store, agent, model, at, worker_kind=None):
    """A fresh model negative in the original configured launch/account scope.

    Blank account grouping binds only the original worker template. Historical
    dispatches lacking scope never acquire one from current configuration.
    Account grouping is operator-declared; it is not an authenticated account ID.
    """
    current = launch_scope(agent, worker_kind)
    for dispatch in reversed((store or {}).get("dispatches", [])):
        refusal = dispatch.get("refusal")
        if not isinstance(refusal, dict) or refusal.get("schema_version") != 2:
            continue
        scope, proof = refusal["launch_scope"], refusal["unavailability"]
        if scope is None or proof["model"] != model or scope["kind"] != current["kind"]:
            continue
        if scope["window_group"]:
            if scope["window_group"] != current["window_group"]:
                continue
        elif (scope["window_group"], scope["worker_kind"]) != (current["window_group"], current["worker_kind"]):
            continue
        age = timestamp(at, "Availability checkpoint") - timestamp(refusal["at"], "Native refusal")
        if age.total_seconds() < 0 or age >= INTERVAL:
            continue
        return {"dispatch": dispatch["id"], "model": model, "launch_scope": scope, "receipt": refusal["evidence"]}
    return None


def require_available(store, agent, model, at, worker_kind=None):
    refused = exclusion(store, agent, model, at, worker_kind)
    if refused is not None:
        raise UsageError("Selected model {} is unavailable in {}'s original configured launch/account scope; replan another eligible pair/provider, never substitute a judge pin.".format(model, agent.name),
                         {"model_unavailability": refused})


def _text_blocks(value, kind):
    if (not isinstance(value, list) or not value
            or not all(isinstance(block, dict) and block.get("type") == kind
                       and isinstance(block.get("text"), str) for block in value)):
        return None
    return "".join(block["text"] for block in value)


def _facts(kind, model, code, scope, version, prompt):
    return {"kind": kind, "model": model, "code": code, "account_scope": scope,
            "cli_version": version, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}


def _codex_error(error, model):
    if (not isinstance(error, dict) or error.get("codex_error_info") != "other"
            or not isinstance(error.get("message"), str)):
        return False
    try:
        body = json.loads(error["message"])
    except json.JSONDecodeError:
        return False
    if (not isinstance(body, dict) or body.get("type") != "error"
            or type(body.get("status")) is not int or body["status"] != 400):
        return False
    detail = body.get("error")
    if (not isinstance(detail, dict) or detail.get("type") != "invalid_request_error"
            or not isinstance(detail.get("message"), str)):
        return False
    match = CODEX_UNSUPPORTED.fullmatch(detail["message"])
    return match is not None and match[1] == model


def codex_error(rows, session):
    """The latest started turn completed with the exact native account error.

    Only metadata-proven user.text supplies the prompt. Native instruction and
    environment records are not user input. A later turn, tool, assistant
    message, abort or contradictory identity invalidates the error evidence.
    """
    metas = [row.get("payload") for row in rows if row.get("type") == "session_meta"]
    if (not metas or any(not isinstance(meta, dict) or meta.get("id") != session
                        or meta.get("originator") != "codex-tui" or meta.get("model_provider") != "openai"
                        or not isinstance(meta.get("cli_version"), str)
                        or meta["cli_version"] not in SUPPORTED_VERSIONS["codex"] for meta in metas)):
        return None
    versions = {meta["cli_version"] for meta in metas}
    if len(versions) != 1:
        return None
    version = versions.pop()
    turn, model, prompt, complete, usable, completed_turn = None, None, None, False, False, False
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        kind = row.get("type")
        if kind == "session_meta":
            continue
        if kind == "world_state":
            # Native snapshot bookkeeping cannot speak as user or assistant.
            if set(payload) != {"full", "state"}:
                return None
            continue
        if kind == "turn_context":
            if completed_turn or payload.get("turn_id") != turn or not isinstance(payload.get("model"), str):
                usable = False
            elif model is not None and payload["model"] != model:
                usable = False
            else:
                model = payload["model"]
        elif kind == "event_msg":
            event = payload.get("type")
            if event == "task_started":
                turn = payload.get("turn_id")
                model, prompt, complete = None, None, False
                completed_turn = False
                usable = isinstance(turn, str) and bool(turn)
            elif event == "task_complete":
                if completed_turn:
                    return None
                completed_turn = True
                complete = bool(usable and model and prompt and payload.get("turn_id") == turn
                                and payload.get("last_agent_message") is None
                                and _codex_error(payload.get("error"), model))
            elif event in ("turn_aborted", "error"):
                usable, complete = False, False
            elif event == "user_message" and (completed_turn or payload.get("message") != prompt):
                usable, complete = False, False
            elif event == "item_completed":
                item = payload.get("item")
                if (complete or payload.get("thread_id") != session or payload.get("turn_id") != turn
                        or not isinstance(item, dict) or item.get("type") != "UserMessage"):
                    usable, complete = False, False
            elif event not in ("user_message",):
                return None
        elif kind == "response_item":
            if complete or payload.get("role") not in ("user", "developer") or payload.get("type") != "message":
                usable, complete = False, False
                continue
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            if not isinstance(metadata, dict) or metadata.get("turn_id") != turn:
                usable = False
                continue
            kinds = metadata.get("content_item_kinds")
            if payload["role"] == "developer":
                if kinds not in CODEX_DEVELOPER_KINDS:
                    usable = False
                continue
            if kinds in CODEX_CONTEXT_KINDS:
                continue
            if kinds != ["user.text"]:
                usable = False
                continue
            prompt = _text_blocks(payload.get("content"), "input_text")
            if not prompt:
                usable = False
        else:
            return None
    return _facts("codex", model, "model_account_unsupported", "chatgpt", version, prompt) if complete and usable else None


def claude_error(rows, session):
    """The latest main-chain assistant row is Claude's synthetic API error."""
    chain = claude_native.main_chain(rows, session)
    if not chain:
        return None
    turns = [row for row in chain if row.get("type") in claude_native.TURN_TYPES]
    if not turns or turns[-1].get("type") != "assistant":
        return None
    row, prompt = turns[-1], claude_native.prompt_text(rows, session)
    version = row.get("version")
    if (not prompt or not isinstance(version, str) or version not in SUPPORTED_VERSIONS["claude"]
            or row.get("isApiErrorMessage") is not True or row.get("error") != "model_not_found"):
        return None
    body = row["message"]
    if body.get("model") != "<synthetic>" or body.get("stop_reason") != "stop_sequence":
        return None
    usage = body.get("usage")
    if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] != 0
            for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))):
        return None
    text = _text_blocks(body.get("content"), "text")
    match = CLAUDE_NOT_FOUND.fullmatch(text) if text is not None else None
    return _facts("claude", match[1], "model_not_found", "unknown", version, prompt) if match else None


def source_error(body, kind, session):
    """Safe, minimal terminal facts from this native session, or no evidence."""
    if not isinstance(body, str):
        return None
    try:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return None
    if not rows or not all(isinstance(row, dict) for row in rows):
        return None
    parser = {"codex": codex_error, "claude": claude_error}.get(kind) if isinstance(kind, str) else None
    return parser(rows, session) if parser else None


def _empty_composer(text, kind):
    glyph = {"codex": "› ", "claude": "❯ "}.get(kind)
    if glyph is None:
        return False
    literal = composer.composer_text(text, glyph, ignore_dim=False)
    if literal is None:
        return False
    if not literal:
        return True
    # Plain text cannot distinguish a recalled/typed placeholder from a hint.
    return (kind == "codex" and literal == "Ask Codex to do anything"
            and not composer.composer_text(text, glyph, ignore_dim=True))


def _source(path):
    try:
        with path.open("rb") as stream:
            body = stream.read(SOURCE_MAX_BYTES + 1)
        if len(body) > SOURCE_MAX_BYTES:
            return None
        return body, body.decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read native error source {}: {}. Restore its readable UTF-8 bytes and permissions before deciding this attempt's outcome.".format(path, exc), {}) from None


def probe(client, agent, pane_id, report, visible, lines, *, sleep=time.sleep, confirm_seconds=None):
    """Read-only, same-session/live-bottom proof; never terminal input.

    Unknown evidence returns {confirmed: false, reason}. Success adds an
    `unavailability` record (schema 1): source-error facts, native_session,
    pane_id, terminal_id, revision and a digest-bound source receipt. It is
    not delivery, capability evidence or authority to replace a model pin.
    Every source, viewport and identity observation survives a separate read.
    """
    unknown = {"confirmed": False, "reason": "native_model_error_unconfirmed"}
    if Path(report).exists():
        return unknown
    info = client.agent_get(agent)
    identity = report_delivery.native_identity(info)
    if (identity is None or identity["agent"] not in SUPPORTED_VERSIONS
            or info.get("pane_id") != pane_id or info.get("agent_status") not in ("idle", "done")):
        return unknown
    before = client.pane_get(pane_id)
    if not report_delivery.pane_identity(before, pane_id, identity):
        return unknown
    try:
        path = report_delivery.source_path(identity)
    except OSError as exc:
        raise UsageError("Cannot locate this native session's source: {}. Restore source-directory search permission before deciding model unavailability.".format(exc), {}) from None
    if path is None:
        return {**unknown, "reason": "native_source_unavailable"}
    source = _source(path)
    if source is None:
        return {**unknown, "reason": "native_source_unavailable"}
    raw, body = source
    facts = source_error(body, identity["agent"], identity["value"])
    if facts is None:
        return unknown
    ansi = client.agent_read(agent, source="visible", lines=lines, fmt="ansi")
    if not _empty_composer(ansi, identity["agent"]):
        return unknown
    sleep(CONFIRM_SECONDS if confirm_seconds is None else confirm_seconds)
    after_visible = client.pane_read(pane_id, lines=lines).rstrip("\n")
    after_ansi = client.agent_read(agent, source="visible", lines=lines, fmt="ansi")
    after = client.pane_get(pane_id)
    after_info = client.agent_get(agent)
    if (after != before or after_visible != visible or after_ansi != ansi
            or report_delivery.native_identity(after_info) != identity or after_info.get("pane_id") != pane_id
            or after_info.get("agent_status") not in ("idle", "done") or Path(report).exists()):
        return unknown
    verified = _source(path)
    if verified is None or verified[0] != raw:
        return unknown
    return {"confirmed": True, "unavailability": {"schema_version": 1, **facts,
        "native_session": identity, "pane_id": pane_id, "terminal_id": before["terminal_id"],
        "revision": before["revision"], "source": {"schema_version": 1, "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}}
