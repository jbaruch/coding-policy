#!/usr/bin/env bash
# Dismiss the policy reviewer's gating CHANGES_REQUESTED on the PR head once a
# recorded weighing ruling rules every blocking finding in it `defer` or
# `decline` (rules/review-severity.md Judge-Weighed Finding Carve-Out;
# rules/ci-safety.md Judge-Ruled-Review Dismissal Carve-Out). This script is the
# only sanctioned path for that dismissal; a hand dismissal is not.
# Who ruled follows the mode: standalone (HERDR_ENV unset) the operator, in a
# Herdr team round (HERDR_ENV set, any value) the pinned judge
# (rules/agent-team-operation.md Judge Seat). The ruling's AUTHORITY line must
# name the authority the mode requires, and a team-round ruling must be the
# report the foreman's owner records enrolled for the pinned judge's weighing.
#
# Usage: dismiss-ruled-review.sh <owner> <repo> <pr-number> [--ruling <file> --followup-issue <number> [--task <id>]]
#   Without --ruling: list mode. Emits the blocking findings of the latest
#   policy review on the head, for composing the weighing question. Dismisses
#   nothing.
#   With --ruling: dismissal mode, under the predicate below. --followup-issue
#   is required with it: the task's follow-up issue in <owner>/<repo>. In a
#   team round --task is required with it too: the foreman's task identifier.
#
# Ruling file — a state artifact reused across pushes of one PR.
#   Owner: the release skill (skills/release/SKILL.md Step 6), which alone
#     changes its shape.
#   Writers: standalone, the release skill's agent, from the operator's answer;
#     in a team round, the pinned judge, whose weighing report
#     (skills/herdr-foreman/templates/brief-judge-weighing.md) is the ruling
#     file. One file per gate.
#   Readers, two:
#     - This script reads every line. It accepts only RULING_SCHEMA; a missing
#       schema_version line or any other version is refused (exit 1), after
#       the owner migration below. It promises nothing is posted or dismissed
#       unless the whole predicate holds.
#     - `foreman verify-ruling` (skills/herdr-foreman/foreman/cli.py
#       `cmd_verify_ruling`), called by this script for `AUTHORITY: judge`,
#       reads the file's bytes and nothing inside them. It accepts the file
#       only when its absolute path is the report of exactly one supervision
#       enrollment, whose dispatch is the pinned judge's, applied, on --task,
#       with a frozen brief carrying the judge-weighing template's marker line.
#       It promises the file's sha256 on success and a refusal message
#       otherwise; it never parses or migrates the ruling.
#   Migration (owner): a version-1 file, which only the operator ever wrote,
#     is upgraded in place before it is read: its `schema_version: 1` line
#     becomes `schema_version: 2` plus `AUTHORITY: operator`, every other line
#     kept. The rewrite changes the file's digest, so a follow-up entry posted
#     under the version-1 digest is not reused.
#   Format, schema_version 2 (lines in any order after the first; unknown lines ignored):
#     RULING: weighed                       (first line, required)
#     schema_version: 2                     (required)
#     AUTHORITY: operator | judge           (required, exactly one)
#     HEAD: <40-hex sha>                    (required, exactly one, full sha)
#     ANSWER: <operator's answer, verbatim> (required for `operator`, non-empty;
#                                            continuation lines indented two spaces)
#     FINDING: <source> <path>:<line> <rule|-> — fix | defer — <follow-up entry> | decline — <reason>
#                                           (one per nominated finding)
#     ACTION: <fix list or "none">          (optional)
#     UNVERIFIED: <… or "none">             (optional)
#
# Predicate (dismissal mode) — every condition must hold, else nothing is
# posted or dismissed and the script exits 1:
#   1. The ruling's first line is exactly `RULING: weighed`; it carries
#      `schema_version: <RULING_SCHEMA>`, exactly one `HEAD:` line and at least
#      one FINDING line; every FINDING line parses; every defer/decline line
#      carries its text.
#   1a. It carries exactly one AUTHORITY line, and that authority matches the
#      mode: `operator` standalone, with a non-empty `ANSWER:` line; `judge` in
#      a team round, where `foreman verify-ruling --task <id>` (the herdr-foreman
#      owner records) confirms the --ruling file is the report supervision
#      enrolled for the pinned judge's applied adjudication on that task.
#   2. The latest policy review (POLICY_REVIEW_LOGINS; per-login latest by
#      submitted_at, CHANGES_REQUESTED wins across logins — the same resolution
#      as poll-pr-reviews.sh) is CHANGES_REQUESTED and bound to the live head.
#      Otherwise there is nothing to dismiss: exit 0, result "noop".
#   3. Its `## Blocking findings` section parses in post-review.sh format, one
#      "- `<path>:<line>` — **<rule>** — <message>" line per finding.
#   4. FINDING lines and blocking findings pair one-to-one on path, line and
#      rule: no two blocking findings in the review share an identity, no two
#      FINDING lines share an identity, and every FINDING line names a
#      blocking finding in the review.
#   5. Every blocking finding's FINDING line is defer or decline, and either the
#      ruling's HEAD is the live head, or the compare API shows the path
#      unchanged from the ruling's HEAD to the live head (status ahead or
#      identical, file list under the API's 300-file cap).
#   6. No blocking finding's rule is in FLOOR_RULES — the rule-id floors. The
#      judgment floors are the ruling authority's to rule `fix`; this script does not
#      classify them.
#   7. No check on the head is in the `fail` bucket.
#
# Once the predicate holds, the script posts one comment on the follow-up issue
# listing every ruled finding (defer, or decline labelled won't-fix) and citing
# `judge ruling <digest>`, where <digest> is sha256(ruling file)[:16]. A comment
# whose body equals that generated entry (whitespace-trimmed) is reused; any
# other comment, even one citing the digest, is not, and the entry is posted.
# Only after
# that comment exists is the review dismissed, with the message
#   <RULED_MARKER> <digest> covers <n> blocking findings at <head>; tracked in #<issue>
# poll-pr-reviews.sh reads a dismissal carrying RULED_MARKER as state RULED, and
# dismiss-stale-reviews.sh counts it as an all-clear.
# skills/release/tests/test_dismiss_ruled_review.sh feeds the message this
# script sends to both readers and asserts they accept it.
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
RULING_SCHEMA="2"
FLOOR_RULES=(no-secrets ci-safety)
POLICY_REVIEW_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")

