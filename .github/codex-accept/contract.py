#!/usr/bin/env python3
"""ACR acceptance v1: bounded proof, producer export and authenticated consumption.

Stdlib only. Exit 1 refuses a contract, 2 denotes invocation/tool failure. Errors
never include subprocess output, untrusted JSON, or credential values. See
 docs/acr-codex-accept.md for the producer/consumer interfaces and ownership.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request
import zipfile

CONTRACT = "acr-credential-boundary/v1"
CENTRAL = "jbaruch/coding-policy"
ACR = "jbaruch/agentic-context-registry"
WORKFLOW = ".github/workflows/acr-codex-accept.yml"
MAX_FILES = 1000
MAX_TEXT = 8 * 1024 * 1024
MAX_TEXT_TOTAL = 64 * 1024 * 1024
MAX_BUNDLE = 128 * 1024 * 1024
MAX_OBJECTS = 100000
MAX_DECODED = 512 * 1024 * 1024
MAX_ARCHIVE = 2 * MAX_BUNDLE + MAX_TEXT_TOTAL
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"[1-9][0-9]*\Z")
RUNTIME_CASES = (
    "seed_proposal", "rotate_proposal", "rotate_patch", "rotate_stream_success",
    "rotate_stream_failure", "auth_uninspectable", "cancel_cleanup",
    "raw_integrity", "clean_rotation",
)
RUNTIME_PACKAGE = f"github.com/{ACR}/internal/producerconvert"
CLI_PACKAGE = f"github.com/{ACR}/cmd/acr"
PROOF_COMMAND = ["go", "test", "-race", "-count=1", "-json", "-timeout", "10m",
                 "-run", "^TestCodexCredentialBoundary(Runtime|CLI)$",
                 "./internal/producerconvert", "./cmd/acr"]
COVERAGE = {"runtime": ["initial", "observed_refreshed"],
            "central_scanner": ["initial", "suite"],
            "refreshed_verification": "runtime-composition"}
FIXTURES = {
    "goc": {"key": "GOC", "upstream": "tesslio/good-oss-citizen",
            "upstream_sha": "f21fda887815af815979a4fea43a66eb5174ee3e",
            "repository": "jbaruch/acr-156-goc-validation", "version": "1.1.11",
            "package": "plugins/good-oss-citizen",
            "commands": [["python3", "tests/test_contribution_declaration.py"],
                         ["python3", "tests/test_install_gate_scaffold.py"],
                         ["python3", "tests/test_github_sh_envelope.py", "--repo",
                          "tesslio/good-oss-citizen", "--issue-number", "13",
                          "--pr-number", "12", "--file-path", "README.md"]]},
    "ffa": {"key": "FFA", "upstream": "jbaruch/frequent-flyer-advocate",
            "upstream_sha": "142babbb1e2bebc798eb42128ac2466f21b5131d",
            "repository": "jbaruch/acr-156-ffa-validation", "version": "0.9.38",
            "package": ".", "commands": [["bash", ".github/scripts/pre-publish-gate.sh"]]},
}
# Native-release digest pins are owned by the candidate's installer, reviewed
# monthly there. Parse its exact Linux-X64 case, never accept arbitrary versions.
INSTALLER_ROW = re.compile(
    r"^\s*([0-9]+\.[0-9]+\.[0-9]+)/codex-x86_64-unknown-linux-musl\) sha256=([0-9a-f]{64}) ;;$",
    re.MULTILINE,
)


class Refusal(Exception):
    """Untrusted or incomplete evidence; no accepted output may be emitted."""


class ToolFailure(Exception):
    """A required tool could not complete its operation."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise Refusal(message)


def exact(value: Any, keys: str) -> dict[str, Any]:
    require(type(value) is dict and set(value) == set(keys.split()),
            "Unexpected schema fields; regenerate evidence with the documented v1 interface")
    return value


def string(value: Any, pattern: re.Pattern[str]) -> str:
    require(type(value) is str and pattern.fullmatch(value), "Invalid identity; use the exact pinned value")
    return value


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key; regenerate the evidence")
        result[key] = value
    return result


def parse(data: bytes) -> Any:
    try:
        return json.loads(data, object_pairs_hook=no_duplicates,
                          parse_constant=lambda _: reject_constant())
    except (ValueError, UnicodeError) as exc:
        raise Refusal("Malformed JSON; regenerate the evidence") from exc


def reject_constant() -> None:
    raise Refusal("Non-finite JSON value; regenerate the evidence")


def encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def safe_name(value: Any) -> str:
    require(type(value) is str and value and "\\" not in value and
            not any(ord(c) < 32 for c in value), "Unsafe member name; refuse the artifact")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and
            all(p not in ("..", ".", "") for p in path.parts), "Unsafe member path; refuse the artifact")
    return value


def regular(path: Path, limit: int = MAX_TEXT) -> bytes:
    # Check every existing ancestor as well; resolve() alone hides symlinks.
    for entry in (path, *path.parents):
        require(not entry.is_symlink(), "Symlink in evidence path; refuse the artifact")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
            "Missing regular bounded evidence; regenerate without truncation")
    return path.read_bytes()


def write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(value)
    path.chmod(0o600)


def run(argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> bytes:
    process = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, check=False)
    if process.returncode:
        raise ToolFailure("Required command failed; inspect the credential-free local reproduction")
    return process.stdout


def git(root: Path, *args: str) -> bytes:
    return run(["git", "--no-replace-objects", "-C", str(root), *args], env={
        **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
    })


def checkout(root: Path, expected: str) -> None:
    string(expected, SHA)
    require(git(root, "rev-parse", "HEAD").decode().strip() == expected,
            "Checkout identity differs; check out the reviewed exact commit")
    require(not git(root, "status", "--porcelain", "--untracked-files=all"),
            "Checkout is dirty; use a fresh exact checkout")


def binding() -> dict[str, str]:
    result = {key: os.environ.get("ACR_ACCEPT_" + key.upper(), "")
              for key in ("acr_sha", "central_sha", "run_id", "run_attempt", "host")}
    for key in ("acr_sha", "central_sha"):
        string(result[key], SHA)
    for key in ("run_id", "run_attempt"):
        string(result[key], DECIMAL)
    require(result["host"] == "linux-amd64", "Hosted acceptance requires linux-amd64")
    return result


