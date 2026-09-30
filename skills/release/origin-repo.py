#!/usr/bin/env python3
"""Name the GitHub repository a checkout's `origin` remote points at.

A script that pushes to, deletes on, or reads from `origin` with git and asks
`gh` about the same repository passes this `<owner>/<repo>` to every `gh`
call, never letting `gh` pick its own default (`gh repo set-default`). The
parse is one answer per URL, so it lives here once for every caller
(`rules/script-delegation.md`).

Accepted origin URL forms, each with or without a trailing `.git` or `/`:
  https://github.com/<owner>/<repo>          (userinfo before the host allowed)
  ssh://[<user>@]github.com[:<port>]/<owner>/<repo>
  [<user>@]github.com:<owner>/<repo>         (scp form)
Every other scheme, host or path shape is not a GitHub repository URL. The
fetch URL and every push URL (`git remote get-url --push --all origin`) must
name the same repository, compared case-insensitively as GitHub does, or the
script refuses. The emitted names keep the fetch URL's spelling.

Contract:
  argv  : <checkout>
  stdout: one JSON object — {"repo": "<owner>/<repo>", "owner": "<owner>",
          "name": "<repo>"}
  stderr: diagnostics only, never the remote URL: it can carry credentials.
  exit  : 0 resolved,
          1 precondition unmet (usage, git absent, git failed, no origin),
          2 origin is not a GitHub repository URL, or its fetch and push
            URLs name different repositories.
"""

import json
import re
import subprocess
import sys

#: The only host whose repositories `gh` is asked about.
GITHUB_HOST = "github.com"

SCHEME_URL = re.compile(r"^(?P<scheme>https|ssh)://(?:[^@/]*@)?(?P<host>[^/:@]+)(?::\d*)?/(?P<path>[^/].*)$")
SCP_URL = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^/:@]+):(?P<path>[^/].*)$")
REPO_PATH = re.compile(r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/(?P<name>[A-Za-z0-9._-]+)$")


def parse_github_url(url):
    """(owner, name) for a GitHub repository URL, None for anything else."""
    if "://" in url:
        match = SCHEME_URL.match(url)
    else:
        match = SCP_URL.match(url)
    if match is None or match.group("host").lower() != GITHUB_HOST:
        return None
    path = match.group("path")
    if path.endswith("/"):
        path = path[:-1]
    if path.endswith(".git"):
        path = path[:-4]
    repo = REPO_PATH.match(path)
    if repo is None or repo.group("name") in (".", ".."):
        return None
    return repo.group("owner"), repo.group("name")


def fail(code, message):
    print("origin-repo: {}".format(message), file=sys.stderr)
    return code


def remote_urls(checkout, push):
    """origin's fetch URL, or every push URL; raises RuntimeError with the rerun command."""
    command = ["git", "-C", checkout, "remote", "get-url"] + (["--push", "--all"] if push else []) + ["origin"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError("git not found on PATH — install it, then re-run") from None
    if done.returncode != 0:
        # git's own message is not relayed: a misconfigured remote can echo its URL.
        raise RuntimeError("`{}` exited {} — run it to see why; add an origin with "
                           "`git -C {} remote add origin <github-url>` if there is none".format(
                               " ".join(command), done.returncode, checkout))
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def same_repo(a, b):
    """GitHub owner and repository names compare case-insensitively."""
    return a is not None and b is not None and (a[0].lower(), a[1].lower()) == (b[0].lower(), b[1].lower())


def main(argv):
    if len(argv) != 1:
        return fail(1, "usage: origin-repo.py <checkout>")
    checkout = argv[0]
    try:
        fetch_urls = remote_urls(checkout, push=False)
        push_urls = remote_urls(checkout, push=True)
    except RuntimeError as exc:
        return fail(1, str(exc))
    fetch = parse_github_url(fetch_urls[0]) if fetch_urls else None
    if fetch is None:
        return fail(2, "origin in {} is not a GitHub repository URL (https://github.com/<owner>/<repo>, "
                       "ssh://git@github.com/<owner>/<repo> or git@github.com:<owner>/<repo>), so gh cannot be "
                       "bound to it — inspect it with `git -C {} remote get-url origin`; nothing falls back to "
                       "gh's default repository".format(checkout, checkout))
    if not push_urls or not all(same_repo(parse_github_url(url), fetch) for url in push_urls):
        return fail(2, "origin in {} fetches from {}/{} but pushes elsewhere, so no single repository answers for "
                       "it — inspect `git -C {} remote get-url --push --all origin` and align every push URL".format(
                           checkout, fetch[0], fetch[1], checkout))
    owner, name = fetch
    print(json.dumps({"repo": "{}/{}".format(owner, name), "owner": owner, "name": name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
