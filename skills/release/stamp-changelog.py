#!/usr/bin/env python3
"""Stamp the CHANGELOG's newest (un-headed) entries with the version being published.

Run by the publish pipeline (`.github/workflows/publish.yml`) immediately before
`tesslio/patch-version-publish`. Every merge is published, so there is no
"Unreleased" bucket: PR authors add `### ...` entry blocks at the TOP of
`CHANGELOG.md` (below the `# Changelog` H1) with NO version heading. This script
computes the version the publish step will assign and inserts a
`## <version> — <date>` heading above those un-headed entries, so the published
artifact (and the registry "what's new") shows them under their real version.

Version computation MIRRORS `tesslio/patch-version-publish`: take the registry's
latest published version and bump the patch, unless the local manifest is already
ahead (a manual bump), in which case use the manifest version as-is. Keeping the
two in lockstep is what makes the stamped heading match the published version.

Idempotent: if the top section already carries a `## ` heading (nothing un-headed
to stamp), it is a no-op.

Usage:
    stamp-changelog.py [--changelog CHANGELOG.md] [--manifest PATH]
                       [--latest X.Y.Z] [--date YYYY-MM-DD]

    --latest  skip the registry query and use this as the latest published
              version (for testing / manual runs). Omit in CI to query the
              registry via `tessl plugin info`.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_H2_RE = re.compile(r"^## ")
_ENTRY_RE = re.compile(r"^### ")


def compute_version(local: str, latest: str | None) -> str:
    """Return the version the publish step will assign.

    Mirrors tesslio/patch-version-publish: first publish (no registry version)
    uses the manifest version as-is; otherwise bump the registry latest's patch,
    unless the manifest is already ahead.
    """
    for label, val in (("local", local), ("latest", latest)):
        if val is not None and not _VERSION_RE.match(val):
            raise ValueError(f"{label} version must be X.Y.Z, got {val!r}")
    if latest is None:
        return local
    lp = [int(x) for x in local.split(".")]
    rp = [int(x) for x in latest.split(".")]
    local_num = lp[0] * 1_000_000 + lp[1] * 1_000 + lp[2]
    latest_num = rp[0] * 1_000_000 + rp[1] * 1_000 + rp[2]
    if local_num > latest_num:
        return local
    return f"{rp[0]}.{rp[1]}.{rp[2] + 1}"


def stamp_changelog(text: str, version: str, date: str) -> tuple[str, bool]:
    """Insert `## <version> — <date>` above the topmost un-headed `### ` entries.

    Un-headed entries are `### ` blocks that appear before the first `## ` heading.
    Returns (new_text, changed). No-op (changed=False) when the top section is
    already under a `## ` heading.
    """
    lines = text.splitlines()
    first_h2 = next((i for i, ln in enumerate(lines) if _H2_RE.match(ln)), None)
    first_entry = next((i for i, ln in enumerate(lines) if _ENTRY_RE.match(ln)), None)

    if first_entry is None:
        return text, False
    if first_h2 is not None and first_h2 < first_entry:
        return text, False  # newest entries already sit under a version heading

    heading = f"## {version} — {date}"
    new_lines = lines[:first_entry] + [heading, ""] + lines[first_entry:]
    new_text = "\n".join(new_lines)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, True


def query_latest_version(plugin_name: str) -> str | None:
    """Return the registry's latest published version, or None if it can't be read.

    Mirrors patch-version-publish's handling. Returns None in two cases so the
    caller falls back to the manifest version: a 404 (the plugin has never been
    published) and a Tessl auth failure (the stamp step ran without login — see
    the caller). Any other failure (network, etc.) is surfaced so it is not masked.
    """
    proc = subprocess.run(
        ["tessl", "plugin", "info", plugin_name],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        combined = proc.stdout + proc.stderr
        if "404" in combined:
            return None
        # No Tessl auth reached this step — e.g. a consumer publish workflow that
        # stamps without running setup-tessl / `tessl login` first. Fall back to
        # the manifest version rather than wedging the publish: the manifest is the
        # version being published when kept ahead of the registry (the standard
        # convention), and patch-version-publish still does its own authoritative
        # bump downstream. Genuine (non-auth) failures still raise.
        if re.search(r"authenticat|log ?in|sign (?:in|up)", combined, re.IGNORECASE):
            print(
                f"warning: `tessl plugin info {plugin_name}` requires Tessl auth in "
                "this step — falling back to the manifest version. Run setup-tessl "
                "before the stamp step for an authoritative registry check.",
                file=sys.stderr,
            )
            return None
        raise RuntimeError(
            f"`tessl plugin info {plugin_name}` failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    for line in proc.stdout.splitlines():
        if "Latest Version" in line:
            return line.split()[-1]
    raise RuntimeError(
        f"Could not find 'Latest Version' in `tessl plugin info {plugin_name}` output"
    )


def _read_manifest(manifest: Path | None) -> tuple[str, str]:
    if manifest is None:
        plugin = Path(".tessl-plugin/plugin.json")
        manifest = plugin if plugin.is_file() else Path("tile.json")
    data = json.loads(manifest.read_text())
    name, version = data.get("name"), data.get("version")
    if not name or not version:
        raise SystemExit(f"{manifest} is missing a .name or .version field")
    return name, version


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    ap.add_argument("--manifest", type=Path, default=None,
                    help="Manifest path (default: .tessl-plugin/plugin.json, then tile.json)")
    ap.add_argument("--latest", default=None,
                    help="Latest published version (skip the registry query)")
    ap.add_argument("--date", default=None, help="Heading date (default: today, UTC)")
    args = ap.parse_args()

    name, local = _read_manifest(args.manifest)
    latest = args.latest if args.latest is not None else query_latest_version(name)
    version = compute_version(local, latest)
    date = args.date or datetime.now(timezone.utc).date().isoformat()

    text = args.changelog.read_text()
    new_text, changed = stamp_changelog(text, version, date)
    if changed:
        args.changelog.write_text(new_text)
        print(f"Stamped {args.changelog} top entries as ## {version} — {date}")
    else:
        print(f"No un-headed entries to stamp in {args.changelog} (no-op)")


if __name__ == "__main__":
    main()