def inputs(acr_root: Path | None = None) -> dict[str, str]:
    values = {key: os.environ.get("INPUT_" + key.upper().replace("-", "_"), "") for key in
              ("phase", "acr-sha", "codex-version", "producer-run-id", "producer-run-attempt", "goc-source", "ffa-source")}
    require(values["phase"] in ("convert", "consume"), "Select convert or consume")
    string(values["acr-sha"], SHA)
    if values["phase"] == "convert":
        require(all(not values[key] for key in ("producer-run-id", "producer-run-attempt", "goc-source", "ffa-source")),
                "Convert forbids producer and source inputs")
        require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", values["codex-version"]), "Supply a pinned Codex release")
        if acr_root is not None:
            checkout(acr_root, values["acr-sha"])
            pins = dict(INSTALLER_ROW.findall(regular(acr_root / ".github/scripts/install-codex.sh").decode()))
            require(values["codex-version"] in pins, "Codex release is absent from candidate installer digest table")
            values["archive_sha256"] = pins[values["codex-version"]]
    else:
        for key in ("producer-run-id", "producer-run-attempt"):
            string(values[key], DECIMAL)
        for key, fixture in FIXTURES.items():
            require(values[key + "-source"] == f"github:{fixture['repository']}@v{fixture['version']}",
                    "Consume requires the two exact published validation sources")
    return values


def leaves() -> list[dict[str, str]]:
    return [{"package": RUNTIME_PACKAGE, "name": f"TestCodexCredentialBoundaryRuntime/{platform}/{case}", "result": "passed"}
            for platform in ("darwin", "linux") for case in RUNTIME_CASES] + [
        {"package": CLI_PACKAGE, "name": "TestCodexCredentialBoundaryCLI/" + case, "result": "passed"}
        for case in ("rotated_refusal", "rotated_clean")]


def parse_events(data: bytes, packages: dict[str, set[str]], exit_code: int) -> None:
    require(exit_code == 0 and data and len(data) <= MAX_TEXT_TOTAL, "Test process failed or exceeded proof bounds")
    package_states: dict[str, str] = {}
    test_states: dict[tuple[str, str], str] = {}
    required = {(package, name) for package, names in packages.items() for name in names}
    allowed = set(required)
    for package, name in required:
        parts = name.split("/")
        allowed.update((package, "/".join(parts[:i])) for i in range(1, len(parts)))
    for line in data.splitlines():
        require(line and len(line) <= MAX_TEXT, "Invalid Go event line")
        event = parse(line)
        require(type(event) is dict, "Malformed Go test event")
        package, action, name = event.get("Package"), event.get("Action"), event.get("Test")
        require(package in packages and action in ("start", "run", "pause", "cont", "pass", "output"),
                "Unknown, failed or skipped proof event; candidate must implement the complete test contract")
        require(package_states.get(package) != "pass", "Event follows package completion")
        if name is None:
            if action == "start":
                require(package not in package_states, "Duplicate package start")
                package_states[package] = "start"
            else:
                require(package_states.get(package) == "start" and action in ("output", "pass"), "Incomplete package event sequence")
                if action == "pass":
                    require(all(test_states.get(item) == "pass" for item in allowed if item[0] == package),
                            "Package passed without every required test execution")
                    package_states[package] = "pass"
        else:
            identity = (package, name)
            require(identity in allowed and package_states.get(package) == "start", "Unexpected test name or package state")
            current = test_states.get(identity)
            if action == "run":
                require(current is None, "Duplicate test execution")
                test_states[identity] = "run"
            elif action == "pass":
                require(current in ("run", "cont"), "Duplicate or missing test completion")
                test_states[identity] = "pass"
            elif action == "pause":
                require(current in ("run", "cont"), "Invalid pause event")
                test_states[identity] = "pause"
            elif action == "cont":
                require(current == "pause", "Invalid continuation event")
                test_states[identity] = "cont"
            else:
                require(action == "output" and current in ("run", "cont", "pause"), "Output outside running test")
    require(set(package_states) == set(packages) and all(s == "pass" for s in package_states.values()) and
            all(test_states.get(item) == "pass" for item in allowed), "Incomplete proof inventory")


def proof_packages() -> dict[str, set[str]]:
    return {package: {item["name"] for item in leaves() if item["package"] == package}
            for package in (RUNTIME_PACKAGE, CLI_PACKAGE)}


def prove_runtime(acr_root: Path, events: Path, exit_code: int, output: Path) -> dict[str, Any]:
    context = binding()
    checkout(acr_root, context["acr_sha"])
    data = regular(events, MAX_TEXT_TOTAL)
    parse_events(data, proof_packages(), exit_code)
    proof = {"schema_version": 1, "contract": CONTRACT, "result": "passed", **context,
             "command": PROOF_COMMAND, "exit_code": 0, "events_sha256": sha(data),
             "tests": leaves(), "coverage": COVERAGE}
    write_new(output, encoded(proof))
    return proof


def validate_proof(proof: Any, context: dict[str, str]) -> None:
    exact(proof, "schema_version contract result acr_sha central_sha run_id run_attempt host command exit_code events_sha256 tests coverage")
    require(type(proof["schema_version"]) is int and proof["schema_version"] == 1 and
            proof["contract"] == CONTRACT and proof["result"] == "passed" and
            type(proof["exit_code"]) is int and proof["exit_code"] == 0 and
            proof["command"] == PROOF_COMMAND and proof["coverage"] == COVERAGE,
            "Runtime proof contract differs; rerun the mandatory proof")
    require(all(proof[key] == value for key, value in context.items()), "Runtime proof has stale or foreign provenance")
    string(proof["events_sha256"], DIGEST)
    require(type(proof["tests"]) is list and len(proof["tests"]) == len(leaves()), "Runtime proof inventory is incomplete")
    for item in proof["tests"]:
        exact(item, "package name result")
    require(sorted(proof["tests"], key=lambda x: (x["package"], x["name"])) ==
            sorted(leaves(), key=lambda x: (x["package"], x["name"])), "Runtime proof inventory differs")


def run_proof(acr_root: Path, root: Path) -> None:
    context = binding()
    checkout(acr_root, context["acr_sha"])
    private = root / "proof-private"
    private.mkdir(mode=0o700)
    for name in ("home", "codex", "state", "tmp"):
        (private / name).mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ACR_CODEX_", "CODEX_", "OPENAI_"))
           and k not in ("GH_TOKEN", "GITHUB_TOKEN", "CODEX_AUTH_JSON")}
    env.update(HOME=str(private / "home"), CODEX_HOME=str(private / "codex"),
               ACR_STATE_HOME=str(private / "state"), TMPDIR=str(private / "tmp"),
               PYTHONDONTWRITEBYTECODE="1")
    events = private / "events.jsonl"
    # Stream into an exclusive private file, never candidate stdout on CI logs.
    with events.open("xb") as handle:
        result = subprocess.run(PROOF_COMMAND, cwd=acr_root, env=env, stdout=handle,
                                stderr=subprocess.PIPE, check=False)
    checkout(acr_root, context["acr_sha"])
    prove_runtime(acr_root, events, result.returncode, root / "evidence/credential-boundary.json")


