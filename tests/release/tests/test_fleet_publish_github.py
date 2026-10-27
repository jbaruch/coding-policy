"""Actual subprocess transport with fake gh and generated in-memory ZIP evidence."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
SPEC = importlib.util.spec_from_file_location(
    "fleet_publish_github", SOURCE / "fleet_publish_github.py"
)
assert SPEC is not None and SPEC.loader is not None
client: Any = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    SPEC.loader.exec_module(client)

RUN = "123456789"
CENTRAL = "repos/jbaruch/coding-policy"
LIST = f"{CENTRAL}/actions/runs/{RUN}/artifacts?per_page=100&page=1"
SECOND = f"{CENTRAL}/actions/runs/{RUN}/artifacts?per_page=100&page=2"
ZIP = f"{CENTRAL}/actions/artifacts/99/zip"
ROUTE = "repos/jbaruch/coding-policy/actions/runs?per_page=100&page=1"


def record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "consumer_repository": "jbaruch/nanoclaw-construction",
        "source_sha": "a" * 40,
        "source_ref": "refs/heads/main",
        "source_tree_sha": "b" * 40,
        "workflow_repository": "jbaruch/coding-policy",
        "workflow_sha": "c" * 40,
        "run_id": RUN,
        "run_attempt": 1,
        "plugin_name": "jbaruch/nanoclaw-construction",
        "state": "claimed",
        "registry_baseline": "unpublished",
        "version": None,
        "publish_outcome": "not_started",
        "publish_exit_code": None,
        "landed_after_error": False,
        "moderation": "not_checked",
        "origin_run_id": RUN,
        "origin_run_attempt": 1,
        "bookkeeping_sha": None,
    }


def zipped(
    entries=None,
    *,
    filename="intent.json",
    mode=stat.S_IFREG | 0o600,
    raw=None,
    compression=zipfile.ZIP_DEFLATED,
) -> bytes:
    stream = io.BytesIO()
    data = json.dumps(record()).encode() if raw is None else raw
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, value, filemode in entries or [(filename, data, mode)]:
            info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = filemode << 16
            info.compress_type = compression
            archive.writestr(info, value)
    return stream.getvalue()


def artifact(
    *,
    identifier: object = 99,
    name: object = "fleet-publish-intent-v1",
    **changes: object,
) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "expired": False,
        "size_in_bytes": 1024,
        "workflow_run": {"id": int(RUN)},
        "archive_download_url": "https://PRIVATE.invalid/do-not-follow",
        **changes,
    }


FAKE_GH = """import base64,json,os,sys,time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['FAKE_GH_LOG'],'a') as log:log.write(json.dumps(args)+'\\n')
include=len(args)==7 and args[5]=='--include'
if args[:5]!=['api','--hostname','github.com','--method','GET'] or len(args)!=(7 if include else 6):
    print('PRIVATE unexpected command',file=sys.stderr);sys.exit(87)
cases=json.loads(Path(os.environ['FAKE_GH_CASES']).read_text())
case=cases.get(args[6] if include else args[5])
if case is None:
    print('PRIVATE route not supplied by fixture',file=sys.stderr);sys.exit(88)
if include and 'wire' in case:
    sys.stdout.buffer.write(base64.b64decode(case['wire']));sys.stdout.flush();sys.exit(case.get('exit',0))
if include:
    if case.get('bad_header'):sys.stdout.buffer.write(b'PRIVATE invalid HTTP\\r\\n\\r\\n')
    else:sys.stdout.buffer.write(('HTTP/2.0 '+str(case.get('http_status',200))+(' Not Found' if case.get('http_status')==404 else ' OK')+'\\r\\nContent-Type: application/json; charset=utf-8\\r\\nX-GitHub-Media-Type: github.v3; format=json\\r\\n\\r\\n').encode())
