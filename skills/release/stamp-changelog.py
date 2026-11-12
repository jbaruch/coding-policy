#!/usr/bin/env python3
"""Stamp the CHANGELOG with the version being published and the PRs it ships.

Run by the publish pipeline (`.github/actions/stamp-changelog`) immediately
before the publish step (`smart-publish`). Every merge is published, and PRs add
no CHANGELOG entries: the PR title is the changelog line. This script computes
the version the publish step will assign and inserts `## <version> — <date>`
above the newest section, followed by one `- <PR title> (#N)` line per pull
request merged since the last publish.

The changes are the first-parent commits after the newest publish-bookkeeping
commit: a first-parent ancestor of HEAD authored by `github-actions[bot]` that
changes only the changelog and manifest files (the stamp commit and the
version-bump commit). With no such commit in history (a repo's first pipeline
publish), HEAD alone is the change. A GitHub merge commit (`Merge pull request
#N from ...`) contributes its body's first line, the PR title, as `<title>
(#N)`. Any other commit, a squash merge included, contributes its subject. A
shallow checkout is an error.

A `### ` block an older PR left un-headed at the top is headed instead, as
before, with no title lines. With nothing un-headed and no changes, the script
is a no-op.

Version computation MIRRORS the publish step's `--bump patch` semantics
(`smart-publish` / `tessl plugin publish --bump patch`): take the registry's
latest published version and bump the patch, unless the local manifest is already
ahead (a manual bump), in which case use the manifest version as-is. Keeping the
two in lockstep is what makes the stamped heading match the published version.

Usage:
    stamp-changelog.py [--changelog CHANGELOG.md] [--manifest PATH]
                       [--latest X.Y.Z] [--date YYYY-MM-DD]

    --latest         skip the registry query and use this as the latest
                     published version (for testing / manual runs). Omit in CI
                     to query the registry's versions API via the sibling
                     `registry-version.sh` — the same read `smart-publish.sh`
                     computes its target from.
"""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_H2_RE = re.compile(r"^## ")
_ENTRY_RE = re.compile(r"^### ")
_MERGE_RE = re.compile(r"^Merge pull request #(\d+) from \S+$")
BOT = "github-actions[bot]"
MANIFESTS = (".tessl-plugin/plugin.json", "tile.json")


def compute_version(local: str, latest: str | None) -> str:
    """Return the version the publish step will assign.

    Mirrors the publish step's `--bump patch` semantics: first publish (no
    registry version) uses the manifest version as-is; otherwise bump the
    registry latest's patch, unless the manifest is already ahead.
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


def _first_unheaded(lines: list[str]) -> int | None:
    """Index of the first `### ` line above every `## ` heading, else None."""
    for i, line in enumerate(lines):
        if _H2_RE.match(line):
            return None
        if _ENTRY_RE.match(line):
            return i
    return None


def stamp_changelog(text: str, version: str, date: str | None,
                    changes: list[str] | tuple[str, ...] = ()) -> tuple[str, bool]:
    """Insert `## <version> — <date>` for the version being published.

    Un-headed `### ` blocks above the first `## ` heading get the heading
    directly above them, and `changes` is ignored. Otherwise the heading and
    one `- <change>` line per item of `changes` go above the first `## `
    heading, or at the end of a file that has none. Returns (new_text,
    changed); a no-op when nothing is un-headed and `changes` is empty.
    """
    lines = text.splitlines()
    unheaded = _first_unheaded(lines)
    if unheaded is None and not changes:
        return text, False
    if date is None:
        raise ValueError("Restore the observed changelog stamp date before reconstructing it.")

    heading = f"## {version} — {date}"
    if unheaded is not None:
        at, block = unheaded, [heading, ""]
    else:
        at = next((i for i, ln in enumerate(lines) if _H2_RE.match(ln)), len(lines))
        block = [heading, ""] + [f"- {change}" for change in changes]
        if at < len(lines):
            block.append("")
        if at > 0 and lines[at - 1].strip():
            block.insert(0, "")
    new_text = "\n".join(lines[:at] + block + lines[at:])
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, True


def git(repo: Path, *args: str) -> str:
    """Stdout of a `git` call in `repo` that must succeed."""
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"`git {' '.join(args)}` failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or 'no diagnostic'}. Run the stamp inside a "
            "full-history checkout of the repository.")
    return proc.stdout


def last_publish(repo: Path, bookkeeping: set[str]) -> str | None:
    """The newest publish-bookkeeping commit on HEAD's first-parent line.

    None when history holds none. The predicate is in the module docstring.
    """
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise RuntimeError(
            "this checkout is shallow, so the last publish commit may be "
            "missing from it. Check out with full history (`actions/checkout` "
            "with `fetch-depth: 0`).")
    log = git(repo, "log", "--first-parent", "--format=%H%x09%an", "HEAD")
    for line in log.splitlines():
        sha, _, author = line.partition("\t")
        if author != BOT:
            continue
        out = git(repo, "diff-tree", "-z", "--no-commit-id", "--name-only",
                  "-r", "--root", sha)
        paths = {path for path in out.split("\0") if path}
        if paths and paths <= bookkeeping:
            return sha
    return None


def merged_changes(changelog: Path, manifest: Path | None) -> list[str]:
    """One line per first-parent commit since the last publish, oldest first."""
    repo = changelog.resolve().parent
    top = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    paths = [changelog] + ([manifest] if manifest else [])
    bookkeeping = set(MANIFESTS) | {p.resolve().relative_to(top).as_posix() for p in paths}
    base = last_publish(repo, bookkeeping)
    span = [f"{base}..HEAD"] if base else ["-1", "HEAD"]
    out = git(repo, "log", "--first-parent", "--reverse",
              "--format=%s%x00%b%x1e", *span)
    changes = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        subject, _, body = record.partition("\0")
        merge = _MERGE_RE.match(subject)
        title = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        changes.append(f"{title} (#{merge.group(1)})" if merge and title else subject)
    return changes


def query_latest_version(plugin_name: str) -> str | None:
    """Return the registry's latest published version, or None if never published.

    Reads through `registry-version.sh` — the versions API `smart-publish.sh`
    computes its target from. The `tessl plugin info` listing lags that API, and
    a lagging read stamped a heading one version behind the published one (#606).

    `{"version": null}` is the only never-published answer; exit and output
    contract in the header of `registry-version.sh`. Any non-zero exit is a
    tool failure and raises.
    """
    workspace, _, slug = plugin_name.partition("/")
    script = Path(__file__).resolve().parent / "registry-version.sh"
    proc = subprocess.run(
        ["bash", str(script), workspace, slug],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"registry-version.sh {workspace} {slug} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()} — run setup-tessl (tessl "
            "login) before the stamp step and make sure jq is on PATH, or pass "
            "--latest X.Y.Z to skip the registry read."
        )
    return json.loads(proc.stdout)["version"]


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
    changes: list[str] = []
    if _first_unheaded(text.splitlines()) is None:
        changes = merged_changes(args.changelog, args.manifest)
    new_text, changed = stamp_changelog(text, version, date, changes)
    if changed:
        args.changelog.write_text(new_text)
        lines = f" with {len(changes)} change line(s)" if changes else ""
        print(f"Stamped {args.changelog} as ## {version} — {date}{lines}")
    else:
        print(f"Nothing to stamp in {args.changelog} (no-op)")


if __name__ == "__main__":
    main()