def credentials(auth: Path, suite: str) -> list[bytes]:
    document = parse(regular(auth))
    require(type(document) is dict and type(document.get("tokens")) is dict and
            not document.get("OPENAI_API_KEY"), "Subscription auth must contain tokens; API-key fallback is forbidden")
    values = [v.encode() for v in document["tokens"].values() if type(v) is str and len(v) >= 16]
    require(values and suite and suite != "journey-fixture-token", "Seed credentials and actual original-suite token are required")
    return [*values, suite.encode()]


def scan(data: bytes, known: list[bytes]) -> None:
    require(known and all(known), "Credential oracle is missing; refuse export")
    require(not any(value in data for value in known), "Credential material detected; refuse export without redaction")


def seed(root: Path) -> None:
    validate_proof(parse(regular(root / "evidence/credential-boundary.json")), binding())
    raw = os.environ.get("CODEX_AUTH_JSON", "")
    require(raw, "Central CODEX_AUTH_JSON is empty; use existing credential maintenance")
    document = parse(raw.encode())
    require(type(document) is dict and type(document.get("tokens")) is dict and
            any(type(v) is str and len(v) >= 16 for v in document["tokens"].values()) and
            not document.get("OPENAI_API_KEY"), "Valid subscription auth is required")
    auth = root / "seed/auth.json"
    write_new(auth, encoded(document))
    # Existing central masking helper; it emits only Actions mask commands.
    subprocess.run(["bash", str(Path(__file__).resolve().parents[1] / "codex-review/mask-secrets.sh"), str(auth)], check=True)


def inventory(repo: Path, revision: str) -> list[dict[str, Any]]:
    result = []
    total = 0
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        require(kind == "blob" and mode in ("100644", "100755", "120000"), "Unsupported Git tree entry")
        total += int(git(repo, "cat-file", "-s", oid))
        require(total <= MAX_DECODED and len(result) < MAX_OBJECTS, "Tree inventory exceeds decoded bounds")
        data = git(repo, "cat-file", "blob", oid)
        result.append({"path": safe_name(name.decode()), "mode": mode, "sha256": sha(data)})
    return sorted(result, key=lambda x: x["path"])


def suite_counts(key: str, index: int, data: bytes) -> list[int]:
    text = data.decode("utf-8")
    if key == "goc":
        patterns = [r"^PASS all (\d+) classification cases \+ CLI/input/error checks$",
                    r"^All (\d+) installer-script tests passed$",
                    r"^All (\d+) commands \+ 1 negative path emitted valid envelopes against tesslio/good-oss-citizen$"]
        matches = re.findall(patterns[index], text, re.MULTILINE)
        minimum = (15, 16, 23)[index]
        require(len(matches) == 1 and int(matches[0]) >= minimum, "Original GOC suite coverage is missing or diminished")
        return [int(matches[0])]
    summaries = re.findall(r"^(\d+)/(\d+) passed$", text, re.MULTILINE)
    matches = [int(passed) for passed, total in summaries if passed == total]
    require(len(summaries) == len(matches) == 3 and all(count >= minimum for count, minimum in zip(matches, (19, 114, 51))) and
            re.search(r"^pyright 1\.1\.411$", text, re.MULTILINE) and
            "0 errors, 0 warnings, 0 informations" in text and
            re.search(r"^All gates passed\.$", text, re.MULTILINE), "Original FFA gate or pinned diagnostics did not pass")
    return matches


def boundary(value: Any) -> None:
    exact(value, "contract authInspected proposalChecked reportSanitized isolatedHomeRemoved refreshObserved")
    require(value["contract"] == CONTRACT and type(value["refreshObserved"]) is bool and
            all(value[key] is True for key in ("authInspected", "proposalChecked", "reportSanitized", "isolatedHomeRemoved")),
            "Live run credential boundary is incomplete")


