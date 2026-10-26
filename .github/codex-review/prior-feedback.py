#!/usr/bin/env python3
"""Read bounded prior PR feedback for review continuity, never merge authority.

Usage: prior-feedback.py OWNER REPO PR HEAD_SHA
Requires authenticated gh. Stdout is one JSON object, capped at OUTPUT_BYTES.
API/format failures warn on stderr and return an explicit unavailable context;
the reviewer must still perform a complete independent current-head pass.
No state or credentials are written. Invalid CLI arguments exit 2.
"""

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


OUTPUT_BYTES = 16000
BODY_BYTES = 3000
POLICY_AUTHORS = {"github-actions[bot]", "coding-policy-fleet-reviewer[bot]"}
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


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


def build(head, groups):
    result = {"schema_version": 1, "head_sha": head, "status": "available",
              "authority": "historical_feedback_only", "items": [], "omitted": 0}
    selected = []
    for kind, rows in groups:
        for row in rows:
            user = row.get("user")
            author = user.get("login") if isinstance(user, dict) else None
            body = row.get("body")
            if not isinstance(author, str) or not isinstance(body, str):
                raise ValueError("GitHub feedback lacks an author or text body")
            if kind != "review" and author not in POLICY_AUTHORS and row.get("author_association") not in TRUSTED_ASSOCIATIONS:
                continue
            at = row.get("submitted_at") or row.get("created_at")
            if not isinstance(at, str):
                raise ValueError("GitHub feedback lacks its timestamp")
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
    selected.sort(key=lambda row: row["at"] or "", reverse=True)
    for item in selected:
        result["items"].append(item)
        if len(encode(result).encode()) > OUTPUT_BYTES - 100:
            result["items"].pop()
            result["omitted"] += 1
    return result


def main(argv):
    if (len(argv) != 4 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in argv[:2])
            or not re.fullmatch(r"[1-9][0-9]*", argv[2]) or not re.fullmatch(r"[a-f0-9]{40}", argv[3])):
        print("prior-feedback: supply OWNER REPO PR HEAD_SHA", file=sys.stderr)
        return 2
    owner, repo, pr, head = argv
    endpoints = [("review", f"repos/{owner}/{repo}/pulls/{pr}/reviews?per_page=100"),
                 ("inline", f"repos/{owner}/{repo}/pulls/{pr}/comments?per_page=100"),
                 ("comment", f"repos/{owner}/{repo}/issues/{pr}/comments?per_page=100")]
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            groups = list(zip((kind for kind, _ in endpoints), pool.map(read, (url for _, url in endpoints))))
        result = build(head, groups)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("prior-feedback: history unavailable; check GitHub access/response and perform the complete current-head review without assuming earlier findings were resolved.", file=sys.stderr)
        result = {"schema_version": 1, "head_sha": head, "status": "unavailable",
                  "authority": "historical_feedback_only", "items": [], "omitted": None}
    print(encode(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
