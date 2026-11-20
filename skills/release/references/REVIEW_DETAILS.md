# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Policy reviewer — two deployments

The policy reviewer reviews the PR diff against the in-tree `rules/*.md` and posts the verdict as a PR review. It runs one of two ways depending on the repo. Normally exactly one deployment reviews a given PR; a repo mid fleet-migration can transiently carry both, which is why the release scripts resolve the verdict fail-safe across both logins (`skills/release/poll-pr-reviews.sh` login table).

Both derive the review event from per-finding severity (`rules/review-severity.md`; `.github/codex-review/post-review.sh` header) — any blocking finding posts `REQUEST_CHANGES`; nonblocking results approve with any advisory notes included, falling back to `COMMENT` only when GitHub forbids approval.

The first verdict posted on a head stands. When the posting identity already reviewed the head, a later run posts nothing and exits 0. A dismissed review counts only when `dismiss-ruled-review.sh` dismissed it, so a run after GitHub's stale-review dismissal posts again; a new commit earns a new verdict. A failed review lookup fails the run without posting (`.github/codex-review/post-review.sh` header).

### A. coding-policy's own PRs — in-repo `review-codex.yml`

The `.github/workflows/review-codex.yml` GitHub Actions workflow runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key), via `codex exec` with `.github/codex-review/prompt.md` and `schema.json`.