def fixture_receipt(key: str, receipt: Any, files: dict[str, bytes], context: dict[str, str]) -> None:
    fixture = FIXTURES[key]
    exact(receipt, "schema_version key result acr_sha upstream_sha producer_sha tree_sha repository version source_root operations commands inventories checks credential_boundary")
    require(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1 and receipt["result"] == "passed" and
            receipt["acr_sha"] == context["acr_sha"] and all(receipt[field] == fixture[field] for field in ("key", "upstream_sha", "repository", "version")),
            "Fixture receipt has wrong source, target, version or outcome")
    for field in ("producer_sha", "tree_sha"):
        string(receipt[field], SHA)
    require(receipt["producer_sha"] != receipt["upstream_sha"], "Fixture did not produce a generated commit")
    exact(receipt["inventories"], "baseline converted")
    exact(receipt["checks"], "deterministic_refusal source_preserved delta_validated validate rerun clean")
    require(all(value is True for value in receipt["checks"].values()), "Incomplete fixture checks")
    guard = exact(receipt["credential_boundary"], "contract plan_checked runs")
    require(guard["contract"] == CONTRACT and guard["plan_checked"] is True and type(guard["runs"]) is list, "Fixture boundary record missing")
    source_root = receipt["source_root"]
    require(type(source_root) is str and source_root.startswith("/") and
            source_root.endswith("/fixtures/" + key) and ".." not in PurePosixPath(source_root).parts,
            "Fixture source root is not the fixed orchestration checkout")
    package_root = str(PurePosixPath(source_root) / fixture["package"])
    migration = ["migrate", "tessl-plugin", package_root, "--acr-only", "--repository",
                 "https://github.com/" + fixture["repository"], "--json"]
    expected_operations = {
        "deterministic-dry-run": (migration + ["--dry-run"], 1),
        "dry-run": (migration + ["--agent", "codex", "--dry-run"], 0),
        "apply": (migration + ["--agent", "codex"], 0),
        "validate": (["validate", source_root, "--json"], 0),
        "rerun": (migration + ["--dry-run"], 0),
    }
    operations = receipt["operations"]
    require(type(operations) is list and len(operations) == len(expected_operations), "Missing CLI operation evidence")
    executable = None
    for operation, (name, (arguments, exit_code)) in zip(operations, expected_operations.items()):
        exact(operation, "name argv exit_code output sha256")
        argv = operation["argv"]
        require(type(argv) is list and len(argv) == len(arguments) + 1 and type(argv[0]) is str and
                argv[0].startswith("/") and PurePosixPath(argv[0]).name == "acr" and argv[1:] == arguments,
                "CLI operation argv differs from the required lane")
        if executable is None:
            executable = argv[0]
        require(argv[0] == executable and operation["name"] == name and type(operation["exit_code"]) is int and
                operation["exit_code"] == exit_code, "CLI operation identity or exit differs")
        member = f"evidence/{key}/{name}.json"
        require(operation["output"] == member and operation["sha256"] == sha(files[member]), "CLI operation evidence hash differs")
        envelope = parse(files[member])
        require(type(envelope) is dict and envelope.get("ok") is (exit_code == 0), "CLI operation envelope disagrees with exit")
        if name == "deterministic-dry-run":
            require(type(envelope.get("error")) is dict and envelope["error"].get("code") == "unsupported_semantic_conversion",
                    "Deterministic control did not refuse semantic conversion")
        if name == "rerun":
            require(type(envelope.get("result")) is dict and envelope["result"].get("current") is True and
                    not envelope["result"].get("agentRuns"), "Provider-free inert rerun evidence is missing")
    expected_runs = []
    for phase in ("dry-run", "apply"):
        report = parse(files[f"evidence/{key}/{phase}.json"])
        require(type(report) is dict and report.get("ok") is True and type(report.get("result")) is dict, "Missing successful CLI report")
        report = report["result"]
        guard_report = exact(report.get("credentialBoundary"), "contract planChecked applicationChecked reportSanitized")
        require(guard_report == {"contract": CONTRACT, "planChecked": True,
                               "applicationChecked": phase == "apply", "reportSanitized": True} and
                type(guard_report["applicationChecked"]) is bool and
                guard_report["planChecked"] is True and guard_report["reportSanitized"] is True,
                "CLI report was not checked at the application boundary")
        require(report.get("wrote") is (phase == "apply"), "CLI report has wrong dry-run/apply outcome")
        runs = report.get("agentRuns")
        require(type(runs) is list and runs, "Fixture has no genuine agent runs")
        for index, agent in enumerate(runs):
            require(type(agent) is dict and agent.get("provider") == "codex" and
                    agent.get("runtimeVersion") and agent.get("isolation") and
                    not agent.get("failure"), "Incomplete or failed agent run")
            require(type(agent.get("arguments")) is list and agent["arguments"] and
                    all(type(arg) is str for arg in agent["arguments"]) and type(agent.get("stdout")) is str,
                    "Agent invocation or completion evidence is absent")
            string(agent.get("requestDigest"), re.compile(r"sha256:[0-9a-f]{64}\Z"))
            stream = [parse(line.encode()) for line in agent["stdout"].splitlines() if line.startswith("{")]
            types = [event.get("type") for event in stream if type(event) is dict]
            require(types.count("turn.started") == 1 and types.count("turn.completed") == 1 and
                    not any(kind in types for kind in ("turn.failed", "error")), "Agent completion evidence is absent or failed")
            boundary(agent.get("credentialBoundary"))
            expected_runs.append({"phase": phase, "index": index, "boundary": agent["credentialBoundary"]})
    for item in guard["runs"]:
        exact(item, "phase index boundary")
        require(type(item["index"]) is int, "Invalid live run index")
        boundary(item["boundary"])
    require(guard["runs"] == expected_runs, "Live run mapping is omitted, duplicated or reordered")
    commands = receipt["commands"]
    require(type(commands) is list and len(commands) == 2 * len(fixture["commands"]), "Missing original suite command")
    prior = []
    for phase in ("baseline", "converted"):
        for index, argv in enumerate(fixture["commands"]):
            command = commands[(0 if phase == "baseline" else len(fixture["commands"])) + index]
            exact(command, "phase argv exit_code output sha256 counts")
            name = f"evidence/{key}/{phase}-test-{index + 1}.log"
            require(command["phase"] == phase and command["argv"] == argv and type(command["exit_code"]) is int and
                    command["exit_code"] == 0 and command["output"] == name and command["sha256"] == sha(files[name]),
                    "Original suite command, result or evidence hash differs")
            counts = suite_counts(key, index, files[name])
            require(command["counts"] == counts and all(type(n) is int for n in command["counts"]), "Suite receipt counts disagree with original output")
            if phase == "baseline":
                prior.append(counts)
            else:
                require(len(counts) == len(prior[index]) and all(a >= b for a, b in zip(counts, prior[index])), "Original suite coverage decreased")


def evidence_names() -> set[str]:
    names = {"evidence/credential-boundary.json"}
    for key, fixture in FIXTURES.items():
        names.update(f"evidence/{key}/{name}" for name in ("fixture-result.json", "deterministic-dry-run.json", "dry-run.json", "apply.json", "validate.json", "rerun.json", "baseline-inventory.json", "converted-inventory.json"))
        names.update(f"evidence/{key}/{phase}-test-{i + 1}.log" for phase in ("baseline", "converted") for i in range(len(fixture["commands"])))
    return names


def validate_inventories(repo: Path, key: str, receipt: dict[str, Any], files: dict[str, bytes]) -> None:
    recorded = {}
    for phase, revision in (("baseline", receipt["upstream_sha"]), ("converted", receipt["producer_sha"])):
        name = f"evidence/{key}/{phase}-inventory.json"
        require(receipt["inventories"][phase] == {"path": name, "sha256": sha(files[name])}, "Inventory hash differs")
        entries = parse(files[name])
        require(entries == inventory(repo, revision), "Inventory differs from actual retained Git tree")
        recorded[phase] = {item["path"]: item for item in entries}
    protected = [p for p in recorded["baseline"] if p.startswith("tests/") or "/tests/" in p or
                 p.startswith(".github/scripts/") or p == ".github/requirements.txt"]
    require(protected, "Original test inventory is empty")
    require(all(recorded["converted"].get(path) == recorded["baseline"][path] for path in protected), "Original tests/gates changed bytes or modes")


