"""Bounded read-only GitHub transport for central fleet publication evidence.

No CLI entrypoint, disk state, extraction, token handling or publish operation.
None means a completely enumerated run has no exact named artifact. Any lost,
expired, ambiguous, oversized or malformed evidence refuses instead. Record
shape belongs to fleet_publish_record; source/workflow trust remains caller-owned.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
import zipfile
import zlib
from datetime import datetime
from typing import Literal, NoReturn
from urllib.parse import parse_qsl, urlsplit

from fleet_publish_record import (
    CENTRAL_REPOSITORY,
    INTENT_ARTIFACT,
    MAX_RECORD_BYTES,
    RECEIPT_ARTIFACT,
    FleetPublishRecord,
    FleetRecordError,
    decode_record,
    valid_run_id,
)

MAX_JSON_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_COMMANDS = 128
COMMAND_TIMEOUT_SECONDS = 30.0
TERMINATE_GRACE_SECONDS = 1.0
PAGE_SIZE = 100
MAX_PAGES = 20
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000


class FleetPublishLookupError(RuntimeError):
    """Repair the native lookup; absent evidence never authorizes a publish retry."""

    def __init__(
        self, kind: str, *, returncode: int | None = None, stderr_sha256: str | None = None
    ) -> None:
        super().__init__(
            "Restore bounded gh read access and complete central publication evidence; "
            "preserve uncertain publication attempts without replay."
        )
        self.kind = kind
        self.returncode = returncode
        self.stderr_sha256 = stderr_sha256


def _refuse(kind: str) -> NoReturn:
    raise FleetPublishLookupError(kind) from None


def _route(route: str) -> None:
    if (
        not isinstance(route, str)
        or not 1 <= len(route) <= 2048
        or not route.isascii()
        or re.search(r"[\x00-\x20\x7f]", route)
        or "://" in route
        or "#" in route
    ):
        _refuse("invalid_route")
    try:
        parts = urlsplit(route)
    except ValueError:
        _refuse("invalid_route")
    segments = parts.path.split("/")
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or len(segments) < 3
        or segments[:2] != ["repos", "jbaruch"]
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", segments[2])
        or any(
            segment in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,256}", segment)
            for segment in segments[3:]
        )
        or not re.fullmatch(r"[A-Za-z0-9_.~%:/@+,&=*-]*", parts.query)
        or re.search(r"%(?![a-fA-F0-9]{2})", parts.query)
    ):
        _refuse("invalid_route")
    try:
        pairs = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
        )
    except (ValueError, UnicodeError):
        _refuse("invalid_route")
    keys = set()
    for key, value in pairs:
        if (
            key in keys
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "://" in value
            or (key != "created" and not re.fullmatch(r"[A-Za-z0-9_.~:/@ +,=-]*", value))
            or "%" in value
        ):
            _refuse("invalid_route")
        if key == "created":
            if not re.fullmatch(r">=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
                _refuse("invalid_route")
            try:
                datetime.fromisoformat(value[2:])
            except ValueError:
                _refuse("invalid_route")
        keys.add(key)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _refuse("invalid_json")
        value[key] = item
    return value


def _finite(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        _refuse("invalid_json")
    return value


def _nonfinite(_value: str) -> NoReturn:
    _refuse("invalid_json")


def _decode(raw: bytes) -> object:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_nonfinite,
            parse_float=_finite,
        )
        pending = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
                _refuse("invalid_json")
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend((child, depth + 1) for pair in item.items() for child in pair)
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError):
        _refuse("invalid_json")


def _retire(child: subprocess.Popen[bytes]) -> None:
    """A bounded failed read must not leave its owned gh process group alive."""
    try:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # The exact owned process group already retired.
        try:
            child.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass  # Escalate the still-owned group, including inherited pipes.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The exact owned group is already absent after TERM.
        child.wait(timeout=TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        raise FleetPublishLookupError(
            "command_retirement_failed", returncode=child.poll()
        ) from None


class GitHubClient:
    def __init__(self) -> None:
        self._commands = 0

    def _read(self, route: str, maximum: int) -> bytes:
        _route(route)
        if self._commands >= MAX_COMMANDS:
            _refuse("command_budget")
        self._commands += 1
        try:
            child = subprocess.Popen(
                ["gh", "api", "--hostname", "github.com", "--method", "GET", route],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise FleetPublishLookupError("command_unavailable") from None
        out = bytearray()
        err_count = 0
        err_hash = hashlib.sha256()
        failure = None
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        selector = selectors.DefaultSelector()
        try:
            assert child.stdout is not None and child.stderr is not None
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "command_timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == "stdout":
                        if len(out) + len(chunk) > maximum:
                            failure = "stdout_budget"
                            break
                        out.extend(chunk)
                    else:
                        err_count += len(chunk)
                        err_hash.update(chunk)
                        if err_count > MAX_STDERR_BYTES:
                            failure = "stderr_budget"
                            break
                if failure is not None:
                    break
            if failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "command_timeout"
                else:
                    try:
                        child.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        failure = "command_timeout"
            if failure is not None:
                _retire(child)
                raise FleetPublishLookupError(
                    failure, returncode=child.returncode, stderr_sha256=err_hash.hexdigest()
                )
            if child.returncode != 0:
                _retire(child)
                raise FleetPublishLookupError(
                    "command_failed",
                    returncode=child.returncode,
                    stderr_sha256=err_hash.hexdigest(),
                )
            return bytes(out)
        except OSError:
            raise FleetPublishLookupError(
                "command_io_failed", returncode=child.poll(), stderr_sha256=err_hash.hexdigest()
            ) from None
        finally:
            selector.close()
            if child.poll() is None:
                _retire(child)
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()

    def json(self, route: str) -> object:
        return _decode(self._read(route, MAX_JSON_BYTES))

    def artifact(
        self, run_id: str, kind: Literal["intent", "receipt"]
    ) -> FleetPublishRecord | None:
        if not valid_run_id(run_id) or kind not in ("intent", "receipt"):
            _refuse("invalid_selection")
        name = INTENT_ARTIFACT if kind == "intent" else RECEIPT_ARTIFACT
        member = "intent.json" if kind == "intent" else "receipt.json"
        found = None
        seen: set[int] = set()
        total = None
        for page_number in range(1, MAX_PAGES + 1):
            route = (
                f"repos/{CENTRAL_REPOSITORY}/actions/runs/{run_id}/artifacts"
                f"?per_page={PAGE_SIZE}&page={page_number}"
            )
            page = self.json(route)
            if not isinstance(page, dict):
                _refuse("invalid_artifact_listing")
            current = page.get("total_count")
            rows = page.get("artifacts")
            if (
                type(current) is not int
                or not 0 <= current <= PAGE_SIZE * MAX_PAGES
                or not isinstance(rows, list)
                or len(rows) > PAGE_SIZE
            ):
                _refuse("invalid_artifact_listing")
            if total is not None and current != total:
                _refuse("changed_artifact_listing")
            total = current
            if len(rows) != min(PAGE_SIZE, total - len(seen)):
                _refuse("incomplete_artifact_listing")
            for row in rows:
                if not isinstance(row, dict):
                    _refuse("invalid_artifact_listing")
                artifact_id, title = row.get("id"), row.get("name")
                size, expired, workflow = (
                    row.get("size_in_bytes"),
                    row.get("expired"),
                    row.get("workflow_run"),
                )
                if (
                    type(artifact_id) is not int
                    or not valid_run_id(str(artifact_id))
                    or artifact_id in seen
                    or not isinstance(title, str)
                    or not 1 <= len(title) <= 255
                    or any(ord(char) < 32 or ord(char) == 127 for char in title)
                    or type(expired) is not bool
                    or type(size) is not int
                    or size < 0
                    or not isinstance(workflow, dict)
                    or type(workflow.get("id")) is not int
                    or str(workflow["id"]) != run_id
                ):
                    _refuse("invalid_artifact_listing")
                seen.add(artifact_id)
                if title == name:
                    if found is not None:
                        _refuse("duplicate_artifact")
                    if expired:
                        _refuse("expired_artifact")
                    if not 0 < size <= MAX_ARCHIVE_BYTES:
                        _refuse("artifact_size_budget")
                    found = artifact_id
            if len(seen) == total:
                break
        else:
            _refuse("artifact_page_budget")
        if found is None:
            return None
        archive = self._read(
            f"repos/{CENTRAL_REPOSITORY}/actions/artifacts/{found}/zip", MAX_ARCHIVE_BYTES
        )
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                members = zipped.infolist()
                if len(members) != 1:
                    _refuse("invalid_archive_member")
                info = members[0]
                if (
                    info.filename != member
                    or info.orig_filename != member
                    or info.is_dir()
                    or not stat.S_ISREG(info.external_attr >> 16)
                    or info.flag_bits & (1 | 0x40 | 0x2000)
                    or info.external_attr & 0x10
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or not 0 < info.file_size <= MAX_RECORD_BYTES
                ):
                    _refuse("invalid_archive_member")
                with zipped.open(info) as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                if len(raw) != info.file_size or len(raw) > MAX_RECORD_BYTES:
                    _refuse("invalid_archive_size")
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            OSError,
            UnicodeError,
            RuntimeError,
            NotImplementedError,
            ValueError,
            EOFError,
            zlib.error,
        ) as error:
            # Our fixed refusals are RuntimeError subclasses, not zip failures.
            if isinstance(error, FleetPublishLookupError):
                raise
            raise FleetPublishLookupError("invalid_archive") from None
        try:
            record = decode_record(raw)
        except FleetRecordError:
            raise FleetPublishLookupError("invalid_record") from None
        if record["run_id"] != run_id:
            _refuse("record_run_mismatch")
        return record