if case.get('sleep'):time.sleep(30)
if 'stderr_repeat' in case:sys.stderr.buffer.write(b'PRIVATE '*case['stderr_repeat']);sys.stderr.flush()
if 'stdout_repeat' in case:sys.stdout.buffer.write(b'x'*case['stdout_repeat']);sys.stdout.flush()
if 'raw' in case:sys.stdout.buffer.write(base64.b64decode(case['raw']));sys.stdout.flush()
if 'json' in case:print(json.dumps(case['json']),flush=True)
if 'stderr' in case:print(case['stderr'],file=sys.stderr,flush=True)
sys.exit(case.get('exit',0))
"""


class GitHubClientTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fleet-publish-gh-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cases = self.root / "cases.json"
        self.log = self.root / "calls.jsonl"
        executable = self.root / "gh"
        executable.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        executable.chmod(0o700)
        env = patch.dict(
            os.environ,
            {
                "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
                "FAKE_GH_CASES": str(self.cases),
                "FAKE_GH_LOG": str(self.log),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.config: dict[str, object] = {}

    def replies(self, values):
        self.config.update(values)
        self.cases.write_text(json.dumps(self.config))

    def calls(self):
        return (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )

    def evidence(self, raw=None, *, row=None, kind="intent"):
        binary = zipped(filename=f"{kind}.json") if raw is None else raw
        metadata = artifact(name=f"fleet-publish-{kind}-v1") if row is None else row
        self.replies(
            {
                LIST: {"json": {"total_count": 1, "artifacts": [metadata]}},
                ZIP: {"raw": base64.b64encode(binary).decode()},
            }
        )
        return binary

    def refuse(self, action, kind=None):
        with self.assertRaises(client.FleetPublishLookupError) as error:
            action()
        if kind is not None:
            self.assertEqual(error.exception.kind, kind)
        self.assertNotIn("PRIVATE", str(error.exception))
        return error.exception

    def test_json_actual_get_transport_is_fixed_and_import_is_inert(self):
        self.assertEqual(self.calls(), [])
        self.replies({ROUTE: {"json": {"workflow_runs": [], "total_count": 0}}})
        self.assertEqual(
            client.GitHubClient().json(ROUTE), {"workflow_runs": [], "total_count": 0}
        )
        self.assertEqual(
            self.calls(),
            [["api", "--hostname", "github.com", "--method", "GET", ROUTE]],
        )

    def test_exact_intent_and_receipt_download_by_id_without_following_url(self):
        for kind in ("intent", "receipt"):
            with self.subTest(kind=kind):
                self.evidence(kind=kind)
                self.assertEqual(client.GitHubClient().artifact(RUN, kind), record())
        self.assertEqual([call[-1] for call in self.calls()], [LIST, ZIP, LIST, ZIP])
        self.assertNotIn("PRIVATE.invalid", self.log.read_text())

    def test_complete_zero_or_other_artifacts_are_the_only_none_results(self):
        for rows in ([], [artifact(name="unrelated")]):
            self.replies(
                {LIST: {"json": {"total_count": len(rows), "artifacts": rows}}}
            )
            self.assertIsNone(client.GitHubClient().artifact(RUN, "intent"))
        self.assertEqual([call[-1] for call in self.calls()], [LIST, LIST])

    def test_actual_pagination_reaches_late_target_and_exhausts_absence(self):
        first = [artifact(identifier=1000 + i, name=f"other-{i}") for i in range(100)]
        self.replies(
            {
                LIST: {"json": {"total_count": 101, "artifacts": first}},
                SECOND: {"json": {"total_count": 101, "artifacts": [artifact()]}},
                ZIP: {"raw": base64.b64encode(zipped()).decode()},
            }
        )
        self.assertEqual(client.GitHubClient().artifact(RUN, "intent"), record())
        self.assertEqual([call[-1] for call in self.calls()], [LIST, SECOND, ZIP])
        self.replies(
            {
                SECOND: {
                    "json": {
                        "total_count": 101,
                        "artifacts": [artifact(name="not-target")],
                    }
                }
            }
        )
        self.assertIsNone(client.GitHubClient().artifact(RUN, "intent"))

    def test_duplicate_name_on_later_page_refuses_before_download(self):
        rows = [
            artifact(),
            *[artifact(identifier=1000 + i, name=f"other-{i}") for i in range(99)],
        ]
        self.replies(
            {
                LIST: {"json": {"total_count": 101, "artifacts": rows}},
                SECOND: {
                    "json": {
                        "total_count": 101,
                        "artifacts": [artifact(identifier=100)],
                    }
                },
            }
        )
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"), "duplicate_artifact"
        )
        self.assertEqual([call[-1] for call in self.calls()], [LIST, SECOND])

    def test_target_expired_oversize_and_bad_identity_are_errors_not_absence(self):
        for changes, expected in (
            ({"expired": True}, "expired_artifact"),
            ({"size_in_bytes": client.MAX_ARCHIVE_BYTES + 1}, "artifact_size_budget"),
            ({"workflow_run": {"id": 987}}, "invalid_artifact_listing"),
            ({"id": True}, "invalid_artifact_listing"),
            ({"expired": None}, "invalid_artifact_listing"),
        ):
            with self.subTest(changes=changes):
                self.evidence(row=artifact(**changes))
                self.refuse(
                    lambda: client.GitHubClient().artifact(RUN, "intent"), expected
                )
        self.assertTrue(all(call[-1] == LIST for call in self.calls()))

    def test_short_changed_duplicate_and_over_budget_pages_never_prove_absence(self):
        first = [artifact(identifier=1000 + i, name=f"other-{i}") for i in range(100)]
        for page, expected in (
            ({"total_count": True, "artifacts": []}, "invalid_artifact_listing"),
            ({"total_count": 2, "artifacts": []}, "incomplete_artifact_listing"),
            ({"total_count": 2001, "artifacts": []}, "invalid_artifact_listing"),
            (
                {"total_count": 2, "artifacts": [artifact(), artifact()]},
                "invalid_artifact_listing",
            ),
        ):
            with self.subTest(page=page):
                self.replies({LIST: {"json": page}})
                self.refuse(
                    lambda: client.GitHubClient().artifact(RUN, "intent"), expected
                )
        self.replies(
            {
                LIST: {"json": {"total_count": 101, "artifacts": first}},
                SECOND: {"json": {"total_count": 100, "artifacts": []}},
            }
        )
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"),
            "changed_artifact_listing",
        )

    def test_record_run_binding_unknown_version_and_missing_fields_refuse(self):
        for changes in (
            {"run_id": "234567890"},
            {"schema_version": 2},
            {"state": "PRIVATE"},
        ):
            with self.subTest(changes=changes):
                self.evidence(zipped(raw=json.dumps({**record(), **changes}).encode()))
                self.refuse(lambda: client.GitHubClient().artifact(RUN, "intent"))
        missing = record()
        del missing["source_sha"]
        self.evidence(zipped(raw=json.dumps(missing).encode()))
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"), "invalid_record"
        )

    def test_exact_member_regular_type_and_archive_cardinality(self):
        cases = [
            zipped(filename="../intent.json"),
            zipped(filename="/intent.json"),
            zipped(filename="receipt.json"),
            zipped(filename="intent.json/"),
            zipped(mode=stat.S_IFLNK | 0o777),
            zipped(mode=stat.S_IFDIR | 0o700),
            zipped(mode=stat.S_IFIFO | 0o600),
            zipped(mode=0o600),
            zipped(
                entries=[
                    ("intent.json", b"{}", stat.S_IFREG | 0o600),
                    ("other.json", b"{}", stat.S_IFREG | 0o600),
                ]
            ),
        ]
        for index, binary in enumerate(cases):
            with self.subTest(case=index):
                self.evidence(binary)
                self.refuse(
                    lambda: client.GitHubClient().artifact(RUN, "intent"),
                    "invalid_archive_member",
                )

    def test_duplicate_archive_names_and_nul_original_name_cannot_alias(self):
        with self.assertWarnsRegex(UserWarning, "Duplicate name"):
            duplicate = zipped(
                entries=[
                    ("intent.json", b"{}", stat.S_IFREG | 0o600),
                    ("intent.json", b"{}", stat.S_IFREG | 0o600),
                ]
            )
        nul = zipped(filename="intent.jsonXPRIVATE").replace(
            b"intent.jsonXPRIVATE", b"intent.json\x00PRIVATE"
        )
        for binary in (duplicate, nul):
            self.evidence(binary)
            self.refuse(
                lambda: client.GitHubClient().artifact(RUN, "intent"),
                "invalid_archive_member",
            )

    def test_encrypted_member_and_oversize_inflation_refuse_before_record(self):
        encrypted = bytearray(zipped())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = encrypted.index(signature) + offset
            struct.pack_into(
                "<H",
                encrypted,
                position,
                struct.unpack_from("<H", encrypted, position)[0] | 1,
            )
        for binary in (
            bytes(encrypted),
            zipped(raw=b" " * (client.MAX_RECORD_BYTES + 1)),
        ):
            self.evidence(binary)
            self.refuse(
                lambda: client.GitHubClient().artifact(RUN, "intent"),
                "invalid_archive_member",
            )

    def test_corrupt_archive_crc_compressed_data_and_record_refuse(self):
        plain = zipped(compression=zipfile.ZIP_STORED)
        corrupt = bytearray(plain)
        start = plain.index(b"{")
        corrupt[start] = ord("[")
        for binary in (
            b"PRIVATE invalid zip",
            bytes(corrupt),
            zipped(raw=b"PRIVATE invalid record"),
        ):
            self.evidence(binary)
            self.refuse(lambda: client.GitHubClient().artifact(RUN, "intent"))

    def test_created_cutoff_uses_fixed_get_transport_and_only_canonical_utc_comparison(
        self,
    ):
        route = "repos/jbaruch/coding-policy/actions/workflows/fleet-publish.yml/runs?event=workflow_dispatch&created=%3E%3D2024-10-20T10%3A00%3A00Z&per_page=100&page=1"
        self.replies({route: {"json": {"total_count": 0, "workflow_runs": []}}})
        self.assertEqual(
            client.GitHubClient().json(route), {"total_count": 0, "workflow_runs": []}
        )
        self.assertEqual(self.calls()[0][-1], route)
        for query in (
            "created=%3E%3D2024-02-30T10%3A00%3A00Z",
            "created=%3E%3D2024-10-20T10%3A00%3A00%2B01%3A00",
            "created=%3E2024-10-20T10%3A00%3A00Z",
            "created=%3C%3D2024-10-20T10%3A00%3A00Z",
            "created=2024-10-20T10%3A00%3A00Z",
            "created=%253E%253D2024-10-20T10%3A00%3A00Z",
            "x=%3E%3D2024-10-20T10%3A00%3A00Z",
        ):
            self.refuse(
                lambda query=query: client.GitHubClient().json(
                    "repos/jbaruch/coding-policy/actions/runs?" + query
                ),
                "invalid_route",
            )
        self.assertEqual(len(self.calls()), 1)

    def test_routes_reject_urls_flags_controls_shell_and_encoded_traversal(self):
        for route in (
            "https://github.com/PRIVATE",
            "/repos/jbaruch/repo",
            "--hostname=PRIVATE",
            "repos/other/repo",
            "repos/jbaruch/repo/../PRIVATE",
            "repos/jbaruch/repo//PRIVATE",
            "repos/jbaruch/repo/%2e%2e/PRIVATE",
            "repos/jbaruch/repo?x=%0aPRIVATE",
            "repos/jbaruch/repo?x=one&x=two",
            "repos/jbaruch/repo?x=https%3A%2F%2FPRIVATE",
            "repos/jbaruch/repo;PRIVATE",
            "repos/jbaruch/repo\nPRIVATE",
            "repos/jbaruch/repo?x=%zz",
            "repos/jbaruch/repo#",
            "repos/jbaruch/repo?x=%24%28PRIVATE%29",
        ):
            with self.subTest(route=route):
                self.refuse(
                    lambda route=route: client.GitHubClient().json(route),
                    "invalid_route",
                )
        self.assertEqual(self.calls(), [])

    def test_native_http_status_is_typed_without_stderr_or_payload_inference(self):
        route = (
            "repos/jbaruch/coding-policy/git/ref/tags/fleet-publish-v1/runs/101/hold"
        )
        for status, kind in (
            (404, "native_missing"),
            (410, "native_expired"),
            (403, "native_http_error"),
            (500, "native_http_error"),
        ):
            self.replies(
                {
                    route: {
                        "http_status": status,
                        "json": {"message": "PRIVATE diagnostic"},
                        "stderr": "PRIVATE",
                        "exit": 1,
                    }
                }
            )
            error = self.refuse(lambda: client.GitHubClient().native_json(route), kind)
            self.assertEqual(error.http_status, status)
        self.replies({route: {"json": {"ref": "fixed native"}}})
        self.assertEqual(
            client.GitHubClient().native_json(route), {"ref": "fixed native"}
        )
        self.assertTrue(
            all(call[5] == "--include" and call[6] == route for call in self.calls())
        )

    def test_independent_literal_gh_wire_accepts_mixed_status_lf_and_crlf_only(self):
        for status, ending, exit_code in (
            (200, b"\n", 0),
            (200, b"\r\n", 0),
            (404, b"\n", 1),
            (404, b"\r\n", 1),
        ):
            body = b'{"message":"Not Found"}' if status == 404 else b'{"ok":true}'
            reason = b"Not Found" if status == 404 else b"OK"
            wire = (
                b"HTTP/2.0 "
                + str(status).encode()
                + b" "
                + reason
                + ending
                + b"Content-Type: application/json; charset=utf-8\r\nX-GitHub-Api-Version-Selected: 2026-03-10\r\n\r\n"
                + body
            )
            self.replies(
                {ROUTE: {"wire": base64.b64encode(wire).decode(), "exit": exit_code}}
            )
            if status == 200:
                self.assertEqual(client.GitHubClient().native_json(ROUTE), {"ok": True})
            else:
                self.refuse(
                    lambda: client.GitHubClient().native_json(ROUTE), "native_missing"
                )
        for bad in (
            b"HTTP/2.0 200 OK\nContent-Type: application/json\nX-Bad: two\r\n\r\n{}",
            b"HTTP/2.0 200 OK\nHTTP/2.0 200 OK\r\n\r\n{}",
        ):
            self.replies({ROUTE: {"wire": base64.b64encode(bad).decode()}})
            self.refuse(
                lambda: client.GitHubClient().native_json(ROUTE),
                "invalid_http_response",
            )

    def test_native_http_malformed_body_header_and_status_exit_conflict_are_not_absence(
        self,
    ):
        for data in (
            {
                "http_status": 404,
                "raw": base64.b64encode(b"PRIVATE").decode(),
                "exit": 1,
            },
            {"http_status": 404, "json": {"message": "Not Found"}, "exit": 0},
            {"bad_header": True, "json": {"message": "Not Found"}, "exit": 1},
        ):
            self.replies({ROUTE: data})
            error = self.refuse(lambda: client.GitHubClient().native_json(ROUTE))
            self.assertNotIn(error.kind, ("native_missing", "native_expired"))

    def test_status_aware_artifact_preserves_expiry_as_typed_native_failure(self):
        self.replies(
            {LIST: {"http_status": 404, "json": {"message": "Not Found"}, "exit": 1}}
        )
        error = self.refuse(
            lambda: client.GitHubClient().artifact_evidence(RUN, "intent"),
            "native_missing",
        )
        self.assertEqual(error.http_status, 404)
        self.evidence()
        self.assertEqual(
            client.GitHubClient().artifact_evidence(RUN, "intent"), record()
        )

    def test_selection_rejects_invalid_run_kind_without_subprocess(self):
        for run, kind in (
            ("0", "intent"),
            ("../PRIVATE", "intent"),
            (RUN, "PRIVATE"),
            (True, "receipt"),
        ):
            self.refuse(
                lambda run=run, kind=kind: client.GitHubClient().artifact(run, kind),
                "invalid_selection",
            )
        self.assertEqual(self.calls(), [])

    def test_json_duplicates_nonfinite_surrogates_and_invalid_utf8_refuse(self):
        for raw in (
            b'{"id":1,"id":2}',
            b"NaN",
            b"1e999",
            b'"\\ud800"',
            b"\xff",
            b"PRIVATE",
        ):
            self.replies({ROUTE: {"raw": base64.b64encode(raw).decode()}})
            self.refuse(lambda: client.GitHubClient().json(ROUTE), "invalid_json")

    def test_failure_retains_exit_code_hash_but_never_stream_contents(self):
        self.replies(
            {
                ROUTE: {
                    "raw": base64.b64encode(b"PRIVATE body").decode(),
                    "stderr": "PRIVATE native error",
                    "exit": 37,
                }
            }
        )
        error = self.refuse(lambda: client.GitHubClient().json(ROUTE), "command_failed")
        self.assertEqual(error.returncode, 37)
        self.assertEqual(len(error.stderr_sha256), 64)

    def test_stdout_stderr_download_and_command_caps_are_actual_subprocess_bounds(self):
        self.replies({ROUTE: {"stdout_repeat": 1025}})
        with patch.object(client, "MAX_JSON_BYTES", 1024):
            self.refuse(lambda: client.GitHubClient().json(ROUTE), "stdout_budget")
        self.replies({ROUTE: {"stderr_repeat": 1024}})
        with patch.object(client, "MAX_STDERR_BYTES", 1024):
            self.refuse(lambda: client.GitHubClient().json(ROUTE), "stderr_budget")
        self.evidence()
        self.replies({ZIP: {"stdout_repeat": client.MAX_ARCHIVE_BYTES + 1}})
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"), "stdout_budget"
        )
        self.replies({ROUTE: {"json": {}}})
        api = client.GitHubClient()
        with patch.object(client, "MAX_COMMANDS", 1):
            api.json(ROUTE)
            before = len(self.calls())
            self.refuse(lambda: api.json(ROUTE), "command_budget")
            self.assertEqual(len(self.calls()), before)

    def test_missing_download_and_incomplete_later_page_never_return_none(self):
        self.evidence()
        self.replies({ZIP: {"exit": 37, "stderr": "PRIVATE unavailable download"}})
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"), "command_failed"
        )
        first = [artifact(identifier=1000 + i, name=f"other-{i}") for i in range(100)]
        self.replies(
            {
                LIST: {"json": {"total_count": 101, "artifacts": first}},
                SECOND: {"json": {"total_count": 101, "artifacts": []}},
            }
        )
        self.refuse(
            lambda: client.GitHubClient().artifact(RUN, "intent"),
            "incomplete_artifact_listing",
        )

    def test_corrupt_deflate_empty_zip_and_duplicate_record_keys_refuse(self):
        compressed = bytearray(zipped())
        name_length, extra_length = struct.unpack_from("<HH", compressed, 26)
        offset = 30 + name_length + extra_length
        compressed_size = struct.unpack_from("<I", compressed, 18)[0]
        compressed[offset : offset + compressed_size] = b"\xff" * compressed_size
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, "w"):
            pass
        duplicate = json.dumps(record()).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
        for binary in (
            bytes(compressed),
            empty.getvalue(),
            zipped(raw=duplicate.encode()),
        ):
            self.evidence(binary)
            self.refuse(lambda: client.GitHubClient().artifact(RUN, "intent"))

    def test_hung_command_timeout_is_not_absence(self):
        self.replies({ROUTE: {"sleep": True}})
        with patch.object(client, "COMMAND_TIMEOUT_SECONDS", 0.05):
            error = self.refuse(
                lambda: client.GitHubClient().json(ROUTE), "command_timeout"
            )
        self.assertIsNotNone(error.returncode)

    def test_cleanup_failure_remains_fixed_lookup_error_without_child_data(self):
        fake = SimpleNamespace(pid=123, poll=lambda: None)
        with patch.object(
            client.os, "killpg", side_effect=PermissionError(1, "PRIVATE failure")
        ):
            error = self.refuse(
                lambda: client._retire(fake), "command_retirement_failed"
            )
        self.assertIsNone(error.returncode)

    def test_missing_gh_is_fixed_lookup_failure(self):
        with patch.dict(os.environ, {"PATH": str(self.root / "missing")}):
            self.refuse(
                lambda: client.GitHubClient().json(ROUTE), "command_unavailable"
            )


if __name__ == "__main__":
    unittest.main()
