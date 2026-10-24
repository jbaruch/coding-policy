#!/usr/bin/env python3
"""Bind an explicit existing GitHub PR to its already checked-out source.

Usage: existing-pr.py <https://github.com/OWNER/REPO/pull/N> <checkout>
No clone, checkout, fetch, push, PR creation or remote write occurs. The caller
prepares the checkout and runs the normal release readiness checks. Repeating
this read after those checks refuses a changed local or remote selection.

The selected origin's fetch/push repositories must agree with the URL. Only
an open same-repository PR into its repository's default branch is accepted;
fork PRs follow adopt-fork-pr. Its source branch and full head SHA must match
the clean feature checkout, including untracked files. Both local state and
remote PR metadata are read again before returning a selection. Draft status
is returned, never interpreted as readiness or permission to mark ready.

stdout: schema-1 JSON {repo, owner, name, pr_number, url, head_sha, branch,
        base_ref, base_sha, is_draft, checkout}. This is a selection snapshot,
        never a CI/review/merge/publication verdict or authorization.
exit: 0 selected; 1 unmet precondition/usage; 2 missing tool or unreadable
      source. Diagnostics never include child output, PR bodies or remotes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SPEC = importlib.util.spec_from_file_location(
    "release_origin_repo", Path(__file__).with_name("origin-repo.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Reinstall the release skill's co-shipped origin resolver.")
ORIGIN: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORIGIN)
SHA = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?")
PR_URL = re.compile(r"https://github\.com/([^/?#]+)/([^/?#]+)/pull/([1-9][0-9]*)")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SelectionError(ValueError):
    """Repair the explicit PR/checkout selection before releasing."""


class ToolError(RuntimeError):
    """Restore the read route; never replace it with another repository."""


def command(argv: list[str], checkout: Path) -> str:
    try:
        result = subprocess.run(
            argv, cwd=checkout, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ToolError("Restore git/gh and their read access, then retry this selection.") from None
    if result.returncode or len(result.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ToolError("Restore the bounded git/gh read route, then retry this selection.")
    return result.stdout


def url_parts(url: str) -> tuple[str, str, int]:
    match = PR_URL.fullmatch(url)
    repository = ORIGIN.REPO_PATH.fullmatch(match[1] + "/" + match[2]) if match else None
    if match is None or repository is None or match[2] in (".", ".."):
        raise SelectionError("Supply the exact https://github.com/OWNER/REPO/pull/N URL.")
    try:
        number = int(match[3])
    except ValueError:
        raise SelectionError("Supply a supported numeric GitHub PR URL.") from None
    return match[1], match[2], number


def api(endpoint: str, checkout: Path) -> dict[str, Any]:
    raw = command(["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint], checkout)
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise ToolError("Restore complete GitHub JSON before selecting the existing PR.") from None
    if not isinstance(value, dict):
        raise ToolError("Restore supported GitHub metadata before selecting the existing PR.")
    return value


def local_source(checkout: Path) -> tuple[str, str]:
    if command(["git", "status", "--porcelain=v1", "--untracked-files=normal"], checkout):
        raise SelectionError("Commit or resolve the selected checkout's changes before resuming the PR.")
    branch = command(["git", "branch", "--show-current"], checkout).strip()
    head = command(["git", "rev-parse", "HEAD"], checkout).strip()
    if not branch or SHA.fullmatch(head) is None:
        raise SelectionError("Check out the existing PR's feature branch and complete source commit.")
    return branch, head


def checked_remote(
    value: dict[str, Any], owner: str, name: str, number: int, default: str,
) -> tuple[str, str, str, str, bool]:
    head, base = value.get("head"), value.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise SelectionError("Read the existing PR's complete head and base metadata.")
    repository = (owner + "/" + name).casefold()
    try:
        actual_url = url_parts(value["html_url"]) if isinstance(value.get("html_url"), str) else None
    except SelectionError:
        actual_url = None
    if (
        value.get("state") != "open" or value.get("merged") is not False
        or type(value.get("number")) is not int or value["number"] != number
        or actual_url is None
        or (actual_url[0] + "/" + actual_url[1]).casefold() != repository
        or actual_url[2] != number or type(value.get("draft")) is not bool
        or any(not isinstance(part.get("repo"), dict) for part in (head, base))
        or any(str(part["repo"].get("full_name", "")).casefold() != repository for part in (head, base))
        or base.get("ref") != default
        or not isinstance(head.get("ref"), str) or not head["ref"] or head["ref"] == default
        or not isinstance(head.get("sha"), str) or SHA.fullmatch(head["sha"]) is None
        or not isinstance(base.get("sha"), str) or SHA.fullmatch(base["sha"]) is None
    ):
        raise SelectionError("Select an open same-repository feature PR into its default branch; adopt forks separately.")
    return head["ref"], head["sha"], base["ref"], base["sha"], value["draft"]


def check_origin(checkout: Path, owner: str, name: str) -> None:
    try:
        fetched = ORIGIN.remote_urls(str(checkout), push=False)
        pushed = ORIGIN.remote_urls(str(checkout), push=True)
    except RuntimeError:
        raise ToolError("Restore the selected checkout's origin fetch/push configuration.") from None
    if (
        len(fetched) != 1 or not pushed
        or not all(ORIGIN.same_repo(ORIGIN.parse_github_url(remote), (owner, name)) for remote in fetched + pushed)
    ):
        raise SelectionError("Align origin's fetch and every push repository with the explicit PR URL.")


def select(url: str, checkout: Path) -> dict[str, Any]:
    owner, name, number = url_parts(url)
    checkout = checkout.resolve(strict=True)
    check_origin(checkout, owner, name)
    branch, head = local_source(checkout)
    root = "repos/" + owner + "/" + name
    repository = api(root, checkout)
    default = repository.get("default_branch")
    if str(repository.get("full_name", "")).casefold() != (owner + "/" + name).casefold() or not isinstance(default, str) or not default:
        raise ToolError("Read the selected repository's actual default branch before releasing.")
    endpoint = root + "/pulls/" + str(number)
    selected = checked_remote(api(endpoint, checkout), owner, name, number, default)
    if selected[:2] != (branch, head):
        raise SelectionError("Check out the selected PR's current branch/head; do not create another PR.")
    if checked_remote(api(endpoint, checkout), owner, name, number, default) != selected or local_source(checkout) != (branch, head):
        raise SelectionError("Re-read the changed local/remote PR selection before releasing.")
    check_origin(checkout, owner, name)
    return {
        "schema_version": 1, "repo": owner + "/" + name, "owner": owner, "name": name,
        "pr_number": number, "url": url, "head_sha": head, "branch": branch,
        "base_ref": selected[2], "base_sha": selected[3], "is_draft": selected[4],
        "checkout": str(checkout),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("existing-pr: usage: existing-pr.py <PR URL> <checkout>", file=sys.stderr)
        return 1
    try:
        result = select(argv[0], Path(argv[1]))
    except SelectionError as error:
        print("existing-pr: " + str(error), file=sys.stderr)
        return 1
    except (ToolError, OSError) as error:
        message = str(error) if isinstance(error, ToolError) else "Restore the readable selected checkout before releasing."
        print("existing-pr: " + message, file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
