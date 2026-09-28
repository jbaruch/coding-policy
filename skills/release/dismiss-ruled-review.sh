#!/usr/bin/env bash
# Dismiss the policy reviewer's gating CHANGES_REQUESTED on the PR head once the
# operator's recorded weighing ruling rules every blocking finding in it `defer`
# or `decline` (rules/review-severity.md Judge-Weighed Finding Carve-Out;
# rules/ci-safety.md Judge-Ruled-Review Dismissal Carve-Out). This script is the
# only sanctioned path for that dismissal; a hand dismissal is not.
#
# Usage: dismiss-ruled-review.sh <owner> <repo> <pr-number> [--ruling <file> --followup-issue <number>]
#   Without --ruling: list mode. Emits the blocking findings of the latest
#   policy review on the head, for composing the weighing question. Dismisses
#   nothing.
#   With --ruling: dismissal mode, under the predicate below. --followup-issue
#   is required with it: the task's follow-up issue in <owner>/<repo>.
#
# Ruling file — a state artifact reused across pushes of one PR.
#   Owner/writer: the release skill (skills/release/SKILL.md Step 6). The agent
#     writes it from the operator's answer, one file per gate.
#   Reader: this script, the only one. It refuses a missing SCHEMA line or any
#     version other than RULING_SCHEMA (exit 1); it never migrates.
#   Format, SCHEMA 1 (lines in any order after the first; unknown lines ignored):
#     RULING: weighed                       (first line, required)
#     SCHEMA: 1                             (required)
#     HEAD: <7-40 hex sha>                  (required, exactly one)
#     ANSWER: <operator's answer, verbatim> (required, non-empty; continuation
#                                            lines indented two spaces)
#     FINDING: <source> <path>:<line> <rule|-> — fix | defer — <follow-up entry> | decline — <reason>
#                                           (one per nominated finding)
#     ACTION: <fix list or "none">          (optional)
#     UNVERIFIED: <… or "none">             (optional)
#
# Predicate (dismissal mode) — every condition must hold, else nothing is
# posted or dismissed and the script exits 1:
#   1. The ruling's first line is exactly `RULING: weighed`; it carries
#      `SCHEMA: <RULING_SCHEMA>`, exactly one `HEAD:` line, a non-empty
#      `ANSWER:` line and at least one FINDING line; every FINDING line parses;
#      every defer/decline line carries its text.
#   2. The latest policy review (POLICY_REVIEW_LOGINS; per-login latest by
#      submitted_at, CHANGES_REQUESTED wins across logins — the same resolution
#      as poll-pr-reviews.sh) is CHANGES_REQUESTED and bound to the live head.
#      Otherwise there is nothing to dismiss: exit 0, result "noop".
#   3. Its `## Blocking findings` section parses in post-review.sh format, one
#      "- `<path>:<line>` — **<rule>** — <message>" line per finding.
#   4. FINDING lines and blocking findings pair one-to-one on path, line and
#      rule: no two FINDING lines share an identity, and every FINDING line
#      names a blocking finding in the review.
#   5. Every blocking finding's FINDING line is defer or decline, and either the
#      ruling's HEAD is the live head, or the compare API shows the path
#      unchanged from the ruling's HEAD to the live head (status ahead or
#      identical, file list under the API's 300-file cap).
#   6. No blocking finding's rule is in FLOOR_RULES — the rule-id floors. The
#      judgment floors are the operator's to rule `fix`; this script does not
#      classify them.
#   7. No check on the head is in the `fail` bucket.
#
# Once the predicate holds, the script posts one comment on the follow-up issue
# listing every ruled finding (defer, or decline labelled won't-fix) and citing
# `judge ruling <digest>`, where <digest> is sha256(ruling file)[:16]. A comment
# already citing that digest on the issue is reused, not reposted. Only after
# that comment exists is the review dismissed, with the message
#   <RULED_MARKER> <digest> covers <n> blocking findings at <head>; tracked in #<issue>
# poll-pr-reviews.sh reads a dismissal carrying RULED_MARKER as state RULED, and
# dismiss-stale-reviews.sh counts it as an all-clear. The marker constant is
# pinned equal across the three scripts by
# skills/release/tests/test_dismiss_ruled_review.sh.
#
# Out: one JSON object on stdout (exit 0 or 1):
#   {"pr_number": N, "head_sha": "...", "result": "dismissed|noop|findings|unmet",
#    "review_id": N|null, "reason": "...", "findings": [{path, line, rule, message}],
#    "uncovered": [{path, line, rule}], "unmet": ["..."], "message": "..."|null,
#    "followup_issue": N|null}
# Exit: 0 dismissed, noop, or findings listed; 1 predicate unmet (the `unmet`
#       list names each failed condition); 2 usage, environment or API error
#       (stderr only, stdout empty).
# Idempotent: once dismissed, the latest policy review is DISMISSED, so a re-run
# is a noop; a re-run after a failed dismissal reuses the follow-up comment.

