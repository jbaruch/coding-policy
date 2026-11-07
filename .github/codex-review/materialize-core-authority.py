#!/usr/bin/env python3
"""Install the current Core authority alone in a private review context.

Usage: materialize-core-authority.py DIRECTORY. The central driver owns this
private directory and calls before starting the reviewer. Core's installation
warnings are accepted for this read-only authority context only;
the helper executes no plugin workflow. Other dependencies never share the
warning-accepting installer workspace. Repeated calls reuse its genuine tree.

Stdout: schema-2 {status: materialized, plugin, version}. Refusal exits 2 with
a fixed reason. The native installed manifest supplies the actual version;
ordinary package releases require no copied fingerprints or security reports.
"""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

PLUGIN = "jbaruch/nanoclaw-core"


class MaterializationError(RuntimeError):
    """Restore the private context or native Core installation before review."""


def private_directory(path):
    info = path.lstat()
    if not (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    ):
        raise MaterializationError("context_directory_not_private")


def provider_version(provider):
    if provider.is_symlink() or not provider.is_dir():
        raise MaterializationError("core_provider_unavailable")
    if any(path.is_symlink() for path in provider.rglob("*")):
        raise MaterializationError("core_provider_contains_symlink")
    manifest = json.loads((provider / ".tessl-plugin/plugin.json").read_text())
    if not (
        isinstance(manifest, dict)
        and manifest.get("name") == PLUGIN
        and isinstance(manifest.get("version"), str)
        and manifest["version"]
    ):
        raise MaterializationError("core_provider_identity_unavailable")
    return manifest["version"]


def materialize(directory):
    private_directory(directory)
    destination = directory / ".tessl/plugins/jbaruch/nanoclaw-core"
    if not os.path.lexists(destination):
        with tempfile.TemporaryDirectory(prefix="core-authority-", dir=directory) as temporary:
            install = Path(temporary)
            (install / "tessl.json").write_text(
                json.dumps({"mode": "managed", "dependencies": {}})
            )
            result = subprocess.run(
                [
                    "tessl", "install", PLUGIN, "--yes", "--accept-warnings",
                    "--strict", "--agent", "codex",
                ],
                cwd=install,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if result.returncode:
                raise MaterializationError("core_install_failed")
            project = json.loads((install / "tessl.json").read_text())
            provider = install / ".tessl/plugins/jbaruch/nanoclaw-core"
            version = provider_version(provider)
            if not isinstance(project, dict) or project.get("dependencies") != {
                PLUGIN: {"version": version}
            }:
                raise MaterializationError("unexpected_installer_dependencies")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.rename(provider, destination)
    return {
        "schema_version": 2,
        "status": "materialized",
        "plugin": PLUGIN,
        "version": provider_version(destination),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        result = materialize(args.directory.absolute())
    except (MaterializationError, OSError, ValueError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, MaterializationError) else "context_tool_unavailable"
        print(json.dumps({"schema_version": 2, "status": "refused", "reason": reason}))
        print(
            "Core authority unavailable: " + reason + "; restore a private fresh context and retry.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