def inspect_bundle(bundle: Path, fixture: dict[str, Any], scratch: Path, known: list[bytes] | None) -> Path:
    regular(bundle, MAX_BUNDLE)
    repo = scratch / bundle.stem
    repo.mkdir()
    git(repo, "init", "-q", "--bare")
    heads = git(repo, "bundle", "list-heads", str(bundle)).decode().splitlines()
    require(heads == [fixture["producer_sha"] + " " + fixture["bundle_ref"]], "Bundle contains unexpected refs or producer commit")
    git(repo, "bundle", "verify", str(bundle))
    git(repo, "fetch", "--no-tags", str(bundle), fixture["bundle_ref"] + ":" + fixture["bundle_ref"])
    require(git(repo, "rev-parse", fixture["bundle_ref"] + "^{tree}").decode().strip() == fixture["tree_sha"], "Bundle tree differs")
    git(repo, "merge-base", "--is-ancestor", fixture["upstream_sha"], fixture["producer_sha"])
    reachable = {line.split(b" ", 1)[0] for line in git(repo, "rev-list", "--objects", fixture["producer_sha"]).splitlines()}
    objects = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype) %(objectsize)").splitlines()
    require(len(objects) <= MAX_OBJECTS, "Git object count exceeds export bound")
    require({line.split()[0] for line in objects} == reachable, "Bundle contains objects outside producer closure")
    total = 0
    for line in objects:
        oid, kind, size = line.decode().split()
        require(kind in ("blob", "commit", "tree", "tag"), "Unknown Git object type")
        total += int(size)
        require(total <= MAX_DECODED, "Decoded Git objects exceed export bound")
    # Bounds are checked before decoding any individual object for the scan.
    for line in objects:
        oid, kind, _ = line.decode().split()
        data = git(repo, "cat-file", kind, oid)
        if known is not None:
            scan(data, known)
    return repo


def members(root: Path) -> dict[str, bytes]:
    require(root.is_dir() and not root.is_symlink(), "Artifact must be a regular directory")
    result = {}
    total = 0
    allowed = evidence_names() | {"manifest.json", "goc.bundle", "ffa.bundle"}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "Artifact contains a symlink directory")
            prefix = (Path(directory) / name).relative_to(root).as_posix() + "/"
            require(any(p.startswith(prefix) for p in allowed), "Artifact contains an unexpected directory")
        for name in names:
            path = Path(directory) / name
            relative = safe_name(path.relative_to(root).as_posix())
            require(relative in allowed, "Artifact contains an unexpected member")
            data = regular(path, MAX_BUNDLE if relative.endswith(".bundle") else MAX_TEXT)
            result[relative] = data
            if not relative.endswith(".bundle"):
                total += len(data)
            require(len(result) <= MAX_FILES and total <= MAX_TEXT_TOTAL, "Artifact exceeds inventory bounds")
    require(set(result) == allowed, "Artifact member inventory is incomplete")
    return result


def validate_manifest(manifest: Any, files: dict[str, bytes], context: dict[str, str]) -> None:
    exact(manifest, "schema_version phase result central_sha acr_sha run_id run_attempt run_url platform codex fixtures files credential_boundary")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1 and
            manifest["phase"] == "convert" and manifest["result"] == "passed" and
            manifest["platform"] == context["host"] and
            all(manifest[k] == context[k] for k in ("central_sha", "acr_sha", "run_id", "run_attempt")) and
            manifest["run_url"] == f"https://github.com/{CENTRAL}/actions/runs/{context['run_id']}" and
            manifest["credential_boundary"] == {"contract": CONTRACT, "proof": "evidence/credential-boundary.json"},
            "Manifest provenance or credential contract differs")
    codex = exact(manifest["codex"], "version archive_sha256 binary_sha256")
    require(type(codex["version"]) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", codex["version"]), "Invalid Codex release identity")
    for field in ("archive_sha256", "binary_sha256"):
        string(codex[field], DIGEST)
    expected_files = [{"path": name, "size": len(data), "sha256": sha(data)} for name, data in sorted(files.items()) if name != "manifest.json"]
    require(type(manifest["files"]) is list, "Invalid manifest file inventory")
    for item in manifest["files"]:
        exact(item, "path size sha256")
        require(type(item["size"]) is int, "Invalid member size")
    require(manifest["files"] == expected_files, "Manifest hashes, sizes or members differ")
    validate_proof(parse(files["evidence/credential-boundary.json"]), context)
    require(type(manifest["fixtures"]) is list and len(manifest["fixtures"]) == 2, "Exactly two fixture exports are required")
    for index, (key, spec) in enumerate(FIXTURES.items()):
        item = exact(manifest["fixtures"][index], "key upstream_sha producer_sha tree_sha repository version bundle bundle_ref bundle_sha256 receipt")
        require(all(item[k] == spec[k] for k in ("key", "upstream_sha", "repository", "version")) and
                item["bundle"] == key + ".bundle" and item["bundle_ref"] == "refs/heads/acr-accept-" + key and
                item["receipt"] == f"evidence/{key}/fixture-result.json" and item["bundle_sha256"] == sha(files[key + ".bundle"]), "Fixture export identity differs")
        receipt = parse(files[item["receipt"]])
        fixture_receipt(key, receipt, files, context)
        require(all(item[k] == receipt[k] for k in ("producer_sha", "tree_sha")), "Producer commit or tree differs from successful fixture")
        for phase in ("dry-run", "apply"):
            report = parse(files[f"evidence/{key}/{phase}.json"])["result"]
            require(all(run["runtimeVersion"] == "codex-cli " + codex["version"] for run in report["agentRuns"]), "Live runtime version differs from installed release")


def verify_artifact(root: Path, context: dict[str, str], known: list[bytes] | None = None) -> dict[str, Any]:
    files = members(root)
    manifest = parse(files["manifest.json"])
    validate_manifest(manifest, files, context)
    if known is not None:
        for name, data in files.items():
            if not name.endswith(".bundle"):
                scan(data, known)
    with tempfile.TemporaryDirectory(prefix="acr-import-", dir=root.parent) as name:
        scratch = Path(name)
        decoded = 0
        count = 0
        for key, fixture in zip(FIXTURES, manifest["fixtures"]):
            repo = inspect_bundle(root / fixture["bundle"], fixture, scratch, known)
            sizes = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectsize)").splitlines()
            decoded += sum(int(size) for size in sizes)
            count += len(sizes)
            require(decoded <= MAX_DECODED and count <= MAX_OBJECTS, "Combined Git closure exceeds export bounds")
            validate_inventories(repo, key, parse(files[fixture["receipt"]]), files)
    return manifest