set -euo pipefail

RULED_MARKER="JUDGE-RULED:"
RULING_SCHEMA="1"
FLOOR_RULES=(no-secrets ci-safety)
POLICY_REVIEW_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")

WORK_DIR=""

cleanup() {
  if [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  return 0
}

usage() {
  echo "usage: $0 <owner> <repo> <pr-number> [--ruling <file> --followup-issue <number>]" >&2
  exit 2
}

# Pure decision over the fetched inputs in <tmp>. Exit codes: 0 no action
# (noop / findings), 1 predicate unmet, 2 input error, 3 compare needed (base
# SHA on stdout), 4 act (decision JSON on stdout; review id, message, digest
# and follow-up comment body written under <tmp>).
decide() {
  local tmp="$1" pr="$2" head="$3" ruling="$4" issue="$5" repo_slug="$6"
  python3 - "$tmp" "$pr" "$head" "$ruling" "$issue" "$repo_slug" "$RULED_MARKER" "$RULING_SCHEMA" \
    "${#POLICY_REVIEW_LOGINS[@]}" "${POLICY_REVIEW_LOGINS[@]}" "${FLOOR_RULES[@]}" <<'PY'
import hashlib
import json
import os
import re
import sys

tmp, pr, head, ruling_path, issue, repo_slug, marker, schema, n_logins = sys.argv[1:10]
logins = sys.argv[10:10 + int(n_logins)]
floors = set(sys.argv[10 + int(n_logins):])

FINDING_RE = re.compile(r"^- `(?P<path>.+):(?P<line>\d+)` — \*\*(?P<rule>[^*]+)\*\* — (?P<message>.*)$")
RULING_LINE_RE = re.compile(
    r"^FINDING: (?P<source>\S+) (?P<path>\S+):(?P<line>\d+) (?P<rule>\S+) — "
    r"(?P<verdict>fix|defer|decline)(?: — (?P<text>.+))?$")
HEAD_RE = re.compile(r"^HEAD: (?P<sha>[0-9a-f]{7,40})$")
COMPARE_FILE_CAP = 300


def input_error(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def load_pages(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    decoder = json.JSONDecoder()
    items, i = [], 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return items
        page, i = decoder.raw_decode(text, i)
        if not isinstance(page, list):
            raise ValueError("page is not a JSON array")
        items.extend(page)


def write(name, text):
    with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
        fh.write(text)


out = {"pr_number": int(pr), "head_sha": head, "result": None, "review_id": None,
       "reason": "", "findings": [], "uncovered": [], "unmet": [], "message": None,
       "followup_issue": int(issue) if issue else None}


def finish(result, reason, code):
    out["result"] = result
    out["reason"] = reason
    print(json.dumps(out))
    sys.exit(code)


try:
    reviews = load_pages(os.path.join(tmp, "reviews.json"))
except ValueError as exc:
    input_error(f"the reviews response is not a JSON array stream ({exc}) — re-run; inspect with 'gh api --paginate repos/{repo_slug}/pulls/{pr}/reviews'")

per_login = {}
for r in reviews:
    if not isinstance(r, dict) or not r.get("submitted_at"):
        continue
    login = (r.get("user") or {}).get("login")
    if login not in logins:
        continue
    if login not in per_login or r["submitted_at"] > per_login[login]["submitted_at"]:
        per_login[login] = r
latest = next((r for r in per_login.values() if r.get("state") == "CHANGES_REQUESTED"), None)
if latest is None and per_login:
    latest = max(per_login.values(), key=lambda r: r["submitted_at"])

if latest is None:
    finish("noop", "no policy review on the PR", 0)
out["review_id"] = latest.get("id")
if latest.get("state") != "CHANGES_REQUESTED":
    finish("noop", f"latest policy review is {latest.get('state')}, not CHANGES_REQUESTED", 0)
if latest.get("commit_id") != head:
    finish("noop", "latest policy review is not bound to the live head; wait for the re-review", 0)

lines = (latest.get("body") or "").splitlines()
start = next((i for i, ln in enumerate(lines) if ln.startswith("## Blocking findings")), None)
findings = []
parse_ok = start is not None
if parse_ok:
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if not ln.strip():
            continue
        m = FINDING_RE.match(ln)
        if m is None:
            parse_ok = False
            break
        findings.append({"path": m["path"], "line": int(m["line"]), "rule": m["rule"], "message": m["message"]})
if not parse_ok or not findings:
    out["unmet"].append("the policy review body has no parseable '## Blocking findings' section")
    finish("unmet", "unparseable review body", 1)
out["findings"] = findings

if not ruling_path:
    finish("findings", "list mode: no ruling given", 0)

with open(ruling_path, "rb") as fh:
    ruling_bytes = fh.read()
ruling_lines = ruling_bytes.decode("utf-8", errors="replace").splitlines()
heads = [m["sha"] for m in (HEAD_RE.match(ln.strip()) for ln in ruling_lines) if m]
schemas = [ln[len("SCHEMA:"):].strip() for ln in ruling_lines if ln.startswith("SCHEMA:")]
answers = [ln[len("ANSWER:"):].strip() for ln in ruling_lines if ln.startswith("ANSWER:")]
entries, malformed = {}, []
duplicates = []
for ln in ruling_lines:
    if not ln.startswith("FINDING:"):
        continue
    m = RULING_LINE_RE.match(ln.rstrip())
    if m is None or (m["verdict"] != "fix" and not m["text"]):
        malformed.append(ln)
        continue
    key = (m["path"], int(m["line"]), m["rule"])
    if key in entries:
        duplicates.append(f"{key[0]}:{key[1]} {key[2]}")
        continue
    entries[key] = m.groupdict()
if not ruling_lines or ruling_lines[0].strip() != "RULING: weighed":
    out["unmet"].append("the ruling's first line is not 'RULING: weighed'")
if schemas != [schema]:
    out["unmet"].append(f"the ruling carries no single 'SCHEMA: {schema}' line — rewrite it in the current format")
if len(heads) != 1:
    out["unmet"].append("the ruling carries no single 'HEAD: <sha>' line")
if len(answers) != 1 or not answers[0]:
    out["unmet"].append("the ruling carries no single non-empty 'ANSWER:' line quoting the operator verbatim")
if malformed:
    out["unmet"].append(f"unparseable FINDING line(s): {malformed}")
if duplicates:
    out["unmet"].append(f"duplicate FINDING line(s): {duplicates}")
if not entries and not malformed:
    out["unmet"].append("the ruling carries no FINDING line")
if out["unmet"]:
    finish("unmet", "malformed ruling", 1)
ruling_head = heads[0]

finding_keys = {(f["path"], f["line"], f["rule"]) for f in findings}
unmatched = sorted(f"{k[0]}:{k[1]} {k[2]}" for k in entries if k not in finding_keys)
if unmatched:
    out["unmet"].append(f"FINDING line(s) naming no blocking finding in the review: {unmatched}")

floor_hits = sorted({f["rule"] for f in findings if f["rule"] in floors})
if floor_hits:
    out["unmet"].append(f"blocking finding(s) under a floor rule: {floor_hits} — fix them")

try:
    checks = load_pages(os.path.join(tmp, "checks.json"))
except ValueError as exc:
    input_error(f"the checks response is not JSON ({exc}) — inspect with 'gh pr checks {pr} --repo {repo_slug} --json name,bucket'")
failing = sorted({c.get("name", "?") for c in checks if isinstance(c, dict) and c.get("bucket") == "fail"})
if failing:
    out["unmet"].append(f"failing check(s) on the head: {failing} — fix them")

at_head = head.startswith(ruling_head)
changed = None
if not at_head:
    compare_path = os.path.join(tmp, "compare.json")
    if not os.path.exists(compare_path):
        print(ruling_head)
        sys.exit(3)
    try:
        with open(compare_path, encoding="utf-8") as fh:
            compare = json.load(fh)
    except ValueError as exc:
        input_error(f"the compare response is not JSON ({exc}) — inspect with 'gh api repos/{repo_slug}/compare/{ruling_head}...{head}'")
    files = compare.get("files") or []
    if compare.get("status") in ("ahead", "identical") and len(files) < COMPARE_FILE_CAP:
        changed = set()
        for f in files:
            changed.add(f.get("filename"))
            if f.get("previous_filename"):
                changed.add(f["previous_filename"])

ruled = []
for f in findings:
    e = entries.get((f["path"], f["line"], f["rule"]))
    carried = at_head or (changed is not None and f["path"] not in changed)
    if e is None or e["verdict"] == "fix" or not carried:
        out["uncovered"].append({"path": f["path"], "line": f["line"], "rule": f["rule"]})
        continue
    ruled.append((f, e))
if out["uncovered"]:
    why = "" if at_head or changed is not None else " (the compare from the ruling's HEAD is diverged or over the file cap, so no carry-over)"
    out["unmet"].append(f"{len(out['uncovered'])} blocking finding(s) not covered by a defer/decline ruling{why}")
if out["unmet"]:
    finish("unmet", "predicate unmet", 1)

digest = hashlib.sha256(ruling_bytes).hexdigest()[:16]
body = [f"Follow-up entries for {repo_slug}#{pr} at {head}, judge ruling {digest}:", ""]
for f, e in ruled:
    label = "deferred" if e["verdict"] == "defer" else "declined, won't-fix"
    body.append(f"- `{f['path']}:{f['line']}` **{f['rule']}** — {label}: {e['text']}")
message = f"{marker} {digest} covers {len(findings)} blocking findings at {head}; tracked in #{issue}"
out["message"] = message
write("review_id", str(latest["id"]))
write("message", message)
write("digest", digest)
write("followup.md", "\n".join(body) + "\n")
finish("dismissed", "every blocking finding is covered by the ruling", 4)
PY
}

fetch_checks() {
  local owner="$1" repo="$2" pr="$3" dest="$4" out rc=0
  out=$(gh pr checks "$pr" --repo "${owner}/${repo}" --json name,bucket 2>"${dest}.err") || rc=$?
  # gh pr checks exits 8 while checks are pending; its JSON is still the list.
  if (( rc == 0 || rc == 8 )); then
    printf '%s' "$out" > "$dest"
    return 0
  fi
  if grep -qi "no check" "${dest}.err"; then
    printf '[]' > "$dest"
    return 0
  fi
  echo "error: 'gh pr checks ${pr} --repo ${owner}/${repo}' failed (rc=${rc}): $(cat "${dest}.err") — run 'gh auth status', then retry" >&2
  return 1
}

# Post the follow-up comment unless one citing the same ruling digest exists.
ensure_followup_comment() {
  local owner="$1" repo="$2" issue="$3" tmp="$4" digest rc=0
  digest=$(cat "${tmp}/digest")
  gh api --paginate "repos/${owner}/${repo}/issues/${issue}/comments?per_page=100" > "${tmp}/issue_comments.json" \
    || { echo "error: failed to read comments on ${owner}/${repo}#${issue} — verify the follow-up issue number and 'gh auth status', then retry" >&2; return 1; }
  grep -qF "judge ruling ${digest}" "${tmp}/issue_comments.json" || rc=$?
  case "$rc" in
    0) echo "dismiss-ruled-review: follow-up comment for ruling ${digest} already on ${owner}/${repo}#${issue} — reusing it" >&2 ;;
    1) gh api "repos/${owner}/${repo}/issues/${issue}/comments" -F body=@"${tmp}/followup.md" >/dev/null \
         || { echo "error: failed to post the follow-up comment on ${owner}/${repo}#${issue} — nothing was dismissed; fix access and re-run" >&2; return 1; } ;;
    *) echo "error: could not search the comments of ${owner}/${repo}#${issue} (grep rc=${rc}) — re-run" >&2; return 1 ;;
  esac
}

