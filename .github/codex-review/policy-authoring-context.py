#!/usr/bin/env python3
"""Resolve a plugin provider's review context from Git objects without execution.

argv: --repo owner/name --base REF [--head REF]. stdout: schema-1 JSON context.
stderr: actionable failures. Exit 0 resolved (provider or consumer), 2 unusable
metadata/tooling. Reads only committed regular blobs; writes no checkout files.
The trusted base establishes provider identity; head metadata cannot create it.
"""

import argparse
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys


MANIFESTS = (".tessl-plugin/plugin.json", "tile.json")


class ContextError(ValueError):
    """Review authority could not be established from committed metadata."""


def git(*arguments):
    try:
        result = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    except UnicodeError:
        raise ContextError("Git metadata is not valid text; restore its original encoding before reviewing") from None
    if result.returncode:
        raise ContextError("Git evidence is unavailable; restore the fetched base and head before reviewing")
    return result.stdout


def commit(ref):
    value = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()
    if re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", value) is None:
        raise ContextError("Git returned an unsupported commit identity; restore the repository")
    return value


def tree(ref):
    result = {}
    for entry in git("ls-tree", "-r", "-z", ref).split("\0"):
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


def manifest(index):
    for path in MANIFESTS:
        body = blob(index, path)
        if body is not None:
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                raise ContextError("Plugin manifest JSON is invalid; repair it before selecting review authority") from None
            if not isinstance(value, dict):
                raise ContextError("Plugin manifest must be an object; restore its declared identity")
            return path, value
    return None, None


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
        if directory in index:
            raise ContextError("A directory-form rule declaration names a file or symlink; select a committed directory")
        prefix = "" if directory == "." else directory + "/"
        children = [path for path in index if path.startswith(prefix)]
        if not children:
            raise ContextError("The declared rule directory is missing; restore its committed contents")
        paths = [path for path in children if path.endswith(".md")]
    elif isinstance(declaration, list):
        paths = [relative_path(path) for path in declaration]
    else:
        raise ContextError("Declare a rule directory or an array of rule-file paths")
    if any(not path.endswith(".md") for path in paths) or len(set(paths)) != len(paths):
        raise ContextError("Declare unique Markdown rule paths before reviewing their authority")
    return sorted(paths)


def context(repo, base, head="HEAD"):
    if re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo) is None:
        raise ContextError("Supply the trusted owner/repository identity from the review driver")
    base_sha, head_sha = commit(base), commit(head)
    path, trusted = manifest(tree(base_sha))
    result = {"schema_version": 1, "repo": repo, "base": base_sha, "head": head_sha,
              "eligible": False, "rules": [], "rule_blobs": {}}
    if trusted is None or trusted.get("name") != repo:
        return result
    head_tree = tree(head_sha)
    authored = manifest(head_tree)[1]
    if authored is None or authored.get("name") != repo:
        raise ContextError("Head plugin identity differs from its trusted base; review the identity migration explicitly")
    rules = rule_paths(authored.get("rules", []), head_tree)
    rule_blobs = {}
    for rule in rules:
        oid = regular_blob(head_tree, rule)
        if oid is None:
            raise ContextError("A declared head rule is missing; restore it or correct the manifest")
        rule_blobs[rule] = oid
    return {**result, "eligible": True, "manifest": path, "rules": rules, "rule_blobs": rule_blobs}


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