def seal(root: Path, evidence: Path, output: Path) -> dict[str, Any]:
    context = binding()
    known = credentials(root / "seed/auth.json", os.environ.get("GH_TOKEN", ""))
    require(not output.exists() and not output.is_symlink(), "Export destination must be fresh")
    validate_proof(parse(regular(evidence / "credential-boundary.json")), context)
    # Only an explicit fixed projection is copied, never raw evidence recursion.
    with tempfile.TemporaryDirectory(prefix="acr-seal-", dir=output.parent) as name:
        staging = Path(name)
        files = {}
        for member in sorted(evidence_names()):
            relative = member.removeprefix("evidence/")
            data = regular(evidence / relative)
            scan(data, known)
            files[member] = data
            write_new(staging / member, data)
        fixtures = []
        for key, spec in FIXTURES.items():
            receipt = parse(files[f"evidence/{key}/fixture-result.json"])
            fixture_receipt(key, receipt, files, context)
            repo = root / "fixtures" / key
            require(not repo.is_symlink() and receipt["source_root"] == str(repo), "Producer root must be orchestration-owned")
            checkout(repo, receipt["producer_sha"])
            require(git(repo, "rev-parse", "HEAD^{tree}").decode().strip() == receipt["tree_sha"], "Producer tree differs")
            git(repo, "merge-base", "--is-ancestor", spec["upstream_sha"], receipt["producer_sha"])
            validate_inventories(repo, key, receipt, files)
            ref = "refs/heads/acr-accept-" + key
            git(repo, "update-ref", ref, receipt["producer_sha"])
            bundle = staging / (key + ".bundle")
            git(repo, "bundle", "create", str(bundle), ref)
            data = regular(bundle, MAX_BUNDLE)
            files[key + ".bundle"] = data
            fixtures.append({field: receipt[field] for field in ("key", "upstream_sha", "producer_sha", "tree_sha", "repository", "version")})
            fixtures[-1].update(bundle=key + ".bundle", bundle_ref=ref, bundle_sha256=sha(data), receipt=f"evidence/{key}/fixture-result.json")
        codex = parse(regular(root / "codex.json"))
        manifest = {"schema_version": 1, "phase": "convert", "result": "passed",
                    **{k: context[k] for k in ("acr_sha", "central_sha", "run_id", "run_attempt")},
                    "run_url": f"https://github.com/{CENTRAL}/actions/runs/{context['run_id']}",
                    "platform": context["host"], "codex": codex, "fixtures": fixtures,
                    "credential_boundary": {"contract": CONTRACT, "proof": "evidence/credential-boundary.json"},
                    "files": [{"path": path, "size": len(data), "sha256": sha(data)} for path, data in sorted(files.items())]}
        write_new(staging / "manifest.json", encoded(manifest))
        verify_artifact(staging, context, known)
        scanner = Path(__file__).resolve().parents[1] / "codex-review/assert-no-secret-leak.sh"
        for path in sorted(evidence_names() | {"manifest.json"}):
            # Presence already enforced. A scanner exception/nonzero aborts export.
            run(["bash", str(scanner), str(root / "seed/auth.json"), str(staging / path)])
        staging.rename(output)
    return manifest


def artifact_name(context: dict[str, str]) -> str:
    return f"acr-accept-convert-{context['acr_sha']}-{context['run_id']}-{context['run_attempt']}"


