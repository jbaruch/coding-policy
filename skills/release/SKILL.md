---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  dual-lens automated review (the Codex code-review app for `rules/*.md`
  compliance + Copilot for correctness and risk), merge, and branch cleanup. Covers
  readiness checks, version
  reasoning, review polling, feedback handling, and post-merge verification.
  Use when the user wants to open a pull request, ship code, merge a branch,
  or handle post-merge cleanup on GitHub.
---

# Release Skill

Process steps in order. Do not skip ahead.

Structured workflow for shipping code: PR creation, automated policy review, merge, and cleanup. Do not stop between steps; the skill runs end-to-end from `git push` through merge + cleanup verification in a single agent session.

## Step 1 — Verify Readiness

Nothing below runs until this exits 0:

```bash
skills/release/check-leftovers.sh
```

Exit 0 clears the release. Exit 1 blocks it, with `blocking` naming each leftover and the stderr diagnostic naming its worktree — commit, stash, or gitignore the work, then re-run. Exit 2 is a usage or tool-state error, never a verdict. Which worktree states it refuses, and why another worktree's work in progress does not trip it, are the script's decision contract — see `skills/release/check-leftovers.sh` header, not restated here (`rules/script-as-black-box.md`).

- Confirm you're on a feature branch (not `main`/`master`)
- Run the test suite — all tests must pass
- Run the linter — no warnings or errors
- Self-audit the diff against every governing rule or skill whose domain covers the touched paths (e.g., auto-loaded prose in `rules/` or `skills/` → `rules/context-writing-style.md`; new scripts → `rules/script-delegation.md` and `rules/testing-standards.md`)
- Grep the diff for the literal markers each governing rule or skill names:
  - banned connectives `because`, `therefore`, `since`, etc. per `rules/context-writing-style.md`
  - the `outer-boundary-process-contract` token per `rules/error-handling.md`
- Run any local check the rule or skill prescribes:
  - `bash -n <script>` must exit 0 on shell scripts
  - the script's own fixture test must pass
- The policy reviewer is a backstop, not the first read
- If anything fails, fix it before proceeding

## Step 2 — Create PR

- Once Step 1's readiness checks pass, create the PR automatically — the green readiness checks are the gate. Do not pause to ask a human whether to open it.
- Push the branch: `git push -u origin <branch>`
- Create the PR with `gh pr create`:
  - **Title**: `<type>(<scope>): <imperative summary>`
  - **Body**:
    ```
    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps>
    ```

When this step is wrapped in a reusable script (e.g., `release.sh` that other devs run unattended), see the script-wrapping gates at:

```text
skills/release/SCRIPTING.md
```

Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

Decide the bump per semver, then apply it the way the channels named in Step 7 require:

- **Tessl** — patch is the default and is auto-bumped by the publish workflow's `smart-publish` step; update the manifest version only for minor or major
- **GitHub tag/asset** — nothing auto-bumps here. Write every bump, patch included, into the package manifest in this PR (`agent-plugin.yaml` for an ACR package), and cut Step 7's release tag at that same version
- **Both** — set one version explicitly in every channel's manifest in this PR and tag that version. Verify that the actual Tessl publishing workflow preserves it without auto-bumping: use `publish-mode: as-is` with `.github/workflows/publish-plugin.yml`, or `mode: as-is` when calling `.github/actions/smart-publish` directly. For another publisher, inspect its supported mechanism before proceeding. Keep the required skill review, registry confirmation and moderation checks enabled

## Step 4 — Policy Review Fires Automatically

Opening the PR, or pushing further commits to an existing PR, automatically triggers the policy reviewer, which reviews the diff against the in-tree `rules/*.md` and posts a verdict. The machinery depends on the repo:

