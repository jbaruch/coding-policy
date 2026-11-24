#!/usr/bin/env python3
"""Resolve a plugin provider's review context from Git objects without execution.

argv: --repo owner/name --base REF [--head REF]. stdout: schema-1 JSON context.
stderr: actionable failures. Exit 0 resolved (provider or consumer), 2 unusable
metadata/tooling. Reads only committed regular blobs; writes no checkout files.
ACR_METADATA_DECODER is the absolute path to the decoder built by the reviewer.
The trusted base establishes provider identity; head metadata cannot create it.
"""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ACR_MANIFEST = "agent-plugin.yaml"
MANIFESTS = (".tessl-plugin/plugin.json", "tile.json", ACR_MANIFEST)
_STDERR_CAP = 512


class ContextError(ValueError):
    """Review authority could not be established from committed metadata."""


class ManifestDataError(ContextError):
    """Base plugin manifest is malformed or declares no usable identity; yields not-eligible."""


def git(*arguments):
    result = subprocess.run(["git", *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ContextError("Git evidence is unavailable; restore the fetched base and head before reviewing")
    return result.stdout


def git_text(data):
    try:
        # Preserve literal CR and LF in Git paths when decoding tree output.
        return data.decode("utf-8")
    except UnicodeError:
        raise ContextError("Git metadata is not valid text; restore its original encoding before reviewing") from None


def commit(ref):
    value = git_text(git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")).strip()
    if re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", value) is None:
        raise ContextError("Git returned an unsupported commit identity; restore the repository")
    return value


def tree(ref):
    result = {}
    for entry in git_text(git("ls-tree", "-r", "-t", "-z", ref)).split("\0"):
        if entry:
            metadata, path = entry.split("\t", 1)
            result[path] = metadata.split()
    return result


def regular_blob(index, path):
    row = index.get(path)
    if row is None:
        return None
    mode, kind, oid = row
    if (mode not in ("100644", "100755") or kind != "blob"
            or re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", oid) is None):
        raise ContextError("Authority metadata must be committed regular files; replace symlink or non-file declarations")
    return oid


def blob(index, path):
    oid = regular_blob(index, path)
    return git("cat-file", "blob", oid) if oid else None


def unique_json_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Plugin manifest JSON keys must be unique")
    return fields


def acr_metadata(body):
    decoder = Path(os.environ.get("ACR_METADATA_DECODER", ""))
    if not decoder.is_absolute():
        raise ContextError("Build the central ACR metadata decoder and set ACR_METADATA_DECODER to its absolute path")
    try:
        result = subprocess.run([str(decoder)], input=body, capture_output=True, check=False)
    except OSError:
        raise ContextError("Cannot run the central ACR metadata decoder; rebuild it before reviewing") from None

    invalid = f"Plugin manifest {ACR_MANIFEST} is invalid; repair it before selecting review authority"
    if result.returncode == 2:
        # The decoder emits fixed diagnostics without quoting input.
        stderr = result.stderr[:_STDERR_CAP].decode('utf-8', errors='replace').strip()
        raise ManifestDataError(f"{invalid} ({stderr})")
    if result.returncode:
        raise ContextError("The central ACR metadata decoder failed; rebuild it before reviewing")
    try:
        value = json.loads(result.stdout)
    except (ValueError, RecursionError):
        raise ContextError("The central ACR metadata decoder returned invalid JSON; rebuild it before reviewing") from None
    if (not isinstance(value, dict) or not isinstance(value.get("name"), str)
            or not isinstance(value.get("repository"), str) or not isinstance(value.get("rules"), list)
            or any(not isinstance(path, str) for path in value["rules"])):
        raise ContextError("The central ACR metadata decoder returned invalid fields; rebuild it before reviewing")
    return value


def manifests(index):
    declarations = {}
    for path in MANIFESTS:
        body = blob(index, path)
        if body is None:
            continue
        if path == ACR_MANIFEST:
            value = acr_metadata(body)
        else:
            body_text = git_text(body)
            try:
                value = json.loads(body_text, object_pairs_hook=unique_json_fields)
            except (ValueError, RecursionError):
                raise ManifestDataError(f"Plugin manifest {path} is invalid; repair it before selecting review authority") from None
        if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"]:
            raise ManifestDataError(f"Plugin manifest {path} must declare a plugin name")
        declarations[path] = value
    if len({value["name"] for value in declarations.values()}) > 1:
        raise ManifestDataError("Committed plugin manifests declare conflicting identities; reconcile their names before reviewing")
    return declarations


def relative_path(value):
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise ContextError("Rule declarations need relative POSIX paths inside the plugin")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ContextError("Rule declarations cannot escape the plugin tree")
    return path.as_posix()


def rule_paths(declaration, index):
    if isinstance(declaration, str):
        directory = relative_path(declaration)
        if directory in index and index[directory][1] != "tree":
            raise ContextError("A directory-form rule declaration names a file or symlink; select a committed directory")
        prefix = "" if directory == "." else directory + "/"
        children = [path for path, row in index.items() if path.startswith(prefix) and row[1] != "tree"]
        if not children:
            raise ContextError("The declared rule directory is missing; restore its committed contents")
        paths = [path for path in children if path.endswith(".md")]
    elif isinstance(declaration, list):
        paths = [relative_path(path) for path in declaration]
    else:
        raise ContextError("Declare a rule directory or an array of rule-file paths")
    if any(not path.endswith(".md") for path in paths) or len(set(paths)) != len(paths):
        raise ContextError("Declare unique Markdown rule paths before reviewing their authority")
    return paths


def context(repo, base, head="HEAD"):
    if re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo) is None:
        raise ContextError("Supply the trusted owner/repository identity from the review driver")
    base_sha, head_sha = commit(base), commit(head)
    base_tree = tree(base_sha)
    result = {"schema_version": 1, "repo": repo, "base": base_sha, "head": head_sha,
              "eligible": False, "rules": [], "rule_blobs": {}}
    try:
        trusted = manifests(base_tree)
    except ManifestDataError as exc:
        print(f"policy-authoring-context: {exc} — local authoring context disabled; correct the committed base metadata before reviewing", file=sys.stderr)
        return result
    path = next(iter(trusted), None)
    if path is None or trusted[path]["name"] != repo:
        return result
    if ACR_MANIFEST in trusted and trusted[ACR_MANIFEST]["repository"] != f"https://github.com/{repo}":
        print(
            f"policy-authoring-context: local authoring context disabled — "
            f"set source.repository in {ACR_MANIFEST} on the base to https://github.com/{repo}",
            file=sys.stderr,
        )
        return result
    head_tree = tree(head_sha)
    authored = manifests(head_tree)
    if not authored or any(value["name"] != repo for value in authored.values()):
        raise ContextError("Head plugin identity differs from its trusted base; review the identity migration explicitly")
    if ACR_MANIFEST in authored and authored[ACR_MANIFEST]["repository"] != f"https://github.com/{repo}":
        raise ContextError(f"Head ACR source.repository must match https://github.com/{repo}")
    rules = set()
    for descriptor, value in authored.items():
        if descriptor == ACR_MANIFEST:
            for declared in value["rules"]:
                rule = relative_path(declared)
                if rule != declared or rule == "." or re.match(r"[A-Za-z]:", rule):
                    raise ContextError("Declare ACR rules with normalized package-relative POSIX paths")
                rules.add(rule)
        else:
            rules.update(rule_paths(value.get("rules", []), head_tree))
    rule_blobs = {}
    for rule in sorted(rules):
        oid = regular_blob(head_tree, rule)
        if oid is None:
            raise ContextError("A declared head rule is missing; restore it or correct the manifest")
        rule_blobs[rule] = oid
    return {**result, "eligible": True, "manifest": path, "rules": list(rule_blobs), "rule_blobs": rule_blobs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    try:
        result = context(args.repo, args.base, args.head)
    except (ContextError, OSError) as exc:
        print("policy-authoring-context: {} — restore Git and committed plugin metadata, then retry".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