def provenance(run_info: Any, jobs: Any, artifacts: Any, workflow: Any,
               acr_sha: str, run_id: str, attempt: str) -> tuple[dict[str, str], dict[str, Any]]:
    for value in (run_id, attempt):
        string(value, DECIMAL)
    string(acr_sha, SHA)
    require(type(run_info) is dict and type(workflow) is dict and workflow.get("path") == WORKFLOW and
            run_info.get("workflow_id") == workflow.get("id") and run_info.get("path") == WORKFLOW and
            str(run_info.get("id")) == run_id and str(run_info.get("run_attempt")) == attempt and
            run_info.get("event") == "workflow_dispatch" and run_info.get("status") == "completed" and
            run_info.get("conclusion") == "success" and run_info.get("repository", {}).get("full_name") == CENTRAL and
            run_info.get("head_repository", {}).get("full_name") == CENTRAL, "Producer run is not the exact successful central dispatch")
    context = {"acr_sha": acr_sha, "central_sha": string(run_info.get("head_sha"), SHA),
               "run_id": run_id, "run_attempt": attempt, "host": "linux-amd64"}
    require(type(jobs) is list and type(artifacts) is list, "Malformed producer API inventory")
    converts = [job for job in jobs if job.get("name") == "convert"]
    require(len(converts) == 1 and converts[0].get("status") == "completed" and converts[0].get("conclusion") == "success" and
            str(converts[0].get("run_id")) == run_id and str(converts[0].get("run_attempt")) == attempt and
            converts[0].get("head_sha") == context["central_sha"], "Producer convert job provenance differs")
    selected = [artifact for artifact in artifacts if artifact.get("name") == artifact_name(context)]
    require(len(selected) == 1, "Producer artifact name is missing or ambiguous")
    artifact = selected[0]
    require(artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int and
            0 < artifact["size_in_bytes"] <= MAX_ARCHIVE and type(artifact.get("id")) is int and artifact["id"] > 0 and
            artifact.get("workflow_run", {}).get("id") == int(run_id) and
            artifact.get("workflow_run", {}).get("head_sha") == context["central_sha"] and
            type(artifact.get("digest")) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"]),
            "Artifact is expired, oversized or has foreign provenance")
    return context, artifact


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def api(path: str) -> Any:
    token = os.environ.get("GH_TOKEN", "")
    require(token, "Existing Actions-read GitHub authentication is required")
    request = urllib.request.Request("https://api.github.com/repos/" + CENTRAL + path,
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
        data = response.read(MAX_TEXT + 1)
    require(len(data) <= MAX_TEXT, "GitHub metadata exceeds bound")
    return parse(data)


def pages(path: str, key: str) -> list[Any]:
    result = []
    for page in range(1, 11):
        data = api(f"{path}?per_page=100&page={page}")
        require(type(data) is dict and type(data.get(key)) is list, "GitHub inventory response is malformed")
        result.extend(data[key])
        if len(result) == data.get("total_count"):
            return result
        require(data[key], "GitHub inventory is incomplete")
    raise Refusal("GitHub inventory exceeds bound; do not select an arbitrary artifact")


def producer_context(acr_sha: str, run_id: str, attempt: str) -> tuple[dict[str, str], dict[str, Any]]:
    string(acr_sha, SHA)
    string(run_id, DECIMAL)
    string(attempt, DECIMAL)
    return provenance(api(f"/actions/runs/{run_id}/attempts/{attempt}"),
                      pages(f"/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"),
                      pages(f"/actions/runs/{run_id}/artifacts", "artifacts"),
                      api("/actions/workflows/acr-codex-accept.yml"), acr_sha, run_id, attempt)


def extract_archive(data: bytes, destination: Path) -> None:
    require(len(data) <= MAX_ARCHIVE and not destination.exists(), "Download must be bounded and destination fresh")
    allowed = evidence_names() | {"manifest.json", "goc.bundle", "ffa.bundle"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(len(entries) <= MAX_FILES, "Archive member bound exceeded")
        names = [safe_name(item.filename) for item in entries]
        require(len(set(names)) == len(names) and set(names) == allowed, "Archive members are duplicated, unsafe or incomplete")
        total = 0
        for item in entries:
            mode = item.external_attr >> 16
            require(not item.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG) and not item.flag_bits & 1,
                    "Archive contains an encrypted or nonregular member")
            limit = MAX_BUNDLE if item.filename.endswith(".bundle") else MAX_TEXT
            require(item.file_size <= limit, "Uncompressed archive member exceeds bound")
            total += item.file_size
        require(total <= MAX_ARCHIVE, "Uncompressed archive exceeds bound")
        destination.mkdir(mode=0o700)
        for item in entries:
            write_new(destination / item.filename, archive.read(item))


def download(acr_sha: str, run_id: str, attempt: str, destination: Path) -> dict[str, Any]:
    context, artifact = producer_context(acr_sha, run_id, attempt)
    token = os.environ.get("GH_TOKEN", "")
    request = urllib.request.Request(f"https://api.github.com/repos/{CENTRAL}/actions/artifacts/{artifact['id']}/zip",
                                     headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            data = response.read(MAX_ARCHIVE + 1)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise ToolFailure("Artifact download failed; verify existing Actions-read access") from exc
        location = exc.headers.get("Location", "")
        require(location.startswith("https://"), "Artifact redirect is not HTTPS")
        # Signed artifact endpoint gets no GitHub token, even across redirects.
        with urllib.request.urlopen(location, timeout=60) as response:
            data = response.read(MAX_ARCHIVE + 1)
    require(sha(data) == artifact["digest"].removeprefix("sha256:"), "Downloaded artifact digest differs from GitHub identity")
    extract_archive(data, destination)
    manifest = verify_artifact(destination, context)
    return {"schema_version": 1, "result": "passed", "artifact_id": artifact["id"],
            "artifact_digest": artifact["digest"], "manifest_sha256": sha(encoded(manifest)), **context}


def prepare(root: Path) -> None:
    require(not root.exists() and not root.is_symlink(), "Run root must be fresh")
    root.mkdir(mode=0o700)
    for name in ("fixtures", "evidence", "tmp", "homes"):
        (root / name).mkdir(mode=0o700)
    for key, fixture in FIXTURES.items():
        target = root / "fixtures" / key
        run(["git", "-c", "credential.helper=", "clone", "--no-checkout", "https://github.com/" + fixture["upstream"] + ".git", str(target)])
        git(target, "checkout", "--detach", fixture["upstream_sha"])
        checkout(target, fixture["upstream_sha"])
        git(target, "config", "user.name", "ACR acceptance")
        git(target, "config", "user.email", "acr-accept@users.noreply.github.com")


def installed(acr_root: Path, root: Path) -> None:
    values = inputs(acr_root)
    version = values["codex-version"]
    binary = Path(os.environ.get("ACR_CODEX_RELEASE_BIN", ""))
    archive = Path(os.environ["RUNNER_TEMP"]) / ("codex-" + version + ".tar.gz")
    require(sha(regular(archive, MAX_BUNDLE)) == values["archive_sha256"], "Installed archive digest differs from candidate pin")
    binary_data = regular(binary, MAX_DECODED)
    require(binary_data.startswith(b"\x7fELF") and run([str(binary), "--version"]).decode().strip() == "codex-cli " + version,
            "Codex native executable or version differs")
    write_new(root / "codex.json", encoded({"version": version, "archive_sha256": values["archive_sha256"], "binary_sha256": sha(binary_data)}))


def convert(acr_root: Path, root: Path) -> None:
    context = binding()
    checkout(acr_root, context["acr_sha"])
    validate_proof(parse(regular(root / "evidence/credential-boundary.json")), context)
    credentials(root / "seed/auth.json", os.environ.get("GH_TOKEN", ""))
    env = {k: v for k, v in os.environ.items() if k not in ("CODEX_AUTH_JSON", "CODEX_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN")}
    env.update(CODEX_HOME=str(root / "seed"), TMPDIR=str(root / "tmp"),
               ACR_STATE_HOME=str(root / "homes"), ACR_CODEX_LIVE="1", ACR_CODEX_LIVE_REQUIRED="1",
               ACR_CODEX_LIVE_EVIDENCE=str(root / "evidence"), PYTHONDONTWRITEBYTECODE="1")
    for key, fixture in FIXTURES.items():
        prefix = "ACR_CODEX_LIVE_" + key.upper()
        env[prefix] = str(root / "fixtures" / key)
        env[prefix + "_SHA"] = fixture["upstream_sha"]
        env[prefix + "_REPOSITORY"] = "https://github.com/" + fixture["repository"]
    events = root / "conversion-private.jsonl"
    with events.open("xb") as handle:
        result = subprocess.run(["go", "test", "-race", "-count=1", "-json", "-timeout", "140m", "-run",
                                 "^TestCodexLiveUpstreamConversion$", "./cmd/acr"], cwd=acr_root, env=env,
                                stdout=handle, stderr=subprocess.PIPE, check=False)
    parse_events(regular(events, MAX_TEXT_TOTAL), {CLI_PACKAGE: {"TestCodexLiveUpstreamConversion/" + key.upper() for key in FIXTURES}}, result.returncode)
    checkout(acr_root, context["acr_sha"])


def consumer_receipt(value: Any, manifest: dict[str, Any], context: dict[str, str]) -> None:
    exact(value, "schema_version result acr_sha central_sha producer_run_id producer_run_attempt host fixtures")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["result"] == "passed" and
            value["acr_sha"] == context["acr_sha"] and value["central_sha"] == context["central_sha"] and
            value["producer_run_id"] == context["run_id"] and value["producer_run_attempt"] == context["run_attempt"] and
            value["host"] == "linux-amd64" and type(value["fixtures"]) is list and len(value["fixtures"]) == 2,
            "Consumer receipt has wrong producer or build provenance")
    for fixture, produced in zip(value["fixtures"], manifest["fixtures"]):
        exact(fixture, "key source release consumers sha_install")
        source = "github:" + produced["repository"]
        require(fixture["key"] == produced["key"] and fixture["source"] == source + "@v" + produced["version"],
                "Consumer substituted a local or foreign source")
        release = exact(fixture["release"], "id tag commit contentHash")
        require(type(release["id"]) is int and release["id"] > 0 and release["tag"] == "v" + produced["version"] and
                release["commit"] == produced["producer_sha"], "Published release or peeled tag moved from producer commit")
        string(release["contentHash"], re.compile(r"sha256:[0-9a-f]{64}\Z"))
        consumers = fixture["consumers"]
        require(type(consumers) is list and len(consumers) == 3, "All three native consumers are required")
        for consumer, adapter in zip(consumers, ("claude-code", "codex", "cursor")):
            exact(consumer, "adapter kind commit release_id tag contentHash install realize check declared_inventory native_inventory")
            require(consumer["adapter"] == adapter and consumer["kind"] == "release" and
                    consumer["commit"] == produced["producer_sha"] and type(consumer["release_id"]) is int and consumer["release_id"] == release["id"] and
                    consumer["tag"] == release["tag"] and consumer["contentHash"] == release["contentHash"] and
                    all(consumer[k] is True for k in ("install", "realize", "check")), "Consumer lock does not identify the real producer release")
            require(type(consumer["declared_inventory"]) is list and consumer["declared_inventory"] and
                    consumer["declared_inventory"] == consumer["native_inventory"], "Native inventory does not match adapter declarations")
            names = set()
            for item in consumer["native_inventory"]:
                exact(item, "path sha256 mode")
                name = safe_name(item["path"])
                require(name not in names and item["mode"] in ("100644", "100755"), "Native inventory has duplicate path or wrong mode")
                names.add(name)
                string(item["sha256"], DIGEST)
        pinned = exact(fixture["sha_install"], "source commit contentHash install")
        require(pinned == {"source": source + "@" + produced["producer_sha"], "commit": produced["producer_sha"],
                           "contentHash": release["contentHash"], "install": True} and pinned["install"] is True,
                "Fresh SHA install differs from the published producer")


def consume(acr_root: Path, artifact: Path, root: Path) -> None:
    values = inputs(acr_root)
    context, _ = producer_context(values["acr-sha"], values["producer-run-id"], values["producer-run-attempt"])
    manifest = verify_artifact(artifact, context)
    checkout(acr_root, context["acr_sha"])
    root.mkdir(mode=0o700)
    for name in ("home", "tmp", "state", "evidence"):
        (root / name).mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "ACR_CODEX_")) and k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    env.update(HOME=str(root / "home"), TMPDIR=str(root / "tmp"), ACR_STATE_HOME=str(root / "state"),
               ACR_CODEX_CONSUME_REQUIRED="1", ACR_CODEX_CONSUME_MANIFEST=str(artifact / "manifest.json"),
               ACR_CODEX_CONSUME_EVIDENCE=str(root / "evidence"),
               ACR_CODEX_CONSUME_GOC_SOURCE=values["goc-source"], ACR_CODEX_CONSUME_FFA_SOURCE=values["ffa-source"])
    events = root / "events.jsonl"
    with events.open("xb") as handle:
        result = subprocess.run(["go", "test", "-race", "-count=1", "-json", "-timeout", "25m", "-run",
                                 "^TestCodexLivePublishedConsumption$", "./cmd/acr"], cwd=acr_root, env=env,
                                stdout=handle, stderr=subprocess.PIPE, check=False)
    parse_events(regular(events, MAX_TEXT_TOTAL), {CLI_PACKAGE: {"TestCodexLivePublishedConsumption/" + key.upper() for key in FIXTURES}}, result.returncode)
    checkout(acr_root, context["acr_sha"])
    consumer_receipt(parse(regular(root / "evidence/consumer-result.json")), manifest, context)