- **coding-policy's own PRs** — the in-repo `review-codex.yml` workflow (Codex CLI on a ChatGPT subscription), posting as `github-actions[bot]`.
- **Consumer repos** — the central `coding-policy-fleet-reviewer[bot]` GitHub App; the in-repo `review-trigger.yml` dispatches it on each `pull_request` event, with a scheduled marker-gated poll as backstop (coding-policy#202).

In both, the PR event (`opened` / `synchronize` / `reopened`) is the primary trigger. A plain `git push` to a non-PR branch does NOT fire a review; the consumer path's scheduled poll is only a backstop for a dispatch that never fired. Fork PRs are skipped in both — no secret or token access to a fork head. Adopt a fork PR via `adopt-fork-pr` (works in any repo where the policy review is fork-guarded). See the trigger / authorship / dismissal mechanics for both at:

```text
skills/release/REVIEW_DETAILS.md
```

**Also request Copilot.** Copilot is a deliberate second reviewer with a different lens — the policy reviewer enforces `rules/*.md` compliance, Copilot reads for correctness, bugs, security, and test gaps. The policy reviewer gates the merge only on **blocking** findings; advisory-only reviews post `COMMENT` and never gate, and Copilot is always advisory (read it, never gate on it) — see `rules/review-severity.md`:

```bash
skills/release/request-copilot-review.sh <owner> <repo> <pr-number>
```

Proceed immediately to Step 5.

## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls `poll-pr-reviews.sh` at a script-owned interval up to a script-owned budget and watches exactly the fields the Step 7 merge gate reads — each gating bot's latest review state (resolved by bot login), CI status, and merge state. Do not hand-roll a poll loop, and do not wrap the watch in an invented wall-clock `timeout` (see `rules/ci-safety.md` "Always Watch CI"):

```bash
skills/release/watch-pr-reviews.sh <owner> <repo> <pr-number>
```

It returns the full `poll-pr-reviews.sh` snapshot plus a `watch` object — `{"result": ..., "attempts": N, "elapsed_seconds": N}`. The interval/budget constants and the result contract are the script's, not restated here (`rules/script-as-black-box.md` — see the header's result matrix). Branch on `.watch.result`:

- `ready` (exit 0) — mergeable, CI `success`/`none`, both bots posted, the policy reviewer not `CHANGES_REQUESTED` (Copilot may be, and this still reaches ready — it is always advisory). Read every non-empty `reviews.*.body` (a `COMMENTED` verdict with zero inline comments still carries a body per `rules/reviewer-feedback-reading.md`), then proceed to Step 6.
- `changes_requested` (exit 0) — the policy reviewer requested changes (a blocking finding). Go to Step 6, address it, push; the next push re-fires the review, so re-run the watcher.
- `ci_failure` (exit 0) — a check failed. Fix it (Step 6), push, re-run the watcher.
- `dirty` (exit 0) — the branch conflicts with `main` and GitHub skipped the `pull_request:` workflows. Rebase onto current `main`, resolve, force-push, then re-run the watcher — the push re-fires the missed workflows.
- `review_unrequested` (exit 1) — Copilot has no verdict at this head and no pending request, so no wait can produce one. Request it with `skills/release/request-copilot-review.sh <owner> <repo> <pr-number>` and re-run the watcher. The policy reviewer runs on the push and never produces this result.
- `pending_at_budget` (exit 1) — a signal never arrived within the budget (a reviewer that never posted, CI stuck pending). Inspect which field is still `none`/`pending` in the returned snapshot. If the policy reviewer never posted on coding-policy's own PRs, check the `review-codex.yml` run (`gh run list --workflow review-codex.yml`) — a missing or expired `CODEX_AUTH_JSON` secret is the usual cause. On a consumer repo, confirm `review-trigger.yml` dispatched and the fleet App ran in coding-policy; its scheduled poll is the backstop. Re-run the watcher to keep waiting once the cause is understood.

## Step 6 — Address Feedback

