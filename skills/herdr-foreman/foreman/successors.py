"""Provisional operating placements, separate from measured qualification (#700).

The capability owner writes these records before an authorized config upgrade.
Provider provenance establishes identity, not adequacy. Routing only reads an
exact worker/role/round/row binding and still checks all three live fact owners.
The existing maintenance report keeps, revises or withdraws a placement; due
alone never stops delivery. No scheduler or per-model test campaign lives here.
"""

import copy
import hashlib
import json
from datetime import timedelta
from html.parser import HTMLParser
from typing import NoReturn
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import capabilities, runnable
from .chronology import timestamp
from .errors import UsageError
from .state import save_state, state_lock
from .tiers import JUDGMENT_ROUNDS, ROLE_ROUNDS, canonical_role

SCHEMA_VERSION = 1
PROVIDERS = {
    "anthropic": ("claude", frozenset({"anthropic.com", "www.anthropic.com", "docs.anthropic.com", "platform.claude.com"})),
    "openai": ("codex", frozenset({"openai.com", "www.openai.com", "platform.openai.com", "developers.openai.com"})),
    "xai": ("grok", frozenset({"x.ai", "www.x.ai", "docs.x.ai"})),
}
BODY_LIMIT = 1024 * 1024


def fail(message) -> NoReturn:
    raise UsageError("Successor placement: " + message, {})


