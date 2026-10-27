"""Schema owner for central fleet publication identity/result records, version 1.

Both the immutable intent.json and receipt.json use this exact shape. Parsing
proves shape only: callers independently bind GitHub run/attempt/workflow/source
and native registry evidence. A claimed, missing or uncertain record grants no
publish retry or landing. Only the central publisher writes these records.
"""

from __future__ import annotations

import json
import re
from typing import Literal, NoReturn, TypedDict, cast

CENTRAL_REPOSITORY = "jbaruch/coding-policy"
CENTRAL_WORKFLOW = ".github/workflows/fleet-publish.yml"
INTENT_ARTIFACT = "fleet-publish-intent-v1"
RECEIPT_ARTIFACT = "fleet-publish-receipt-v1"
MAX_RECORD_BYTES = 64 * 1024

State = Literal["claimed", "published", "confirmed", "blocked", "uncertain"]
PublishOutcome = Literal["not_started", "success", "failure", "unknown"]
Moderation = Literal["not_checked", "pass", "pending", "fail", "unknown"]


class FleetPublishRecord(TypedDict):
    schema_version: int
    consumer_repository: str
    source_sha: str
    source_ref: str
    source_tree_sha: str
    workflow_repository: str
    workflow_sha: str
    run_id: str
    run_attempt: int
    plugin_name: str
    state: State
    registry_baseline: str
    version: str | None
    publish_outcome: PublishOutcome
    publish_exit_code: int | None
    landed_after_error: bool
    moderation: Moderation
    origin_run_id: str
    origin_run_attempt: int
    bookkeeping_sha: str | None


class FleetRecordError(ValueError):
    """A record cannot support central publication lookup or confirmation."""


def refuse() -> NoReturn:
    raise FleetRecordError(
        "Restore the complete supported central publication record and verify its "
        "original GitHub run/source; preserve uncertain attempts without replay."
    )


def valid_repository(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"jbaruch/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value)
    )


def valid_sha(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{40}", value))


def valid_run_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{0,19}", value))


def valid_version(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 100:
        return False
    match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?",
        value,
    )
    if not match:
        return False
    return all(
        not (part.isdecimal() and len(part) > 1 and part.startswith("0"))
        for part in (match.group(4) or "").split(".")
    )


def parse_record(value: object) -> FleetPublishRecord:
    """Validate fixed version-1 data; no transport or persistent writes occur."""
    if not isinstance(value, dict) or set(value) != set(
        FleetPublishRecord.__annotations__
    ):
        refuse()
    raw = cast(dict[str, object], value)
    try:
        for field in raw.values():
            if isinstance(field, str):
                field.encode("utf-8")
    except UnicodeError:
        refuse()
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or not valid_repository(raw["consumer_repository"])
        or raw["workflow_repository"] != CENTRAL_REPOSITORY
        or not all(
            valid_sha(raw[key])
            for key in ("source_sha", "source_tree_sha", "workflow_sha")
        )
        or not all(valid_run_id(raw[key]) for key in ("run_id", "origin_run_id"))
        or not all(
            type(raw[key]) is int and cast(int, raw[key]) > 0
            for key in ("run_attempt", "origin_run_attempt")
        )
        or not isinstance(raw["plugin_name"], str)
        or not re.fullmatch(r"jbaruch/[a-z0-9][a-z0-9_-]{0,99}", raw["plugin_name"])
        or raw["state"]
        not in ("claimed", "published", "confirmed", "blocked", "uncertain")
        or raw["publish_outcome"]
        not in ("not_started", "success", "failure", "unknown")
        or raw["moderation"]
        not in ("not_checked", "pass", "pending", "fail", "unknown")
        or type(raw["landed_after_error"]) is not bool
        or (
            raw["registry_baseline"] != "unpublished"
            and not valid_version(raw["registry_baseline"])
        )
        or (raw["version"] is not None and not valid_version(raw["version"]))
        or (
            raw["bookkeeping_sha"] is not None and not valid_sha(raw["bookkeeping_sha"])
        )
        or (
            raw["publish_exit_code"] is not None
            and type(raw["publish_exit_code"]) is not int
        )
    ):
        refuse()
    ref = raw["source_ref"]
    if (
        not isinstance(ref, str)
        or not ref.startswith("refs/heads/")
        or len(ref) <= len("refs/heads/")
        or len(ref) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in ref)
    ):
        refuse()
    if raw["state"] == "confirmed" and (
        raw["publish_outcome"] != "success"
        or raw["publish_exit_code"] != 0
        or raw["landed_after_error"] is not False
        or raw["moderation"] != "pass"
        or raw["version"] is None
        or raw["bookkeeping_sha"] is None
    ):
        refuse()
    return cast(FleetPublishRecord, dict(raw))


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            refuse()
        result[key] = value
    return result


def _nonfinite(_value: str) -> NoReturn:
    refuse()


def decode_record(raw: bytes) -> FleetPublishRecord:
    """Decode one bounded complete record without losing duplicate-key evidence."""
    if len(raw) > MAX_RECORD_BYTES:
        refuse()
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite
        )
    except (ValueError, UnicodeError, RecursionError):
        refuse()
    return parse_record(value)