- **Read every review in full first.** Read each reviewer's `reviews.*.body` and every inline comment body before judging any item — a `COMMENTED` state or zero inline comments is not a license to skip the body (see `rules/reviewer-feedback-reading.md`)
- **Then act by severity** (see `rules/review-severity.md`): blocking findings — fix now; advisory findings — acknowledge, fold in only when a blocking round is already happening, else defer to a follow-up. Never burn a dedicated re-review round on a lone advisory
- **CI failures**: Fix every one
- **Review suggestions**: Apply what's right. Push back on anything that misreads scope — cite concrete evidence (file:line, log line, spec quote) when declining
- **Reply on EVERY thread.** Use these exact opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
  - Advisory deferred: `Acknowledged — deferred to <follow-up ref>` (em dash `—`; names where it is tracked)
- Push fixes to the same branch
- **Re-request Copilot after every push** via `skills/release/request-copilot-review.sh` (same args as Step 4). Copilot does not re-post on its own.
- The policy reviewer re-runs automatically on every push (coding-policy via `review-codex.yml` `pull_request: synchronize`; consumers via `review-trigger.yml` re-dispatching the fleet App). No manual re-request.
- Repeat Step 5 until the policy reviewer carries no blocking finding — `APPROVED`, or `COMMENTED` with its body read and only advisories — and every thread has a reply.

## Step 7 — Merge + Cleanup

Only proceed when:
- Step 5's watcher returned `.watch.result` as `ready` — its exit-0 readiness conjunction (mergeable, CI `success`/`none`, both bots posted, the policy reviewer not `CHANGES_REQUESTED`); the field predicate is the watcher's, not restated here (`rules/script-as-black-box.md` — see `skills/release/watch-pr-reviews.sh` header). `ready` already requires each bot's `state` to have left `none`, so a reviewer that never ran cannot satisfy the gate vacuously, AND
- Every non-empty `reviews.*.body` in the returned snapshot has been read in full — a `COMMENTED` state with zero inline comments is not a license to skip the body (see `rules/reviewer-feedback-reading.md`), AND
- Every inline comment from Step 5's `inline_comments` count has a `Fixed in <sha>`, `Declining — <reason>`, or `Acknowledged — deferred to <follow-up ref>` reply per Step 6 (verify by listing the PR's review comments — the poll script tracks counts, not reply state, so the operator confirms thread closure). An advisory comment deferred with the `Acknowledged — deferred` reply closes its thread and never blocks the merge per `rules/review-severity.md`.

