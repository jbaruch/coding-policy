#!/usr/bin/env python3
"""Read bounded prior PR feedback for review continuity, never merge authority.

Usage: prior-feedback.py OWNER REPO PR HEAD_SHA [REVIEWER_LOGIN]
REVIEWER_LOGIN defaults to the environment variable of the same name; missing login exits 1.
Requires authenticated gh. Stdout is one JSON object; items are capped at OUTPUT_BYTES.
`last_review` is {commit_id, blocking} from the final-line marker post-review.sh writes on the
latest review by REVIEWER_LOGIN, never truncated. The marker is {"schema_version": 1, "blocking": [...]};
a bare array is read as version 0, and any other integer schema_version gives a first review.
That review's body is re-read with its edit provenance over GraphQL. It is null with no such review,
when that review has no marker (posted before markers) or when it was ever edited (the poster never
edits), so the head gets a first review.
Rows by deleted accounts (null user) are skipped.
API/format failures and a malformed marker exit 1 with a stderr diagnostic, so the review posts no verdict.
No state or credentials are written. Invalid CLI arguments exit 2.
"""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


OUTPUT_BYTES = 16000
BODY_BYTES = 3000
MARKER = "<!-- coding-policy-blockers "
POLICY_AUTHORS = {"github-actions[bot]", "coding-policy-fleet-reviewer[bot]"}
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
PROVENANCE = ("query($id: ID!) { node(id: $id) { ... on PullRequestReview "
              "{ body author { login } commit { oid } lastEditedAt editor { login } } } }")


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def read(endpoint):
    done = subprocess.run(["gh", "api", "--paginate", "--slurp", endpoint],
                          capture_output=True, text=True, check=False, timeout=45)
    if done.returncode:
        raise ValueError("GitHub feedback read failed")
    pages = json.loads(done.stdout)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub feedback response is not paginated arrays")
    rows = [row for page in pages for row in page]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("GitHub feedback contains an unsupported row")
    return rows


def graphql(node_id):
    done = subprocess.run(["gh", "api", "graphql", "-f", f"query={PROVENANCE}", "-f", f"id={node_id}"],
                          capture_output=True, text=True, check=False, timeout=45)
    if done.returncode:
        raise ValueError(f"GitHub review provenance read failed for {node_id}")
    return json.loads(done.stdout)


def last_review(review, login):
    url = review.get("html_url")
    node_id = review.get("node_id")
    if not isinstance(node_id, str):
        raise ValueError(f"{url} lacks its node_id")
    response = graphql(node_id)
    node = (response.get("data") or {}).get("node") if isinstance(response, dict) else None
    author = node.get("author") if isinstance(node, dict) else None
    commit = node.get("commit") if isinstance(node, dict) else None
    if (not isinstance(node, dict) or not isinstance(node.get("body"), str) or not isinstance(author, dict)
            or author.get("login") not in (login, login.removesuffix("[bot]"))
            or not isinstance(commit, (dict, type(None))) or not isinstance(node.get("lastEditedAt"), (str, type(None)))
            or not isinstance(node.get("editor"), (dict, type(None)))):
        raise ValueError(f"GitHub review provenance for {url} is malformed")
    if node["lastEditedAt"] is not None or node["editor"] is not None:
        print(f"prior-feedback: review {review.get('id')} ({url}) was edited after posting; "
              "this head gets a first review", file=sys.stderr)
        return None
    line = node["body"].rstrip().rpartition("\n")[2]
    if not line.startswith(MARKER):
        print(f"prior-feedback: {url} has no blocker marker; this head gets a first review", file=sys.stderr)
        return None
    payload = line[len(MARKER):]
    try:
        record = json.loads(payload[:-len(" -->")]) if payload.endswith(" -->") else None
    except ValueError:
        record = None
    version = record.get("schema_version") if isinstance(record, dict) else None
    if isinstance(record, list):
        blocking = record  # version 0: the unversioned array PR #849's earlier heads posted
    elif type(version) is int and version == 1:
        blocking = record.get("blocking")
    elif type(version) is int:
        print(f"prior-feedback: {url} has blocker marker schema_version {version}, which this reader "
              "does not accept; this head gets a first review", file=sys.stderr)
        return None
    else:
        blocking = None
    if not isinstance(blocking, list) or any(not isinstance(finding, dict) for finding in blocking):
        raise ValueError(f"{url} ends with a malformed blocker marker; its final line must be "
                         f"'{MARKER}{{\"schema_version\":1,\"blocking\":[<blocking finding objects>]}} -->'")
    return {"commit_id": (commit or {}).get("oid"), "blocking": blocking}