- **Trigger:** fires on `pull_request` `opened` / `synchronize` / `reopened` — reviews on open and re-reviews each pushed commit. Fork PRs are skipped (no secret access) — adopt via `Skill(skill: "adopt-fork-pr")`.
- **Authorship:** submitted with the workflow's `GITHUB_TOKEN`, so the author is `github-actions[bot]`.
- **APPROVE limitation:** `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so a clean pass falls back to `COMMENT`. A clean or advisory-only re-review after an earlier `CHANGES_REQUESTED` lands as a `COMMENT` that does NOT supersede the stale request in GitHub's merge gate, so Step 7 dismisses it via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the subscription token is read only from `CODEX_AUTH_JSON` at runtime, never persisted to the runner (rules/no-secrets.md); Codex refreshes the access token in-memory. No per-token API billing. If the refresh token expires the review fails loudly and the operator re-seeds the secret. One-time setup — `codex login` plus `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json` — is in the `review-codex.yml` header.

### B. Consumer repos — central `coding-policy-fleet-reviewer[bot]` App

Consumer repos carry no per-repo Codex review workflow (`review-codex.yml`) and no `CODEX_AUTH_JSON` — only a thin `.github/workflows/review-trigger.yml`, one lightweight `FLEET_DISPATCH_TOKEN` PAT, and a `.github/fleet-review-enabled` marker that enrolls the repo in the scheduled backstop poll (scaffolded by `skills/onboard-repo/scaffold.sh`). The central fleet App (coding-policy#202) does the reviewing: the fleet-review run `tessl install`s `jbaruch/coding-policy` to a temp path and symlinks its `.tessl/` into the PR workspace, so the review runs against coding-policy's `rules/*.md` (see the policy install/symlink block in `.github/codex-review/fleet-review-one.sh` `main`), not a copy the consumer pre-installed.

- **Trigger:** the consumer's in-repo `.github/workflows/review-trigger.yml` fires on the same `pull_request` events and dispatches `fleet-review.yml` (single-PR `workflow_dispatch`) in coding-policy via the `FLEET_DISPATCH_TOKEN` PAT; a scheduled marker-gated cron poll in coding-policy is the backstop for any dispatch that never fired (cadence in the `fleet-review.yml` header).
- **Authorship:** submitted as `coding-policy-fleet-reviewer[bot]`.
- **APPROVE:** the App CAN `APPROVE`, so a clean re-review supersedes its own earlier `CHANGES_REQUESTED` natively — no dismissal step is needed on consumer repos. Which reviews are dismissal-eligible is the script's decision (see the `skills/release/dismiss-stale-reviews.sh` header).

## Claude — independent code-quality review

Codex checks policy compliance. Claude uses the replacement system prompt in
.github/claude-review/system-prompt.md for correctness, bugs, security,
reliability, performance and concrete test gaps. Its isolated session loads no
policy, agent instructions, memory, plugins, skills, hooks or MCP servers and
has only read-only source tools. Authentication uses CLAUDE_CODE_OAUTH_TOKEN
from claude setup-token; the token is held only in coding-policy. Its dedicated GitHub App uses CLAUDE_REVIEW_APP_ID and CLAUDE_REVIEW_APP_PRIVATE_KEY, also held only in coding-policy (see .env.example).

Both reviews run on PR events. The central fleet workflow runs them as separate
jobs using distinct reviewer Apps; existing consumer dispatches start both. The quality result posts as a
severity-derived verdict with the exact analyzed commit and the marker owned by
`skills/release/review-lanes.sh` — `CLAUDE_REVIEW_LOGIN` and `review_lanes_jq`. Snapshot and dedup use that classifier and the separate logins. Claude posts as jbaruch-claude-reviewer[bot], independently of the Codex policy identity. Its first verdict on a head stands the same way (`.github/claude-review/review.py` docstring).

Claude approves nonblocking results, including advisory notes. An App's approval
supersedes its earlier change request; a plain comment would leave that request
active. The dedicated App can approve on coding-policy and consumer repositories.
The policy reviewer retains its existing self-review fallback and cleanup.

Both reviewers emit findings in their review bodies. Reply in a PR comment citing the review and finding; use an inline thread only for an actual inline comment. Claude blocking findings are fixed before merge.

Read both bodies before merge. On failed authentication, renew the subscription
token in coding-policy and rerun the failed job. Do not use API-key fallback or
request Copilot. The workflow pins the Claude CLI; recheck the model pin when
Renovate updates the CLI and at the weekly capability refresh.

## Weighed findings and the ruled dismissal

Reference for Step 6 — how a marginal blocking finding reaches a weighing and how the ruling clears the gating policy review (`rules/review-severity.md` Judge-Weighed Finding Carve-Out; `rules/ci-safety.md` Judge-Ruled-Review Dismissal Carve-Out).

### Who rules

The agent nominates. The judge is the operator or a software-factory judge.

- A software-factory judge rules only:
  - `decline`: the finding misreads the code or the governing policy
  - `defer`: the finding is pre-existing or outside the PR's goal, tracked in the named follow-up issue, and fixing it now costs more than the failure it prevents
- A correctly applied binding rule the judge considers defective goes to the operator. The judge cannot waive it.
- A real defect the change introduced stays the operator's risk acceptance, even when described as outside scope.
- Floors are `fix` whichever authority rules.
- Apply time-scoped obligations only within their window; an expired exception restores its ordinary rule.
- An unmet precondition is a floor only when its protected harm is reachable; cite the harm, triggering conditions and source evidence.
- Changes affecting the finding or its governing contract require checking the ruling's continued applicability. Obtain a current-head ruling when that contract or the finding's path changed; an unrelated edit does not invalidate the judgment.

### The question

Nominate only a finding on lines the previous fix push added, or one you can support with a cited reachability claim — a path from input to the flagged line, with file and line citations. A gate a software-factory judge rules wholly `decline` or `defer` needs no question. The claim travels in the question itself; no separate report is needed. Otherwise ask the operator one question per gate, carrying every nomination:

```text
Weighing request at <head sha>. For each finding answer fix, defer or decline:
1. <source> <path>:<line> <rule> — <finding text> — nominated because <added by the last fix push | reachability claim with citations>
Floors are always fix. I keep fixing meanwhile; no answer means fix.
```

The question never blocks. Keep working the fix loop; a finding the operator has not answered stays blocking.

### The ruling file

The ruling file's format, `schema_version`, owner and writer/reader contract are in the `skills/release/dismiss-ruled-review.sh` header. Keep the file for the life of the PR; the dismissal message pins its sha256 digest.

- **Operator:** write the operator's answer into a ruling file with `AUTHORITY: operator` and its `ANSWER:` line.
- **Software-factory judge:** the judge's collected report is the ruling file. Brief the judge to emit the ruling format as its report artifacts, one line per artifact, in order:

  ```text
  Rule on each blocking finding of the policy review on <owner>/<repo>#<pr> at <head sha>.
  Report these lines as your artifacts, one line per artifact, in this order, and no other artifact before them:
  RULING: weighed
  schema_version: 4
  AUTHORITY: judge
  HEAD: <head sha>
  FINDING: policy <path>:<line> <rule> — <verdict>
  One FINDING line per blocking finding below, each verdict one of:
  decline — wrong: <evidence that the finding misreads the code or the governing policy>
  defer — out-of-scope #<follow-up issue>: <evidence that it is pre-existing or outside the PR's goal>; cost: <why fixing now costs more than the failure it prevents>
  fix
  Rule fix on a floor, on a defect this change introduced, and on a correctly applied rule you consider defective; those are the operator's.
  Blocking findings:
  1. <path>:<line> <rule> — <message>
  ```

  Take the findings from the script's list mode. After collection, pass the report's absolute path, `<task>.<id>.<harness>.report.txt` beside the judge's receipt, as `--ruling` and its sha256 (`shasum -a 256 <path>`) as `--ruling-sha256`. Never edit or rewrite the report. A report the script refuses as malformed or outdated needs a new judge report; a finding it leaves uncovered goes back to the fix loop or to the operator.

### Order of work

1. Run `dismiss-ruled-review.sh` with `--ruling`, plus `--ruling-sha256` for a judge's report (Step 6 command block). It records the dispositions and ruling in the PR conversation before dismissing. Use the existing `--followup-issue` argument to record them in an authorized task issue instead. A judge's `defer` still names a follow-up issue in its ground; when the argument is supplied, it must match. Exit 1 names each unmet condition in `.unmet`; fix those findings or obtain a current-head ruling that covers them.
2. Reply citing the ruling, with `<digest>` the 16-hex digest from the script's `.message`. Policy findings appear in the review body; reply in a PR comment citing the review and finding:
   - Declined: `Declining — judge ruling <digest>: <reason from the ruling>`
   - Deferred: `Acknowledged — deferred to #<follow-up issue> (judge ruling <digest>)`
3. Run `dismiss-stale-reviews.sh` (Step 7), then re-run the Step 5 watcher. `poll-pr-reviews.sh` reads the dismissed review as `RULED`, which the watcher's ready test accepts.

A later push re-runs the policy reviewer. Run the script again with the same ruling: it dismisses the new review when the ruling still covers it, and exits 1 naming each finding it no longer covers otherwise. Those go back to a new question or the fix loop.