main() {
  [[ $# -ge 3 ]] || usage
  local owner="$1" repo="$2" pr="$3" ruling="" issue=""
  shift 3
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ruling)         [[ $# -ge 2 ]] || usage; ruling="$2"; shift 2 ;;
      --followup-issue) [[ $# -ge 2 ]] || usage; issue="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  if [[ -z "$owner" || -z "$repo" ]] || ! [[ "$pr" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: <owner> and <repo> must be non-empty and <pr-number> a positive integer" >&2
    exit 2
  fi
  if [[ -n "$ruling" ]] && ! [[ "$issue" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: --ruling needs --followup-issue <number> — the task's follow-up issue in ${owner}/${repo}, where the ruled findings are entered" >&2
    exit 2
  fi
  if [[ -z "$ruling" && -n "$issue" ]]; then
    echo "error: --followup-issue is only meaningful with --ruling" >&2
    exit 2
  fi
  if [[ -n "$ruling" && ! ( -f "$ruling" && -r "$ruling" ) ]]; then
    echo "error: ruling file not readable at '${ruling}' — pass the recorded operator ruling" >&2
    exit 2
  fi
  local tool
  for tool in gh python3; do
    command -v "$tool" >/dev/null || { echo "error: ${tool} is not on PATH — install it and re-run" >&2; exit 2; }
  done

  WORK_DIR=$(mktemp -d) || { echo "error: mktemp -d failed — check TMPDIR is writable, then re-run" >&2; exit 2; }
  trap cleanup EXIT
  local tmp="$WORK_DIR"

  local head
  head=$(gh pr view "$pr" --repo "${owner}/${repo}" --json headRefOid --jq .headRefOid) \
    || { echo "error: failed to read the head of ${owner}/${repo}#${pr} — run 'gh auth status', then retry" >&2; exit 2; }
  [[ -n "$head" ]] || { echo "error: ${owner}/${repo}#${pr} returned no headRefOid — verify the PR number" >&2; exit 2; }
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" > "${tmp}/reviews.json" \
    || { echo "error: failed to fetch reviews for ${owner}/${repo}#${pr} — run 'gh auth status', then retry" >&2; exit 2; }
  if [[ -n "$ruling" ]]; then
    fetch_checks "$owner" "$repo" "$pr" "${tmp}/checks.json" || exit 2
  fi

  local decision rc=0
  decision=$(decide "$tmp" "$pr" "$head" "$ruling" "$issue" "${owner}/${repo}") || rc=$?
  if (( rc == 3 )); then
    gh api "repos/${owner}/${repo}/compare/${decision}...${head}" > "${tmp}/compare.json" \
      || { echo "error: failed to compare ${decision}...${head} on ${owner}/${repo} — verify the ruling's HEAD exists in the repo, then retry" >&2; exit 2; }
    rc=0
    decision=$(decide "$tmp" "$pr" "$head" "$ruling" "$issue" "${owner}/${repo}") || rc=$?
  fi
  case "$rc" in
    0) printf '%s\n' "$decision"; exit 0 ;;
    1) printf '%s\n' "$decision"
       echo "dismiss-ruled-review: predicate unmet on ${owner}/${repo}#${pr} — see .unmet; fix the findings or obtain a ruling that covers them" >&2
       exit 1 ;;
    4) ;;
    *) exit 2 ;;
  esac

  ensure_followup_comment "$owner" "$repo" "$issue" "$tmp" || exit 2

  local review_id message
  review_id=$(cat "${tmp}/review_id")
  message=$(cat "${tmp}/message")
  gh api -X PUT "repos/${owner}/${repo}/pulls/${pr}/reviews/${review_id}/dismissals" \
    -f message="$message" -f event="DISMISS" >/dev/null \
    || { echo "error: failed to dismiss review ${review_id} on ${owner}/${repo}#${pr} — the token needs pull-request write access; re-run once fixed (the follow-up comment is reused)" >&2; exit 2; }
  printf '%s\n' "$decision"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