def fields(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        fail("{} carries exactly {}.".format(label, ", ".join(sorted(required))))


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        fail(label + " needs nonempty text.")
    return value


def provider_url(ref, provider):
    text(ref, "Provider reference")
    try:
        url = urlsplit(ref)
        valid = (provider in PROVIDERS and url.scheme == "https" and url.hostname in PROVIDERS[provider][1]
                 and url.port in (None, 443) and not url.username and not url.password and not url.fragment and not url.query)
    except ValueError:
        valid = False
    if not valid:
        fail("cite an HTTPS catalog/publication on this provider's trusted host, without credentials or redirects to another host.")


class ProviderRedirect(HTTPRedirectHandler):
    def __init__(self, provider):
        self.provider = provider

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        provider_url(newurl, self.provider)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_provider(ref, provider):
    """Bounded HTTPS read. Caller validates the exact source quote, not its meaning."""
    provider_url(ref, provider)
    try:
        with build_opener(ProviderRedirect(provider)).open(
                Request(ref, headers={"User-Agent": "coding-policy-successor-owner"}), timeout=10) as response:
            provider_url(response.geturl(), provider)
            body = response.read(BODY_LIMIT + 1)
            if len(body) > BODY_LIMIT:
                fail("provider evidence exceeds the bounded read; cite a smaller catalog page.")
            decoded = body.decode("utf-8")
    except (OSError, URLError, UnicodeError) as exc:
        fail("cannot read provider evidence: {}. Restore access or cite a reachable official source; nothing was recorded.".format(exc))
    page = PageText()
    page.feed(decoded)
    return " ".join(page.parts)


def provenance(proof, at=None, kind=None):
    fields(proof, {"provider", "citations", "checked_at", "relationship", "predecessor", "successor"}, "provenance")
    provider = proof["provider"]
    if not isinstance(provider, str) or provider not in PROVIDERS:
        fail("unknown provider; use a supported provider catalog, never a model's own report.")
    citations = proof["citations"]
    if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
        fail("cite one to three bounded provider sources for the identity and successor relationship.")
    for citation in citations:
        fields(citation, {"ref", "quote"}, "provider citation")
        provider_url(citation["ref"], provider)
        text(citation["quote"], "Provider relationship/identity quote")
    if kind is not None and PROVIDERS[provider][0] != kind:
        fail("provider and installed worker kind differ.")
    checked = timestamp(proof["checked_at"], "Successor provenance")
    if at is not None and not timedelta(0) <= timestamp(at, "Successor checkpoint") - checked < capabilities.INTERVAL:
        fail("provider provenance is stale or future; read the official successor relationship again.")
    if proof["relationship"] != "same_family_successor":
        fail("record the provider's explicit same-family successor relationship, not prefix similarity.")
    for name in ("predecessor", "successor"):
        identity = proof[name]
        fields(identity, {"model", "family", "version", "status"}, name + " identity")
        for field in ("model", "family", "version"):
            text(identity[field], name + " " + field)
        if not isinstance(identity["status"], str) or identity["status"] not in {"active", "retired"}:
            fail("provider status is active or retired.")
    before, after = proof["predecessor"], proof["successor"]
    if before["family"] != after["family"] or before["version"] == after["version"] or before["model"] == after["model"]:
        fail("the exact models must be distinct versions in the same provider family.")
    quote = " ".join(citation["quote"] for citation in citations)
    if not all(value in quote for value in (before["model"], after["model"], before["family"], before["version"], after["version"])):
        fail("the cited provider evidence must bind both exact model IDs and their family/versions.")


def row_binding(row):
    # A predecessor's billing observation never follows its replacement.
    return {key: value for key, value in row.items() if key != "billing_evidence"}


def validate(row):
    fields(row, {"schema_version", "id", "worker", "kind", "window_group", "role", "round", "tier_row",
                 "successor", "authorized_row", "needs", "provenance", "provider_sha256", "origin", "assigned_at", "history"}, "saved placement")
    if row["schema_version"] != SCHEMA_VERSION:
        fail("unsupported placement schema; update the owner skill.")
    for key in ("id", "worker", "successor"):
        capabilities._name(row[key], "Successor " + key)
    if not isinstance(row["window_group"], str):
        fail("window_group must bind the configured account identity.")
    role, round_type = row["role"], row["round"]
    if (not isinstance(role, str) or role != canonical_role(role) or role not in ROLE_ROUNDS
            or not isinstance(round_type, str) or round_type not in ROLE_ROUNDS[role]
            or round_type in JUDGMENT_ROUNDS or not isinstance(row["tier_row"], str)
            or row["tier_row"] in JUDGMENT_ROUNDS):
        fail("inherit only the same non-judgment operating responsibility; judge and judgment floors stay pinned.")
    if not isinstance(row["tier_row"], str) or row["tier_row"] not in ROLE_ROUNDS[role]:
        fail("the predecessor row is not authorized for this responsibility.")
    if row["tier_row"] != round_type:
        fail("inherit the predecessor's existing round/spot, not a newly borrowed capability.")
    if row["needs"] != list(capabilities.required(role, round_type, JUDGMENT_ROUNDS)):
        fail("required capabilities differ from the authorized responsibility.")
    if not isinstance(row["authorized_row"], dict) or not isinstance(row["authorized_row"].get("model"), str):
        fail("authorized_row needs the exact predecessor's configured row.")
    provenance(row["provenance"], kind=row["kind"])
    if (row["provenance"]["predecessor"]["model"] != row["authorized_row"]["model"]
            or row["provenance"]["successor"]["model"] != row["successor"]):
        fail("provider identities do not bind the authorized predecessor and successor.")
    digest = row["provider_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        fail("provider_sha256 needs the owner-stamped source digest.")
    fields(row["origin"], {"verdict", "entries"}, "qualification origin")
    if (not isinstance(row["origin"]["verdict"], str) or row["origin"]["verdict"] not in {"adequate", "unknown"}
            or not isinstance(row["origin"]["entries"], list)):
        fail("origin must preserve adequate or unknown evidence, never negative qualification.")
    for entry in row["origin"]["entries"]:
        capabilities.validate_entry(entry)
        if (entry["model"] != row["authorized_row"]["model"]
                or entry["effort"] != (row["authorized_row"].get("effort") or capabilities.DEFAULT_EFFORT)
                or entry["capability"] not in row["needs"]):
            fail("origin entries must belong to the authorized predecessor's exact pair and capabilities.")
    if capabilities.assess({"entries": row["origin"]["entries"]}, row["authorized_row"]["model"],
                           row["authorized_row"].get("effort"), row["needs"]) != row["origin"]["verdict"]:
        fail("origin verdict disagrees with the preserved predecessor evidence.")
    timestamp(row["assigned_at"], "Successor assignment")
    if not isinstance(row["history"], list):
        fail("recalibration history must be an array.")
    previous = row["assigned_at"]
    for event in row["history"]:
        validate_recalibration(event, saved=True)
        if event["id"] != row["id"] or timestamp(event["recorded_at"], "Recalibration") < timestamp(previous, "Previous checkpoint"):
            fail("recalibration cannot rewrite placement chronology.")
        previous = event["recorded_at"]


def record(path, data, at, agent):
    fields(data, {"id", "worker", "role", "round", "tier_row", "successor", "provenance"}, "successor report")
    for field in ("id", "worker", "tier_row", "successor"):
        capabilities._name(data[field], "Successor " + field)
    policy = agent.tier_routing
    if policy is None or policy["mode"] != "minimum_adequate":
        fail("the operator's minimum-adequate opt-in is required; an explicit pin is not replaced.")
    if data["worker"] != agent.name or data["tier_row"] not in agent.tiers:
        fail("name the exact configured predecessor worker and row before editing config.")
    predecessor = agent.tiers[data["tier_row"]]
    role, round_type = data["role"], data["round"]
    if (not isinstance(role, str) or role not in ROLE_ROUNDS or not isinstance(round_type, str)
            or round_type not in ROLE_ROUNDS[role]):
        fail("name an existing authorized role and round, not a new responsibility.")
    proof = data["provenance"]
    provenance(proof, at, agent.kind)
    if proof["successor"]["status"] != "active":
        fail("successor retirement prevents inheritance; preserve it in maintenance evidence.")
    now = timestamp(at, "Successor assignment").isoformat()
    row = {**copy.deepcopy(data), "schema_version": SCHEMA_VERSION, "kind": agent.kind,
           "window_group": agent.window_group, "authorized_row": row_binding(predecessor),
           "needs": list(capabilities.required(data["role"], data["round"], JUDGMENT_ROUNDS)),
           "assigned_at": now, "history": [], "origin": {"verdict": "unknown", "entries": []},
           "provider_sha256": "0" * 64}
    validate(row)
    observed = []
    for citation in proof["citations"]:
        body = " ".join(read_provider(citation["ref"], proof["provider"]).split())
        if " ".join(citation["quote"].split()) not in body:
            fail("the cited quote is absent from the trusted provider source; no inheritance recorded.")
        observed.append(body)
    row["provider_sha256"] = hashlib.sha256(json.dumps(observed, ensure_ascii=False).encode("utf-8")).hexdigest()
    with state_lock(capabilities.storage_path(path)):
        document = capabilities.load(path, for_write=True)
        existing = next((old for old in document.get("successors", []) if old["id"] == data["id"]), None)
        if existing is not None:
            if all(existing[key] == value for key, value in data.items()) and all(
                    existing[key] == row[key] for key in ("kind", "window_group", "authorized_row", "needs")):
                return existing
            fail("placement id already exists with different evidence/authority; recalibrate through `{}`, never rewrite its origin.".format(
                runnable.command("capability-record --record FILE")))
        needs = row["needs"]
        row["origin"] = {"verdict": capabilities.assess(document, predecessor["model"], predecessor.get("effort"), needs),
                         "entries": [copy.deepcopy(entry) for entry in document["entries"]
                                     if entry["model"] == predecessor["model"]
                                     and entry["effort"] == (predecessor.get("effort") or capabilities.DEFAULT_EFFORT)
                                     and entry["capability"] in needs]}
        capabilities.assess(document, data["successor"], predecessor.get("effort"), needs)
        document["schema_version"] = capabilities.SCHEMA_VERSION
        document.setdefault("successors", []).append(row)
        capabilities.validate(document)
        save_state(capabilities.storage_path(path), document)
    return row


def validate_recalibration(event, saved=False):
    fields(event, {"id", "action", "verdict", "source", "provider_status"} | ({"schema_version", "recorded_at"} if saved else set()), "recalibration")
    if not isinstance(event["action"], str) or event["action"] not in {"keep", "revise", "withdraw"}:
        fail("recalibration action is keep, revise or withdraw.")
    if event["verdict"] not in capabilities.VERDICTS:
        fail("recalibration verdict is adequate, inadequate or unknown.")
    if not isinstance(event["provider_status"], str) or event["provider_status"] not in {"active", "retired", "unknown"}:
        fail("provider status is active, retired or unknown.")
    capabilities.validate_entry({"schema_version": capabilities.ENTRY_SCHEMA_VERSION, "model": "recalibration",
                                 "effort": "default", "capability": "implementation", "verdict": event["verdict"],
                                 "source": event["source"], "recorded_at": event.get("recorded_at", "2000-01-01T00:00:00Z")})
    if event["action"] != "withdraw" and (event["verdict"] == "inadequate" or event["provider_status"] != "active"):
        fail("contrary evidence, retirement or unknown provider status requires withdrawal, not inherited permission.")
    if event["action"] == "revise" and event["verdict"] != "adequate":
        fail("revise confirms measured placement only with adequate evidence; unknown stays provisional on keep.")
    if saved and event["schema_version"] != SCHEMA_VERSION:
        fail("unsupported recalibration schema.")


def recalibrate(document, events, at):
    if not isinstance(events, list) or not events:
        fail("recalibrations needs at least one maintenance outcome.")
    seen = set()
    for event in events:
        validate_recalibration(event)
        observed = timestamp(event["source"]["dated"] + "T00:00:00Z", "Recalibration source")
        if not timedelta(0) <= timestamp(at, "Recalibration checkpoint") - observed < capabilities.INTERVAL:
            fail("recalibration needs current dated outcome evidence; unknown results remain unknown.")
        identity = text(event["id"], "Recalibration id")
        if identity in seen:
            fail("one maintenance report cannot recalibrate the same placement twice.")
        seen.add(identity)
        row = next((item for item in document.get("successors", []) if item["id"] == identity), None)
        if row is None:
            fail("unknown placement id {}; inspect `{}`.".format(identity, runnable.command("capability-show")))
        stamped = {**copy.deepcopy(event), "schema_version": SCHEMA_VERSION,
                   "recorded_at": timestamp(at, "Recalibration checkpoint").isoformat()}
        if row["history"] and row["history"][-1] == stamped:
            continue
        if row["history"] and row["history"][-1]["action"] == "withdraw":
            fail("a withdrawn placement cannot be revived by maintenance; record a new authorized assignment.")
        if event["action"] != "withdraw":
            predecessor = row["authorized_row"]
            capabilities.assess(document, predecessor["model"], predecessor.get("effort"), row["needs"])
            verdict = capabilities.assess(document, row["successor"], predecessor.get("effort"), row["needs"])
            if event["action"] == "revise" and verdict != "adequate":
                fail("revise needs actual successor capability entries from this maintenance report, not a launch smoke test.")
        row["history"].append(stamped)


def inspect(row, at):
    last = row["history"][-1] if row["history"] else None
    status = "withdrawn" if last and last["action"] == "withdraw" else "confirmed" if last and last["action"] == "revise" else "provisional"
    checked = last["recorded_at"] if last else row["assigned_at"]
    if timestamp(checked, "Placement checkpoint") > timestamp(at, "Placement read"):
        status = "future"
    due_at = timestamp(checked, "Placement checkpoint") + capabilities.INTERVAL
    return {"id": row["id"], "status": status, "origin": copy.deepcopy(row["origin"]),
            "provenance": copy.deepcopy(row["provenance"]), "assigned_at": row["assigned_at"],
            "recalibration_due_at": due_at.isoformat(), "due": status == "future" or timestamp(at, "Placement read") >= due_at,
            "history": copy.deepcopy(row["history"])}


def placement(document, agent, role, round_type, name, configured, needs, at, worker_kind=None):
    """Exact authorized spot only. New evidence may veto, never rewrite the origin."""
    for row in reversed(document.get("successors", [])):
        expected = {**row["authorized_row"], "model": row["successor"]}
        if (row["worker"], row["kind"], row["window_group"], row["role"], row["round"], row["tier_row"], row["needs"], expected) != (
                worker_kind or agent.name, agent.kind, agent.window_group, canonical_role(role), round_type, name, list(needs), row_binding(configured)):
            continue
        result = inspect(row, at)
        if timestamp(at, "Placement read") < timestamp(row["assigned_at"], "Assignment"):
            result["status"] = "future"
        if row["provenance"]["successor"]["status"] == "retired":
            result["status"] = "retired"
        try:
            capabilities.assess(document, row["authorized_row"]["model"], configured.get("effort"), needs)
            capabilities.assess(document, row["successor"], configured.get("effort"), needs)
        except capabilities.InadequateCapability:
            result["status"] = "inadequate"
        return result
    return None


def due(document, at):
    return [inspect(row, at) for row in document.get("successors", [])
            if inspect(row, at)["status"] != "withdrawn" and inspect(row, at)["due"]]