WORK_DIR=""

# This script's directory, resolved once while BASH_SOURCE still names this
# file, so a caller that sources it reaches the sibling herdr-foreman skill.
# Parameter expansion and a sentinel keep a trailing newline in the name (#592).
case "${BASH_SOURCE[0]}" in
  */*) _dismiss_src="${BASH_SOURCE[0]%/*}" ;;
  *) _dismiss_src=. ;;
esac
if ! DISMISS_DIR="$(CDPATH='' cd -- "${_dismiss_src:-/}" && pwd && printf x)"; then
  echo "error: cannot enter the script directory ${_dismiss_src:-/} — restore read and search access to the plugin directory, then re-run" >&2
  exit 2
fi
DISMISS_DIR="${DISMISS_DIR%x}"
DISMISS_DIR="${DISMISS_DIR%$'\n'}"

cleanup() {
  if [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  return 0
}

usage() {
  echo "usage: $0 <owner> <repo> <pr-number> [--ruling <file> --followup-issue <number> [--task <id>]]" >&2
  exit 2
}

# Pure decision over the fetched inputs in <tmp>. Exit codes: 0 no action
# (noop / findings), 1 predicate unmet, 2 input error, 3 compare needed (base
# SHA on stdout), 4 act (decision JSON on stdout; review id, message, digest
# and follow-up comment body written under <tmp>).
decide() {
  local tmp="$1" pr="$2" head="$3" ruling="$4" issue="$5" repo_slug="$6" mode="standalone"
  if [[ -n "${HERDR_ENV+x}" ]]; then
    mode="team"
  fi
  python3 - "$tmp" "$pr" "$head" "$ruling" "$issue" "$repo_slug" "$RULED_MARKER" "$RULING_SCHEMA" "$mode" \
    "${#POLICY_REVIEW_LOGINS[@]}" "${POLICY_REVIEW_LOGINS[@]}" "${FLOOR_RULES[@]}" <<'PY'
import hashlib
import json
import os
import re
import sys

tmp, pr, head, ruling_path, issue, repo_slug, marker, schema, mode, n_logins = sys.argv[1:11]
logins = sys.argv[11:11 + int(n_logins)]
floors = set(sys.argv[11 + int(n_logins):])

FINDING_RE = re.compile(r"^- `(?P<path>.+):(?P<line>\d+)` — \*\*(?P<rule>[^*]+)\*\* — (?P<message>.*)$")
RULING_LINE_RE = re.compile(
    r"^FINDING: (?P<source>\S+) (?P<path>\S+):(?P<line>\d+) (?P<rule>\S+) — "
    r"(?P<verdict>fix|defer|decline)(?: — (?P<text>.+))?$")
HEAD_RE = re.compile(r"^HEAD: (?P<sha>[0-9a-f]{40})$")
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
schemas = [ln[len("schema_version:"):].strip() for ln in ruling_lines if ln.startswith("schema_version:")]
answers = [ln[len("ANSWER:"):].strip() for ln in ruling_lines if ln.startswith("ANSWER:")]
authorities = [ln[len("AUTHORITY:"):].strip() for ln in ruling_lines if ln.startswith("AUTHORITY:")]
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
    out["unmet"].append(f"the ruling carries no single 'schema_version: {schema}' line — rewrite it in the current format")
if len(heads) != 1:
    out["unmet"].append("the ruling carries no single 'HEAD: <40-hex sha>' line — write the full commit sha the findings were raised on")
if len(authorities) != 1:
    out["unmet"].append("the ruling carries no single 'AUTHORITY: operator | judge' line")
elif mode == "standalone":
    if authorities[0] != "operator":
        out["unmet"].append(f"standalone (HERDR_ENV unset) the operator is the judge, so the ruling's authority must be 'operator', not '{authorities[0]}'")
    elif len(answers) != 1 or not answers[0]:
        out["unmet"].append("the ruling carries no single non-empty 'ANSWER:' line quoting the operator verbatim")
else:
    with open(os.path.join(tmp, "judge_binding"), encoding="utf-8") as fh:
        binding = fh.read().strip()
    if authorities[0] != "judge":
        out["unmet"].append(f"in a Herdr team round (HERDR_ENV set) the pinned judge weighs, so the ruling's authority must be 'judge', not '{authorities[0]}'")
    elif not binding.startswith("ok "):
        out["unmet"].append(f"the ruling is not the pinned judge's enrolled weighing report: {binding}")
    elif binding[len("ok "):] != hashlib.sha256(ruling_bytes).hexdigest():
        # The bytes parsed here are not the bytes the foreman verified.
        out["unmet"].append("ruling changed since verification — re-run")
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
if len(finding_keys) != len(findings):
    seen, repeated = set(), set()
    for f in findings:
        key = (f["path"], f["line"], f["rule"])
        if key in seen:
            repeated.add(f"{key[0]}:{key[1]} {key[2]}")
        seen.add(key)
    out["unmet"].append(f"the review carries duplicate blocking findings (same path, line and rule): {sorted(repeated)} — no ruling line can pair with each; fix them")
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

at_head = head == ruling_head
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

# Owner migration of a version-1 ruling file to RULING_SCHEMA; see the header.
# Exit 0 whether or not it rewrote the file, 2 when it could not.
migrate_ruling() {
  local ruling="$1"
  python3 - "$ruling" <<'PY'
import os
import sys
import tempfile

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
except (OSError, UnicodeError) as exc:
    print(f"error: cannot read the ruling at {path} as UTF-8 text ({exc}) — repair the file, or rewrite the ruling in the current format, then re-run", file=sys.stderr)
    sys.exit(2)
old = [i for i, ln in enumerate(lines) if ln.strip() == "schema_version: 1"]
if len(old) != 1 or any(ln.startswith("AUTHORITY:") for ln in lines):
    sys.exit(0)
lines[old[0]:old[0] + 1] = ["schema_version: 2", "AUTHORITY: operator"]
try:
    # An exclusive, randomly named sibling: never follows a planted symlink.
    fd, tmp = tempfile.mkstemp(prefix=".ruling-", dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
except OSError as exc:
    print(f"error: could not upgrade the version-1 ruling at {path} ({exc}) — make the file and its directory writable, then re-run", file=sys.stderr)
    sys.exit(2)
print(f"dismiss-ruled-review: upgraded the version-1 ruling at {path} to schema_version 2 (AUTHORITY: operator)", file=sys.stderr)
PY
}

# Whether the ruling file is the pinned judge's enrolled weighing report for
# <task>, from the foreman's owner records (`foreman verify-ruling`). Writes
# "ok <sha256 of the verified bytes>", or the refusal's message, to <out>.
# Returns non-zero only when the check could not run.
# decide() hashes the bytes it parses and refuses a digest that differs.
verify_judge_ruling() { # <task> <ruling> <out>
  local foreman="${DISMISS_DIR}/../herdr-foreman/foreman.sh"
  if [[ ! ( -f "$foreman" && -r "$foreman" ) ]]; then
    echo "error: ${foreman} is not readable — reinstall the coding-policy plugin, then re-run" >&2
    return 2
  fi
  local rc=0
  bash "$foreman" verify-ruling --task "$1" --ruling "$2" > "${3}.json" 2> "${3}.err" || rc=$?
  if (( rc == 0 )); then
    python3 - "${3}.json" "$3" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        digest = json.load(fh).get("sha256")
except (OSError, ValueError, AttributeError) as exc:
    print(f"error: foreman verify-ruling succeeded without a readable JSON result ({exc}) — reinstall the coding-policy plugin, then re-run", file=sys.stderr)
    sys.exit(2)
if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
    print("error: foreman verify-ruling succeeded without the verified sha256 — reinstall the coding-policy plugin, then re-run", file=sys.stderr)
    sys.exit(2)
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    fh.write("ok " + digest)
PY
    return
  fi
  # The foreman CLI contract (skills/herdr-foreman/foreman/cli.py `main`): a
  # refusal exits 1 with a JSON object {"error", "message", "details"} on
  # stderr, after any `foreman:` diagnostic lines. Only that is a binding
  # refusal; every other exit or diagnostic is a tool error.
  if (( rc != 1 )); then
    echo "error: foreman verify-ruling exited ${rc} instead of answering: $(cat "${3}.err") — repair or reinstall the coding-policy plugin, then re-run" >&2
    return 2
  fi
  python3 - "${3}.err" "$3" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        lines = fh.read().splitlines()
except (OSError, UnicodeError) as exc:
    print(f"error: cannot read foreman verify-ruling's diagnostic ({exc}) — repair or reinstall the coding-policy plugin, then re-run", file=sys.stderr)
    sys.exit(2)
start = next((i for i, ln in enumerate(lines) if ln.startswith("{")), None)
try:
    refusal = json.loads("\n".join(lines[start:])) if start is not None else None
except ValueError:
    refusal = None
if (not isinstance(refusal, dict) or not isinstance(refusal.get("error"), str)
        or not isinstance(refusal.get("message"), str) or not refusal["message"].strip()):
    print("error: foreman verify-ruling exited 1 without its refusal diagnostic — repair or reinstall the coding-policy plugin, then re-run", file=sys.stderr)
    sys.exit(2)
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    fh.write(refusal["message"])
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

# Post the follow-up comment unless an earlier run already posted the same
# generated body, byte for byte after trimming — any other comment, even one
# citing the digest, is not reused.
ensure_followup_comment() {
  local owner="$1" repo="$2" issue="$3" tmp="$4" rc=0
  gh api --paginate "repos/${owner}/${repo}/issues/${issue}/comments?per_page=100" > "${tmp}/issue_comments.json" \
    || { echo "error: failed to read comments on ${owner}/${repo}#${issue} — verify the follow-up issue number and 'gh auth status', then retry" >&2; return 1; }
  python3 - "${tmp}/issue_comments.json" "${tmp}/followup.md" <<'PY' || rc=$?
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    text = fh.read()
with open(sys.argv[2], encoding="utf-8") as fh:
    want = fh.read().strip()
decoder, i, comments = json.JSONDecoder(), 0, []
try:
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        page, i = decoder.raw_decode(text, i)
        comments.extend(page if isinstance(page, list) else [])
except ValueError as exc:
    print(f"error: the issue comments response is not JSON ({exc})", file=sys.stderr)
    sys.exit(2)
sys.exit(0 if any(isinstance(c, dict) and (c.get("body") or "").strip() == want for c in comments) else 1)
PY
  case "$rc" in
    0) echo "dismiss-ruled-review: the generated follow-up entry is already on ${owner}/${repo}#${issue} — reusing it" >&2 ;;
    1) gh api "repos/${owner}/${repo}/issues/${issue}/comments" -F body=@"${tmp}/followup.md" >/dev/null \
         || { echo "error: failed to post the follow-up comment on ${owner}/${repo}#${issue} — nothing was dismissed; fix access and re-run" >&2; return 1; } ;;
    *) echo "error: could not read the comments of ${owner}/${repo}#${issue} (rc=${rc}) — re-run" >&2; return 1 ;;
  esac
}

main() {
  [[ $# -ge 3 ]] || usage
  local owner="$1" repo="$2" pr="$3" ruling="" issue="" task=""
  shift 3
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ruling)         [[ $# -ge 2 ]] || usage; ruling="$2"; shift 2 ;;
      --followup-issue) [[ $# -ge 2 ]] || usage; issue="$2"; shift 2 ;;
      --task)           [[ $# -ge 2 ]] || usage; task="$2"; shift 2 ;;
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
  if [[ -n "$ruling" && -n "${HERDR_ENV+x}" && -z "$task" ]]; then
    echo "error: in a Herdr team round --ruling needs --task <id> — the foreman task the judge's weighing was dispatched under" >&2
    exit 2
  fi
  if [[ -z "$ruling" && ( -n "$issue" || -n "$task" ) ]]; then
    echo "error: --followup-issue and --task are only meaningful with --ruling" >&2
    exit 2
  fi
  if [[ -n "$ruling" && ! ( -f "$ruling" && -r "$ruling" ) ]]; then
    echo "error: ruling file not readable at '${ruling}' — pass the recorded ruling (the operator's, or the pinned judge's weighing report)" >&2
    exit 2
  fi
  local tool
  for tool in gh python3; do
    command -v "$tool" >/dev/null || { echo "error: ${tool} is not on PATH — install it and re-run" >&2; exit 2; }
  done
  if [[ -n "$ruling" ]]; then
    migrate_ruling "$ruling" || exit 2
  fi

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
    if [[ -n "${HERDR_ENV+x}" ]]; then
      verify_judge_ruling "$task" "$ruling" "${tmp}/judge_binding" || exit 2
    fi
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
