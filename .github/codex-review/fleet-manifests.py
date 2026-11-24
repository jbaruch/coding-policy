"""Select fleet review installs and limitations from one captured Git commit."""

import json
import re
import subprocess
import sys
import unicodedata


ROOT = "tessl.json"
LIST = ".github/fleet-review-manifests"
NUMBER = r"(?:0|[1-9][0-9]*)"
PRE = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
BUILD = r"[0-9A-Za-z-]+"
SEMVER = re.compile(rf"{NUMBER}\.{NUMBER}\.{NUMBER}(?:-{PRE}(?:\.{PRE})*)?(?:\+{BUILD}(?:\.{BUILD})*)?")


class ManifestError(ValueError):
    """Committed configuration needs repair."""


def git(command: str, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", command, *args], capture_output=True, check=False)
    except OSError:
        sys.exit("error: cannot run Git to read review manifests — restore Git and rerun the review")
    if result.returncode:
        sys.exit(f"error: git {command} failed while reading review manifests — restore the fetched objects and rerun")
    return result.stdout


def blob(base: str, path: str) -> bytes | None:
    entry = git("ls-tree", "--full-tree", "--format=%(objectmode) %(objectname)", base, "--", path)
    if not entry:
        return None
    mode, oid = entry.split()
    if mode not in (b"100644", b"100755"):
        raise ManifestError("use a regular committed file")
    return git("cat-file", "blob", oid.decode("ascii"))


def unique_object(pairs: list) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def invalid_constant(_value: str) -> None:
    raise ValueError("invalid JSON literal")


def dependencies(data: bytes) -> dict:
    message = "declare one JSON object with a valid dependencies map"
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise ManifestError(message) from None
    deps = doc.get("dependencies", {}) if isinstance(doc, dict) else None
    if not isinstance(deps, dict):
        raise ManifestError(message)
    for name, record in deps.items():
        if (not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", name)
                or name.startswith("-") or any(part in (".", "..") for part in name.split("/"))
                or not isinstance(record, dict) or not record or record.keys() - {"version", "source"}
                or not all(isinstance(value, str) and value
                           and all(unicodedata.category(char) != "Cc" for char in value)
                           for value in record.values())):
            raise ManifestError(message)
    return deps


def selection(base: str) -> dict:
    limitations = []
    manifests = {ROOT: ROOT}
    try:
        listing = blob(base, LIST)
    except ManifestError as error:
        limitations.append(f"{LIST}: {error} on the base.")
        listing = None
    for number, raw in enumerate(listing.split(b"\n") if listing is not None else (), 1):
        location = f"{LIST} line {number}"
        entry = raw.strip()
        if b"\0" not in raw and (not entry or entry.startswith(b"#")):
            continue
        if (not re.fullmatch(rb"[A-Za-z0-9._/-]+", entry)
                or any(part in (b"", b".", b"..") for part in entry.split(b"/"))):
            limitations.append(f"{location}: use a repository-relative path such as tessl-workspace/tessl.json.")
            continue
        manifests.setdefault(entry.decode("ascii"), location)

    installs = {}
    conflicts = set()
    for path, location in manifests.items():
        try:
            data = blob(base, path)
            if data is None:
                if path != ROOT:
                    limitations.append(f"{location}: the listed manifest is missing on the base; commit it there.")
                continue
            deps = dependencies(data)
        except ManifestError as error:
            if path == ROOT:
                sys.exit(f"error: could not parse dependencies from base {ROOT} — {error} before reviewing")
            limitations.append(f"{location}: {error} on the base.")
            continue
        for name, record in sorted(deps.items()):
            if "source" in record:
                limitations.append(f"{location}, {name}: declare a registry version in place of source.")
                continue
            version = record["version"]
            if version == "latest" and path == ROOT and name.startswith("jbaruch/"):
                spec = name
            elif SEMVER.fullmatch(version):
                spec = f"{name}@{version}"
            else:
                limitations.append(f"{location}, {name}: declare an exact SemVer such as 1.2.3.")
                continue
            # The reviewer owns these versions. Core uses the declaration only for enrollment.
            if name == "jbaruch/coding-policy":
                continue
            if name == "jbaruch/nanoclaw-core":
                spec = name
            if installs.setdefault(name, spec) != spec and name not in conflicts:
                conflicts.add(name)
                limitations.append(f"{location}, {name}: conflicting versions; use one pin across base manifests.")
    return {
        "installs": [[name, spec] for name, spec in installs.items() if name not in conflicts],
        "limitations": limitations,
    }


if __name__ == "__main__":
    print(json.dumps(selection(sys.argv[1])))
