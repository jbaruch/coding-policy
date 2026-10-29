#!/usr/bin/env python3
"""Materialize only the reviewed Core157 authority in a private review context.

Usage: materialize-core-authority.py DIRECTORY [--prepare-only]. DIRECTORY is
the central driver's owned non-workspace policy directory. Preparation performs
only registry metadata/report reads. Full mode installs Core alone in a fresh
private workspace, verifies every installed byte, then relocates that genuine
tree into the context after ordinary dependency installation. It executes no
plugin script and changes no global installer policy. Stdout is schema-1 JSON;
operational refusal exits2 with a fixed reason and actionable stderr.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


PLUGIN = "jbaruch/nanoclaw-core"
# Renew only through source review, monthly or on Core/CLI publication changes.
# This finite exception covers the audited157 package/report, never latest risk.
VERSION = "0.1.157"
FINGERPRINT = "57516bf5160c8fd04d5bcac9329f7ebfc07ba9c4d393d8c6ed2a5b0f245dab63"
REPORT_SHA256 = "9b5ddf8f67a93880dfdffe43212fc4345c80a7928ed58b80ca2f74a96c104e21"
REPORT_URL = (
    "https://api.tessl.io/v1/tiles/jbaruch/nanoclaw-core/versions/"
    + VERSION
    + "/skill-security-reviews"
)
MAX_RESPONSE = 256 * 1024
EXPECTED_FILES = {
    ".tessl-plugin/plugin.json": "e0e884b1e6cde6cef730b1ae5c02559bc2698b4dbabe5c966d153435108b018e",
    "README.md": "77a3947018f2ee412aa1c31e1fb50f36a0c810fbee56421c469412a169c0db21",
    "rules/context-recovery.md": "e1bae936e642355bd473ddd05ee1ad00d44dcb42af9512eb72a5e8ec58e409e8",
    "rules/core-behavior.md": "bb549c0057de8f8308c3ad0ed0706c121ab49f3ffcf5fa78a928c764211df2d4",
    "rules/default-silence.md": "4ba5eb5cb35c967066b144af2c51f79534544ca05905888a3f0ba222cae599b3",
    "rules/ground-truth.md": "9a6e7c7091809cfbea6e5c7e791982100b91d58cce4c3a66c1deb32df821c1e1",
    "rules/language-matching.md": "c04c3761f3924f8b3ce7a979ff2652de2635f0efee5132cb075d9e6def26e198",
    "rules/post-compaction-trust.md": "27d8c2f435df610ebbc192803fd22c9d164a00a04d82f9c01b47b8aa1fea492c",
    "rules/query-size-limits.md": "98c982c5592e9bba05f18a38d64c1ebbf8f5b005f166d1ea8be82136efa425cc",
    "rules/read-full-content.md": "35e04219a1b27ce0a5b7bddd0624ae5a03739400f218167880286e8e5c5e218a",
    "rules/telegram-protocol.md": "7bc310534f0a3fe63f248c1006465f6b39a22a727959423a64615b13ee6ed7be",
    "rules/temporal-awareness.md": "0799a962a14c78c16a8d1cd09a11f6f8eadd9717775104097d0d5c9328908d4e",
    "rules/tone-matching.md": "670114dd6b1786e6cdd4fadb3a11c93d77c7165e90025e5d6eb1f07474b2fd77",
    "skills/current-tz/SKILL.md": "f76f1f1e4bc85308dc6ae436860ae78d2fc6a7221347becfa05680efd41be52b",
    "skills/current-tz/scripts/read-current-tz.py": "4411d0c8636d2a36b2932c39f48c9551921d70d69b75fcce78d6ad14bc69ae5a",
    "skills/now-vs-deadline/SKILL.md": "46ab6ef560e9678d9a557baee31e3370bd1a690698a4e1525502d41a84358e9d",
    "skills/now-vs-deadline/scripts/now-vs-deadline.py": "6ad002a3359fb37b5d861625225d1652df56c01e6ff1f10263412631970119c6",
    "skills/query-history/SKILL.md": "beac12474c491a37d5ad0a909a33c75d1047ad1d8fecda4e319d41def40e4397",
    "skills/query-history/scripts/query-message-history.py": "1a05c34579783e8cd4605ddd2749ae508c45e46d8a8526fe2ac201049a4e122e",
    "tessl-package.json": "8cad899bf0ddc6c0ff3ef4bb62d26f1bd763863634691f7af7393954983f81b0",
    "tile.json": "b62cad5197bcc02e4e022898446e34e266facae3e104e881ca8a59f7104dae22",
}


class MaterializationError(RuntimeError):
    """A fixed registry, report or materialized-context precondition refused."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def run_tool(arguments, directory):
    result = subprocess.run(
        ["tessl", *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode or len(result.stdout.encode()) > MAX_RESPONSE:
        raise MaterializationError("tessl_command_unavailable")
    return result.stdout


def fetch_report():
    # Use curl's verified system TLS path without reading user curl settings.
    # No redirects, retries, credentials or TLS-verification exceptions.
    result = subprocess.run(
        [
            "curl",
            "--disable",
            "--proto",
            "=https",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--max-filesize",
            str(MAX_RESPONSE),
            "--write-out",
            "\\n%{http_code}",
            REPORT_URL,
        ],
        capture_output=True,
        check=False,
        timeout=40,
    )
    if result.returncode or len(result.stdout) > MAX_RESPONSE + 4:
        raise MaterializationError("security_report_unavailable")
    body, separator, status = result.stdout.rpartition(b"\n")
    if not separator or status != b"200":
        raise MaterializationError("security_report_unavailable")
    return json.loads(body)


def prepare(directory):
    metadata = json.loads(run_tool(["plugin", "info", PLUGIN, "--json"], directory))
    if not isinstance(metadata, dict):
        raise MaterializationError("registry_identity_changed")
    tile, version = metadata.get("tile"), metadata.get("version")
    if not (
        isinstance(tile, dict)
        and tile.get("fullName") == PLUGIN
        and tile.get("latestVersion") == VERSION
        and tile.get("archived") is False
        and isinstance(version, dict)
        and version.get("version") == VERSION
        and version.get("fingerprint") == FINGERPRINT
        and version.get("moderationPassed") is True
        and version.get("moderationStatus") == "pass"
        and version.get("archived") is False
    ):
        raise MaterializationError("registry_identity_changed")
    report = fetch_report()
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    if digest(canonical) != REPORT_SHA256:
        raise MaterializationError("reviewed_security_report_changed")
    # The exact report limits W007/W011 to existing current-chat history reads;
    # current-tz and now-vs-deadline have no findings. No other warning is accepted.
    return {
        "schema_version": 1,
        "status": "prepared",
        "plugin": PLUGIN,
        "version": VERSION,
        "fingerprint": FINGERPRINT,
        "security_report_sha256": REPORT_SHA256,
    }


def private_directory(path):
    info = path.lstat()
    if not (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    ):
        raise MaterializationError("context_directory_not_private")
    return info.st_dev, info.st_ino


def contained_directory(path, root):
    for current in (path, *path.parents):
        info = current.lstat()
        if not (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_mode & 0o022 == 0
        ):
            raise MaterializationError("context_directory_binding_changed")
        if current == root:
            return
    raise MaterializationError("context_directory_escaped")


def verify_tree(provider, root):
    contained_directory(provider, root)
    found = {}
    for path in provider.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            contained_directory(path, root)
            continue
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_mode & 0o022 == 0
        ):
            raise MaterializationError("installed_provider_not_regular")
        contained_directory(path.parent, root)
        with path.open("rb") as source:
            data = source.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise MaterializationError("installed_provider_oversized")
        found[path.relative_to(provider).as_posix()] = digest(data)
    if found != EXPECTED_FILES:
        raise MaterializationError("installed_provider_bytes_changed")
    return found


def materialize(directory, prepare_only=False):
    identity = private_directory(directory)
    destination = directory / ".tessl/plugins/jbaruch/nanoclaw-core"
    if os.path.lexists(destination):
        raise MaterializationError("provider_destination_already_exists")
    proof = prepare(directory)
    if prepare_only:
        return proof
    # No other dependency manifest exists in this fresh workspace. Acceptance
    # cannot apply to an existing policy dependency or a plugin selected by PR data.
    with tempfile.TemporaryDirectory(prefix="core157-", dir=directory) as temporary:
        install = Path(temporary)
        # Anchor the install to this project even when the surrounding review
        # context has its own Tessl manifest and unrelated dependencies.
        descriptor = os.open(
            install / "tessl.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w") as project:
            json.dump({"mode": "managed", "dependencies": {}}, project)
        run_tool(
            [
                "install",
                PLUGIN + "@" + VERSION,
                "--yes",
                "--accept-warnings",
                "--strict",
                "--agent",
                "codex",
            ],
            install,
        )
        manifest = json.loads((install / "tessl.json").read_text())
        if not isinstance(manifest, dict) or manifest.get("dependencies") != {
            PLUGIN: {"version": VERSION}
        }:
            raise MaterializationError("unexpected_installer_dependencies")
        provider = install / ".tessl/plugins/jbaruch/nanoclaw-core"
        inventory = verify_tree(provider, install)
        if private_directory(directory) != identity:
            raise MaterializationError("context_directory_binding_changed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        contained_directory(destination.parent, directory)
        if os.path.lexists(destination):
            raise MaterializationError("provider_destination_already_exists")
        os.rename(provider, destination)
        if verify_tree(destination, directory) != inventory:
            raise MaterializationError("relocated_provider_bytes_changed")
        prepare(directory)
    proof.update(status="materialized", files=len(inventory))
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    # Outer-boundary-process-contract: only expected registry/installer/filesystem
    # failures become a fixed refusal; no raw output or credential is exported.
    # No reviewer runs with missing authority. Programming defects propagate.
    try:
        result = materialize(args.directory.absolute(), args.prepare_only)
    except (
        MaterializationError,
        OSError,
        json.JSONDecodeError,
        UnicodeError,
        subprocess.SubprocessError,
    ) as error:
        reason = (
            str(error)
            if isinstance(error, MaterializationError)
            else "context_tool_unavailable"
        )
        print(json.dumps({"schema_version": 1, "status": "refused", "reason": reason}))
        print(
            "Core authority refused: " + reason + "; review the exact provider/report "
            "and restore a private fresh context before retrying.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
