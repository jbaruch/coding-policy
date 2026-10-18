#!/usr/bin/env python3
"""Resolve a plugin provider's review context from Git objects without execution.

argv: --repo owner/name --base REF [--head REF]. stdout: schema-1 JSON context.
stderr: actionable failures. Exit 0 resolved (provider or consumer), 2 unusable
metadata/tooling. Reads only committed regular blobs; writes no checkout files.
The trusted base establishes provider identity; head metadata cannot create it.
"""

import argparse
import json
import re
import subprocess
import sys


MANIFESTS = (".tessl-plugin/plugin.json", "tile.json")
RULE_PATH = re.compile(r"rules/[a-z0-9][a-z0-9_-]*\.md")


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


def regular_blob(ref, path):
    entries = git("ls-tree", "-z", ref, "--", path).rstrip("\0")
    if not entries:
        return False
    metadata, found = entries.split("\t", 1)
    mode, kind, _oid = metadata.split()
    if found != path or mode not in ("100644", "100755") or kind != "blob":
        raise ContextError("Authority metadata must be committed regular files; replace symlink or non-file declarations")
    return True


def blob(ref, path):
    return git("cat-file", "blob", ref + ":" + path) if regular_blob(ref, path) else None


def manifest(ref):
    for path in MANIFESTS:
        body = blob(ref, path)
        if body is not None:
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                raise ContextError("Plugin manifest JSON is invalid; repair it before selecting review authority") from None
            if not isinstance(value, dict):
                raise ContextError("Plugin manifest must be an object; restore its declared identity")
            return path, value
    return None, None


def context(repo, base, head="HEAD"):
    if re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo) is None:
        raise ContextError("Supply the trusted owner/repository identity from the review driver")
    base_sha, head_sha = commit(base), commit(head)
    path, trusted = manifest(base_sha)
    result = {"schema_version": 1, "repo": repo, "base": base_sha, "head": head_sha,
              "eligible": False, "rules": []}
    if trusted is None or trusted.get("name") != repo:
        return result
    authored = manifest(head_sha)[1]
    if authored is None or authored.get("name") != repo:
        raise ContextError("Head plugin identity differs from its trusted base; review the identity migration explicitly")
    rules = authored.get("rules", [])
    if (not isinstance(rules, list) or any(not isinstance(p, str) or RULE_PATH.fullmatch(p) is None for p in rules)
            or len(set(rules)) != len(rules)):
        raise ContextError("Declare unique rules/<name>.md paths in the head manifest before reviewing their authority")
    for rule in rules:
        if not regular_blob(head_sha, rule):
            raise ContextError("A declared head rule is missing; restore it or correct the manifest")
    return {**result, "eligible": True, "manifest": path, "rules": sorted(rules)}


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
