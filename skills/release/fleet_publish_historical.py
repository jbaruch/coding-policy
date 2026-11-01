"""Later source-derived publication evidence, never recovered receipt bytes.

The central archive owns this distinct schema1 family. Its writer verifies the
closed original producer, complete successful native attempt and whole package.
Readers retain the explicit derivation and NEW attester provenance. No effect,
network call, migration or retry occurs on import.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Literal, TypedDict

from fleet_publish_archive import (
    ArchiveError,
    FINALIZER_WORKFLOW,
    finalizer_identity,
    native_jobs,
    native_run,
    need,
    obj,
    parse_intent,
    positive_native,
    utc,
)

KIND = "fleet-publish-historical-source-proof"
DERIVATION = "closed-producer-success-v1"
MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
JSON_MEMBERS = frozenset({".tessl-plugin/plugin.json", "tile.json"})
CRITICAL_STEPS = (
    "Validate selected coordinates before minting a write credential",
    "Prepare verified data-only package and intent",
    "Review projected skills through canonical action",
    "Claim source atomically before publication",
    "Persist immutable pre-effect intent",
    "Publish once or perform moderation readback only",
    "Persist terminal evidence even after failed/uncertain publication",
)


class HistoricalPrior(TypedDict):
    """Derived common facts; deliberately lacks native receipt/exit fields."""

    schema_version: Literal[2]
    proof_kind: Literal["historical_source_proof"]
    consumer_repository: str
    source_sha: str
    source_ref: str
    source_tree_sha: str
    plugin_name: str
    registry_baseline: str
    version: str
    bookkeeping_sha: str
    run_id: str
    run_attempt: int
    origin_run_id: str
    origin_run_attempt: int
    state: Literal["confirmed"]
    moderation: Literal["pass"]
    attester: dict[str, Any]


def _digest(value: object) -> str:
    need(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None)
    return str(value)


def _member(value: object) -> str:
    need(
        isinstance(value, str)
        and 0 < len(value) <= 1000
        and value == str(PurePosixPath(value))
        and not value.startswith("/")
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )
    return str(value)


def _inventory(value: object) -> list[dict[str, Any]]:
    need(isinstance(value, list) and 0 < len(value) <= MAX_PACKAGE_FILES)
    if not isinstance(value, list):
        raise ArchiveError()
    rows = []
    total = 0
    for item in value:
        row = obj(item)
        path = _member(row.get("path"))
        need(set(row) == {"path", "bytes", "sha256", "json_sha256"})
        need(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_PACKAGE_BYTES)
        total += row["bytes"]
        _digest(row["sha256"])
        if path in JSON_MEMBERS:
            _digest(row["json_sha256"])
        else:
            need(row["json_sha256"] is None)
        rows.append(dict(row))
    need(total <= MAX_PACKAGE_BYTES)
    paths = [row["path"] for row in rows]
    need(paths == sorted(set(paths)))
    return rows


def package_binding(value: object, plugin: str, version: str) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw)
        == {
            "source",
            "installed",
            "installer",
            "fingerprint",
            "moderation",
            "version",
            "observed_at",
            "tool",
        }
    )
    source = _inventory(raw["source"])
    installed = _inventory(raw["installed"])
    need([row["path"] for row in source] == [row["path"] for row in installed])
    need(JSON_MEMBERS <= {row["path"] for row in source})
    for left, right in zip(source, installed, strict=True):
        need(
            left["json_sha256"] == right["json_sha256"]
            if left["path"] in JSON_MEMBERS
            else left["sha256"] == right["sha256"] and left["bytes"] == right["bytes"]
        )
    need(raw["installer"] == {"name": plugin, "version": version})
    _digest(raw["fingerprint"])
    need(raw["moderation"] == "pass" and raw["version"] == version)
    utc(raw["observed_at"])
    tool = obj(raw["tool"])
    need(set(tool) == {"name", "version"} and tool["name"] == "tessl")
    need(
        isinstance(tool["version"], str)
        and re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", tool["version"]) is not None
    )
    return dict(raw)


def parse_historical(value: object) -> dict[str, Any]:
    raw = obj(value)
    need(
        set(raw)
        == {
            "schema_version",
            "kind",
            "derivation",
            "intent",
            "native",
            "producer_pinset",
            "registry",
            "attester",
        }
    )
    need(
        type(raw["schema_version"]) is int
        and raw["schema_version"] == 1
        and raw["kind"] == KIND
        and raw["derivation"] == DERIVATION
    )
    envelope = parse_intent(raw["intent"])
    original = envelope["intent"]
    need(
        original["run_id"] == original["origin_run_id"]
        and original["run_attempt"] == original["origin_run_attempt"] == 1
        and isinstance(original["version"], str)
    )
    native = obj(raw["native"])
    need(set(native) == {"run", "attempt", "jobs"})
    run = native_run(native["run"], projected=True)
    attempt = native_run(native["attempt"], projected=True)
    jobs = native_jobs(native["jobs"], run, projected=True)
    need(
        run == attempt
        and run["id"] == original["run_id"]
        and run["head_sha"] == original["workflow_sha"]
        and run["display_title"]
        == f"fleet-publish-v1:{original['consumer_repository']}:{original['source_sha']}"
    )
    clean_native = {"run": run, "attempt": attempt, "jobs": jobs}
    need(positive_native(clean_native))
    publish = next(job for job in jobs if job["name"] == "publish")
    need(all(step["conclusion"] == "success" for step in publish["steps"]))
    for name, number in zip(CRITICAL_STEPS, (3, 7, 8, 9, 10, 11, 12), strict=True):
        matches = [step for step in publish["steps"] if step["name"] == name]
        need(len(matches) == 1 and matches[0]["number"] == number)
    # Exact historical pinset identities come from the co-shipped producer owner;
    # a remote record cannot introduce a trusted graph or copy current semantics.
    from fleet_publish_producer import HISTORICAL_PRODUCERS

    pins = obj(raw["producer_pinset"])
    need(any(pins == candidate for candidate in HISTORICAL_PRODUCERS))
    registry = package_binding(
        raw["registry"], original["plugin_name"], original["version"]
    )
    attester = finalizer_identity(raw["attester"])
    need(
        attester["workflow_path"] == FINALIZER_WORKFLOW
        and attester["run_id"] != original["run_id"]
        and attester["run_attempt"] == 1
    )
    return {
        "schema_version": 1,
        "kind": KIND,
        "derivation": DERIVATION,
        "intent": envelope,
        "native": clean_native,
        "producer_pinset": dict(pins),
        "registry": registry,
        "attester": attester,
    }


def prior_facts(value: object) -> HistoricalPrior:
    proof = parse_historical(value)
    envelope = proof["intent"]
    intent = envelope["intent"]
    return {
        "schema_version": 2,
        "proof_kind": "historical_source_proof",
        "consumer_repository": intent["consumer_repository"],
        "source_sha": intent["source_sha"],
        "source_ref": intent["source_ref"],
        "source_tree_sha": intent["source_tree_sha"],
        "plugin_name": intent["plugin_name"],
        "registry_baseline": intent["registry_baseline"],
        "version": intent["version"],
        "bookkeeping_sha": envelope["bookkeeping_sha"],
        "run_id": intent["run_id"],
        "run_attempt": 1,
        "origin_run_id": intent["run_id"],
        "origin_run_attempt": 1,
        "state": "confirmed",
        "moderation": "pass",
        "attester": proof["attester"],
    }


def describe_files(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """All members, not a selected subset. Duplicate-safe decoding is shared."""
    from fleet_publish_github import _decode

    rows = []
    for name, data in sorted(files.items()):
        _member(name)
        canonical = None
        if name in JSON_MEMBERS:
            value = _decode(data)
            need(isinstance(value, dict))
            canonical = hashlib.sha256(
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        rows.append(
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "json_sha256": canonical,
            }
        )
    return _inventory(rows)