def build(head, login, groups):
    result = {"schema_version": 1, "head_sha": head,
              "authority": "historical_feedback_only", "last_review": None, "items": [], "omitted": 0}
    selected = []
    latest = None
    for kind, rows in groups:
        for row in rows:
            if "user" not in row:
                raise ValueError("GitHub feedback lacks its user field")
            user = row["user"]
            if user is None:
                continue  # A deleted account is neither the reviewer nor a trusted author.
            author = user.get("login") if isinstance(user, dict) else None
            body = row.get("body")
            if not isinstance(author, str) or not isinstance(body, str):
                raise ValueError("GitHub feedback lacks an author or text body")
            if kind != "review" and author not in POLICY_AUTHORS and row.get("author_association") not in TRUSTED_ASSOCIATIONS:
                continue
            at = row.get("submitted_at") or row.get("created_at")
            if not isinstance(at, str):
                raise ValueError("GitHub feedback lacks its timestamp")
            if kind == "review" and author == login:
                latest = row
            if kind == "review" and author not in POLICY_AUTHORS:
                continue
            item = {"kind": kind, "author": author, "body": body,
                    "commit_id": row.get("commit_id"), "state": row.get("state"),
                    "path": row.get("path"), "line": row.get("line"),
                    "url": row.get("html_url"), "at": at}
            # A context limit never turns omitted feedback into a clean verdict.
            if len(encode(item["body"]).encode()) > BODY_BYTES:
                shortened = body
                while len(encode(shortened).encode()) > BODY_BYTES:
                    shortened = shortened[:len(shortened) // 2]
                item["body"] = shortened
                item["body_truncated"] = True
            selected.append(item)
    # GitHub lists reviews oldest first, so the last row by the login is its latest review.
    if latest is not None:
        result["last_review"] = last_review(latest, login)
    selected.sort(key=lambda row: row["at"] or "", reverse=True)
    for item in selected:
        result["items"].append(item)
        if len(encode(result).encode()) > OUTPUT_BYTES - 100:
            result["items"].pop()
            result["omitted"] += 1
    return result


def main(argv):
    if len(argv) == 4:
        login = os.environ.get("REVIEWER_LOGIN")
        if not login:
            print("prior-feedback: reviewer login missing; set REVIEWER_LOGIN or supply it as the fifth argument", file=sys.stderr)
            return 1
        argv = [*argv, login]
    if (len(argv) != 5 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in argv[:2])
            or not re.fullmatch(r"[1-9][0-9]*", argv[2]) or not re.fullmatch(r"[a-f0-9]{40}", argv[3])
            or not re.fullmatch(r"[A-Za-z0-9-]+(\[bot\])?", argv[4])):
        print("prior-feedback: supply OWNER REPO PR HEAD_SHA REVIEWER_LOGIN", file=sys.stderr)
        return 2
    owner, repo, pr, head, login = argv
    endpoints = [("review", f"repos/{owner}/{repo}/pulls/{pr}/reviews?per_page=100"),
                 ("inline", f"repos/{owner}/{repo}/pulls/{pr}/comments?per_page=100"),
                 ("comment", f"repos/{owner}/{repo}/issues/{pr}/comments?per_page=100")]
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            groups = list(zip((kind for kind, _ in endpoints), pool.map(read, (url for _, url in endpoints))))
        result = build(head, login, groups)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"prior-feedback: history unavailable: {error}; check GitHub access/response, then rerun the review; "
              "it posts no verdict without history.", file=sys.stderr)
        return 1
    print(encode(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
