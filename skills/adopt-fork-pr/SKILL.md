---
name: adopt-fork-pr
description: >
  Use when the user asks to check, review, look at, or act on a pull request by
  number — "check PR 5", "review PR 12", "what's going on with PR 7", "look at
  this PR". Classifies the PR as same-repo or fork. Fork PRs are skipped by the
  gh-aw policy reviewer's fork-guard; this skill offers to adopt the fork branch
  into the base repo as a same-repo PR the reviewer can run on, preserving the
  contributor's commits. Same-repo PRs pass straight through to the existing
  reviewer.
---

# Adopt Fork PR Skill

Process steps in order. Do not skip ahead.

Classify a referenced pull request and, when it comes from a fork, adopt its branch into the base repo as a same-repo PR the policy reviewer can run on.

## Step 1 — Classify the PR

Run `gh pr view <N> --json number,isCrossRepository,headRefName,headRepositoryOwner,author,title,url,state`.

- `isCrossRepository: false` — same-repo PR. Proceed to Step 2.
- `isCrossRepository: true` — fork PR. Proceed to Step 3.

## Step 2 — Same-Repo Passthrough

The policy reviewer already covers same-repo PRs. Report the PR's review and check status from `gh pr view <N> --json reviewDecision,statusCheckRollup` and finish here. Do not create branches, push, or open duplicate PRs.

## Step 3 — Surface the Fork and Confirm

Tell the user PR #N comes from fork `<headRepositoryOwner>/<repo>` and that the policy reviewer's fork-guard skips it. Ask with `AskUserQuestion`:

- **Adopt for review** — push the contributor's branch into the base repo as a reviewable same-repo PR.
- **Just inspect** — read-only; no adoption.

On **Just inspect**, report the diff and status, then finish here. On **Adopt for review**, proceed to Step 4.

## Step 4 — Adopt the Branch

```bash
.tessl/plugins/jbaruch/coding-policy/skills/adopt-fork-pr/adopt.sh <N>
```

Contract: see `skills/adopt-fork-pr/adopt.sh` — top-of-file docstring carries the branch-naming rule, the verbatim new-PR-body and original-PR-comment templates, the idempotency states, and the exit codes. It checks out the fork head, pushes it to a base-repo branch preserving the original commits, opens a same-repo PR, and comments on the original fork PR linking the adopted one. Emits `{ "state", "adopted_branch", "new_pr_url", "original_pr", "author" }`.

Report the adopted PR URL. The original fork PR is left open with a pointer comment — the contributor closes it on their own. The contributor's commits ride across unchanged. The `Co-authored-by:` trailer carries the Author-Model signal to the adopted PR; if the original declared the model only in its PR body, add an `**Author-Model:**` line to the adopted PR. From here it is an ordinary same-repo PR: the cross-family reviewer runs and you merge through your normal flow. Finish here.
