"""Discriminated prior publication evidence, preserving native receipt records.

Historical facts support confirmed source continuity only. They deliberately do
not expose publication exit codes, receipt digests or original publication time.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fleet_publish_archive import ArchiveReader
from fleet_publish_historical import HistoricalPrior, prior_facts
from fleet_publish_record import FleetPublishRecord, parse_record


class NativePublication(TypedDict):
    proof_kind: Literal["native_receipt"]
    record: FleetPublishRecord


class HistoricalPublication(TypedDict):
    proof_kind: Literal["historical_source_proof"]
    proof: dict[str, Any]
    facts: HistoricalPrior


PriorPublication = NativePublication | HistoricalPublication
PublicationFacts = FleetPublishRecord | HistoricalPrior


def native_publication(record: object) -> NativePublication:
    return {"proof_kind": "native_receipt", "record": parse_record(record)}


def facts(value: PriorPublication) -> PublicationFacts:
    if value["proof_kind"] == "native_receipt":
        return value["record"]
    return value["facts"]


def prior_publication(reader: ArchiveReader, run_id: str) -> PriorPublication | None:
    """Native evidence wins; historical proof never repairs a negative receipt."""
    record = reader.record(run_id, "receipt")
    if record is not None:
        return native_publication(record)
    proof = reader.historical_source_proof(run_id)
    if proof is None:
        return None
    return {
        "proof_kind": "historical_source_proof",
        "proof": proof,
        "facts": prior_facts(proof),
    }