A `COMMENTED` review never gates the merge on its state alone — but its body must be read before merge, zero inline comments included. With inline comments, it is mergeable once every thread also has a reply. Advisory findings (the reviewer's `## Advisory findings` section, and every Copilot comment) do not block the merge — acknowledge them and defer per `rules/review-severity.md`; only a blocking finding gates.

Once these conditions hold, merge automatically per `rules/ship-on-green.md` — the green gates are the approval, stakes raise care not permission, and the only blocks are its three objective exits (Red / No undo / Murky). Do not pause to ask a human whether to merge.

**Clear superseded review gates first.** This applies to coding-policy's OWN releases, where the policy reviewer posts as `github-actions[bot]`, which cannot `APPROVE` (GitHub returns HTTP 422), so a clean re-review lands as a `COMMENT` that does NOT supersede the bot's earlier `CHANGES_REQUESTED` — the stale request keeps `merge_state.status` at `BLOCKED`. On consumer repos the reviewer is the central fleet App `coding-policy-fleet-reviewer[bot]` (coding-policy#202), which CAN `APPROVE` and supersedes its own earlier `CHANGES_REQUESTED` directly — no dismissal needed there. Dismiss every superseded `github-actions[bot]` review before merging:

```bash
skills/release/dismiss-stale-reviews.sh <owner> <repo> <pr-number>
```

Run it once Step 5's poll shows every bot's latest verdict clean. It emits a JSON summary of what it dismissed and what it left active, exits non-zero on API failure, and is idempotent on re-run. Which reviews it dismisses and which it leaves is the script's decision contract — see `skills/release/dismiss-stale-reviews.sh` header, not restated here (`rules/script-as-black-box.md`).

**Name this repo's publication channels before merging.** The confirmation a release owes is keyed on the publication, never on the package — a package that publishes through more than one channel owes the duty once per publication, each confirmed against the channel that carried it (`rules/ci-safety.md` Always Watch CI). Read the repo's publish workflow and its manifest, and name every channel it publishes on. How each channel is recognized, the command for every gate below, each helper's exit-code contract, and a walkthrough of the Tessl-only, tag/asset-only and mixed cases:

```text
skills/release/PUBLICATION.md
```

**Tessl publication:** capture the registry baseline into `PRE` with `registry-baseline.sh` before merging. A publication on another channel skips this gate.

Pick the right cleanup path based on where you ran the skill from.

**(A) From the base checkout (no additional worktree):**

```bash
# Merge
gh pr merge <N> --merge --delete-branch

# Update local
git checkout main && git pull --ff-only

# Clean up local branch
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

**(B) From an additional worktree** (per `rules/agent-worktree-isolation.md`):

```bash
# Merge (running from inside the worktree is fine)
gh pr merge <N> --merge --delete-branch

# Return to the base checkout, fast-forward main
cd <path-to-base-checkout>
git checkout main && git pull --ff-only

# Tear down the worktree — directory + `.git/worktrees/` metadata
git worktree remove <path-to-worktree>

# Now safe: branch is no longer checked out anywhere and is fully merged
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

Order in (B) is mandatory: `git branch -d` refuses to delete a branch that is checked out in any worktree, so the `git worktree remove` step must come before `git branch -d`. Reversing the order produces a "checked out at `<path>`" error and leaves a stranded branch.

After merge — per `rules/ci-safety.md`'s Always Watch CI duty extended through release, run each gate its channels owe, in order:

- Verify the merge landed on main (`git pull --ff-only` succeeds; `git log -1 --oneline` shows the merge commit)
- **GitHub tag/asset publication:** push the release tag from the fast-forwarded `main` before resolving anything. Its publish workflow fires on the tag, never on the merge. The version follows Step 3
- **Every publication, whatever channel carries it:** resolve that publication's own run with `resolve-publish-run.sh`
- Bind that resolution to the workflow, the exact commit, the `push` event and the ref that fired it, never to "latest on main"
- Watch the resolved run to a terminal state
- Require its `conclusion` to be `success`
- Each channel keeps its own run id; a mixed publication holds both at once, and each confirmation below reads the id for its own channel
- **Tessl publication:** confirm conjuncts 1 and 2 with `confirm-tessl-landed.sh` — the resolved run's `conclusion == success` AND the registry's `Latest Version > PRE`
- Gate on that helper's exit code
- Keep the version it prints for the moderation gate
- A non-zero exit stops the release there, with no fall-through to moderation
- Do not compare against a specific expected version
- **Tessl publication:** confirm conjunct 3 with `verify-moderation-cleared.sh` on that `current` version. A freshly published version can be install-blocked until its moderation state reaches `pass`. Never report the release confirmed until this clears
- **GitHub tag/asset publication:** confirm its own two conjuncts with `verify-github-release.sh` — the resolved run's `conclusion` is `success`, AND the release exists at that exact tag, is published, and carries retrievable assets
- Run none of the three Tessl helpers for a tag/asset publication
- Report the outcome: merged PR URL, the version published, and each publication's own confirmation — registry advance plus moderation clear for a Tessl publication, the published release and its retrievable assets for a tag publication, both for a package on both channels

When this step is wrapped in a reusable script (e.g., `merge-and-cleanup.sh` that other devs run unattended), see `skills/release/SCRIPTING.md` for the gates the script must enforce.

Finish here — the skill is complete.