def clean(root: Path) -> None:
    require(root.name in ("acr-accept", "acr-consume") and not root.is_symlink(), "Cleanup root is not an owned acceptance directory")
    if root.exists():
        shutil.rmtree(root)
    require(not root.exists(), "Cleanup did not remove private runtime files; refuse upload")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inputs", "prepare", "proof-run", "seed", "installed", "convert", "seal", "verify", "download", "consume", "clean", "prove-runtime"):
        command = commands.add_parser(name)
        if name in ("inputs", "proof-run", "installed", "convert", "consume", "prove-runtime"):
            command.add_argument("--acr-root", type=Path, required=name != "inputs")
        if name in ("prepare", "proof-run", "seed", "installed", "convert", "seal", "consume", "clean"):
            command.add_argument("--run-root", type=Path, required=True)
        if name == "seal":
            command.add_argument("--evidence", type=Path, required=True)
        if name in ("seal", "prove-runtime"):
            command.add_argument("--output", type=Path, required=True)
        if name == "prove-runtime":
            command.add_argument("--events", type=Path, required=True)
            command.add_argument("--exit-code", type=int, required=True)
        if name in ("verify", "download", "consume"):
            command.add_argument("--artifact", type=Path, required=True)
        if name in ("verify", "download"):
            for flag in ("acr-sha", "run-id", "run-attempt"):
                command.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    try:
        result: Any = None
        if args.command == "inputs":
            result = inputs(args.acr_root)
        elif args.command == "prepare":
            prepare(args.run_root)
        elif args.command == "proof-run":
            run_proof(args.acr_root, args.run_root)
        elif args.command == "prove-runtime":
            result = prove_runtime(args.acr_root, args.events, args.exit_code, args.output)
        elif args.command == "seed":
            seed(args.run_root)
        elif args.command == "installed":
            installed(args.acr_root, args.run_root)
        elif args.command == "convert":
            convert(args.acr_root, args.run_root)
        elif args.command == "seal":
            result = seal(args.run_root, args.evidence, args.output)
        elif args.command == "download":
            result = download(args.acr_sha, args.run_id, args.run_attempt, args.artifact)
        elif args.command == "verify":
            context, _ = producer_context(args.acr_sha, args.run_id, args.run_attempt)
            result = verify_artifact(args.artifact, context)
        elif args.command == "consume":
            consume(args.acr_root, args.artifact, args.run_root)
        elif args.command == "clean":
            clean(args.run_root)
        payload = {"schema_version": 1, "result": "passed", "command": args.command}
        if args.command == "inputs":
            payload["inputs"] = result
        elif args.command == "download":
            payload.update(result)
        print(json.dumps(payload))
        return 0
    except Refusal as exc:
        print("ACR acceptance refused: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ToolFailure, zipfile.BadZipFile) as exc:
        # Fixed diagnostics: exception messages may contain credentials or raw candidate output.
        print("ACR acceptance tool failure (" + type(exc).__name__ + "); inspect the credential-free reproduction", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
