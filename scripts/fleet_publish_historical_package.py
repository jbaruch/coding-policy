"""Read/pack/install comparison for the historical proof owner; never publish.

Only the trusted finalizer calls this module. Consumer Git blobs stay inert;
commands and installation configuration are fixed here, not consumer supplied.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from typing import Any

from fleet_publish import decode, encoded, file, require, tree
from fleet_publish_historical import (
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILES,
    _member,
    describe_files,
    package_binding,
)

# Review monthly and after CLI package/install format changes. Renew the same
# version in fleet-publish-finalize.yml in a dedicated dependency change.
TESSL_VERSION = "0.119.0"
COMMAND_SECONDS = 180


def command(arguments: list[str], cwd: Path) -> bytes:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GH_TOKEN", "GITHUB_TOKEN", "FLEET_CENTRAL_TOKEN")
        and not key.startswith("FLEET_APP_")
    }
    result = subprocess.run(
        ["tessl", *arguments],
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=COMMAND_SECONDS,
        check=False,
    )
    require(
        result.returncode == 0 and len(result.stdout) <= MAX_PACKAGE_BYTES,
        "Restore the pinned registry reader; preserve the historical source HOLD.",
    )
    return result.stdout


def archive_files(path: Path) -> dict[str, bytes]:
    """Never extract archive paths; admit every member before reading bytes."""
    require(
        path.stat().st_size <= MAX_PACKAGE_BYTES, "Packed source exceeds its bound."
    )
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = _member(member.name)
            require(
                member.isfile()
                and name not in files
                and len(files) < MAX_PACKAGE_FILES
                and 0 <= member.size <= MAX_PACKAGE_BYTES,
                "Restore a complete regular-file package without duplicate members.",
            )
            total += member.size
            require(total <= MAX_PACKAGE_BYTES, "Packed source exceeds its byte bound.")
            stream = archive.extractfile(member)
            require(stream is not None, "Restore complete packed file bytes.")
            if stream is None:
                raise ValueError("Packed file bytes are unavailable.")
            with stream:
                data = stream.read(MAX_PACKAGE_BYTES + 1)
            require(len(data) == member.size, "Packed source member size changed.")
            files[name] = data
    require(files, "Restore the complete original packed source.")
    return files


def installed_files(root: Path) -> dict[str, bytes]:
    """Temporary, owner-created install root; refuse every unexpected leaf."""
    require(
        root.is_dir() and not root.is_symlink(), "Restore the private install root."
    )
    result: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), "Refuse symlinked installed content.")
        if stat.S_ISDIR(info.st_mode):
            continue
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and len(result) < MAX_PACKAGE_FILES,
            "Refuse nonregular or multiply linked installed content.",
        )
        total += info.st_size
        require(total <= MAX_PACKAGE_BYTES, "Installed content exceeds its byte bound.")
        name = _member(path.relative_to(root).as_posix())
        data = path.read_bytes()
        require(len(data) == info.st_size, "Installed content changed during readback.")
        result[name] = data
    return result


def observe_package(github: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    original = envelope["intent"]
    plugin, version = original["plugin_name"], original["version"]
    with tempfile.TemporaryDirectory(prefix="fleet-historical-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir(mode=0o700)
        source_tree, entries = tree(
            github, original["consumer_repository"], original["source_sha"]
        )
        require(
            source_tree == original["source_tree_sha"], "Original source tree changed."
        )
        total = 0
        count = 0
        for name, entry in entries.items():
            _member(name)
            if entry.get("type") == "tree":
                require(
                    entry.get("mode") == "040000", "Refuse special source directories."
                )
                continue
            data = file(github, original["consumer_repository"], entries, name)
            total += len(data)
            count += 1
            require(
                total <= MAX_PACKAGE_BYTES and count <= MAX_PACKAGE_FILES,
                "Original source exceeds its package bounds.",
            )
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        require(
            command(["--version"], root).decode().strip() == TESSL_VERSION,
            "Install the reviewed Tessl CLI version before attesting.",
        )
        packed = root / "source.tgz"
        command(["plugin", "pack", "--output", str(packed), str(source)], root)
        source_files = archive_files(packed)
        # The installed package owner receives only this fixed exact-version
        # dependency. No consumer configuration, hook or script is executed.
        install = root / "install"
        install.mkdir(mode=0o700)
        (install / "tessl.json").write_bytes(
            encoded(
                {
                    "name": "fleet-historical-verification",
                    "mode": "managed",
                    "dependencies": {plugin: {"version": version}},
                }
            )
        )
        command(["install", "--strict", "--yes", f"{plugin}@{version}"], install)
        installed = installed_files(install / ".tessl/plugins" / plugin)
        for complete in (source_files, installed):
            for descriptor in ("tile.json", ".tessl-plugin/plugin.json"):
                require(
                    descriptor in complete, "Restore both complete package descriptors."
                )
                parsed = decode(complete[descriptor])
                require(
                    isinstance(parsed, dict)
                    and parsed.get("name") == plugin
                    and parsed.get("version") == version,
                    "Original pack and exact installed descriptor coordinates must match the intent.",
                )
        metadata = installed.pop("tessl-package.json", None)
        require(metadata is not None, "Restore exact installer metadata.")
        installer = decode(metadata) if metadata is not None else None
        endpoint = f"v1/tiles/{plugin}/versions/{version}"
        registry = decode(command(["api", endpoint], root))
        require(isinstance(registry, dict), "Restore exact registry version readback.")
        attributes = registry.get("data", {}).get("attributes", {})
        return package_binding(
            {
                "source": describe_files(source_files),
                "installed": describe_files(installed),
                "installer": installer,
                "fingerprint": attributes.get("fingerprint"),
                "moderation": attributes.get("moderationStatus"),
                "version": attributes.get("version"),
                "observed_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "tool": {"name": "tessl", "version": TESSL_VERSION},
            },
            plugin,
            version,
        )
