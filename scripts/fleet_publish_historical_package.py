"""Read/pack/install comparison for the historical proof owner; never publish.

Only the trusted finalizer calls this module. Consumer Git blobs stay inert;
commands and installation configuration are fixed here, not consumer supplied.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import stat
import re
import subprocess
import tarfile
import tempfile
from typing import Any

from fleet_publish import (
    MANIFEST,
    STAMP,
    decode,
    encoded,
    file,
    require,
    tree,
    projection,
    previous,
    publication_facts,
    overlay_previous_changelog,
    stamp_package,
    replace_suffix,
)
from fleet_publish_record import valid_sha
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


def historical_projection(github: Any, envelope: dict[str, Any], destination: Path) -> None:
    """Reproduce publisher bytes from native source/claim, never installed output.

    A date is observed only in the original native bookkeeping changelog. It
    is transformed artifact content, not an inferred publication timestamp.
    An absent date is usable only when the maintained stamp is a no-op.
    """
    original = envelope["intent"]
    repo = original["consumer_repository"]
    source_tree, source_entries = tree(github, repo, original["source_sha"])
    require(source_tree == original["source_tree_sha"], "Original source tree changed.")
    claim = github.api(f"repos/{repo}/git/commits/{envelope['bookkeeping_sha']}")
    require(
        isinstance(claim, dict)
        and claim.get("sha") == envelope["bookkeeping_sha"]
        and isinstance(claim.get("parents"), list)
        and len(claim["parents"]) == 1
        and isinstance(claim["parents"][0], dict)
        and valid_sha(claim["parents"][0].get("sha")),
        "Restore the exact single-parent native bookkeeping commit.",
    )
    _, parent_entries = tree(github, repo, claim["parents"][0]["sha"])
    _, claimed_entries = tree(github, repo, envelope["bookkeeping_sha"])
    require(
        decode(file(github, repo, claimed_entries, STAMP)) == original,
        "Native bookkeeping no longer contains the original intent.",
    )
    def unchanged(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            name: row
            for name, row in entries.items()
            if row.get("type") != "tree" and name not in (MANIFEST, STAMP, "CHANGELOG.md")
        }
    require(
        unchanged(parent_entries) == unchanged(claimed_entries),
        "Native bookkeeping changed source outside its three owned paths.",
    )
    manifest = projection(github, repo, source_entries, destination, original["plugin_name"])
    source_manifest = dict(manifest)
    # The original successful closed publisher already proved its initial
    # no-prior boundary. Never query today's default history as old absence.
    prior_evidence = previous(github, repo, parent_entries) if STAMP in parent_entries else None
    prior = publication_facts(prior_evidence) if prior_evidence is not None else None
    require(prior is None or prior["state"] == "confirmed", "Restore confirmed prior publication continuity.")
    changelog = destination / "CHANGELOG.md"
    selected_text = overlay_previous_changelog(github, repo, changelog.read_text(), prior)
    changelog.write_text(selected_text)
    retained = file(github, repo, claimed_entries, "CHANGELOG.md").decode("utf-8")
    dates = re.findall(r"^## " + re.escape(original["version"]) + r" — ([0-9]{4}-[0-9]{2}-[0-9]{2})$", retained, re.MULTILINE)
    require(len(dates) <= 1, "Restore one unambiguous observed version stamp.")
    observed_date = dates[0] if dates else None
    if observed_date is not None:
        date.fromisoformat(observed_date)
    version = stamp_package(destination, manifest, original["registry_baseline"], observed_date)
    require(version == original["version"], "Original source and baseline do not derive the recorded version.")
    before = file(github, repo, parent_entries, "CHANGELOG.md").decode("utf-8")
    require(
        replace_suffix(before, selected_text, changelog.read_text()) == retained,
        "The maintained projection/stamp does not reproduce native bookkeeping.",
    )
    before_manifest = file(github, repo, parent_entries, MANIFEST)
    current = decode(before_manifest)
    require(isinstance(current, dict), "Restore native parent manifest metadata.")
    if (
        {key: value for key, value in current.items() if key != "version"}
        == {key: value for key, value in source_manifest.items() if key != "version"}
        and current.get("version") in {source_manifest["version"], prior["version"] if prior else None}
    ):
        before_manifest = (destination / MANIFEST).read_bytes()
    require(
        file(github, repo, claimed_entries, MANIFEST) == before_manifest,
        "Native bookkeeping manifest is not the exact owned transform.",
    )


def observe_package(github: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    original = envelope["intent"]
    plugin, version = original["plugin_name"], original["version"]
    with tempfile.TemporaryDirectory(prefix="fleet-historical-") as temporary:
        root = Path(temporary)
        source = root / "source"
        historical_projection(github, envelope, source)
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
