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

Run from readiness through publication confirmation and cleanup in one session.
At each step, read its named reference in full and execute that step's commands.
The step references preserve the command, failure and continuation contracts.
Resolve the plugin in each independent command block as that reference shows.

## Step 1 — Verify Readiness

Read [readiness](references/step-1-readiness.md). Resolve only the selected
checkout's authorized blockers; preserve unrelated work. Proceed immediately to
Step 2 after all required readiness checks pass.

## Step 2 — Create PR

Read [pull request creation](references/step-2-pull-request.md). Push the branch,
create the PR in its origin repository and verify its issue declaration.
Wrappers follow [scripting requirements](references/SCRIPTING.md).
Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

Read [versioning](references/step-3-versioning.md). Inspect each publication
channel and apply its semver and publisher-mode requirements.
Proceed immediately to Step 4.

## Step 4 — Request Reviews

Read [review requests](references/step-4-review.md) and
[review mechanics](references/REVIEW_DETAILS.md). Confirm the policy reviewer
was triggered and request Copilot. Proceed immediately to Step 5.

## Step 5 — Watch PR State

Read [watching](references/step-5-watch.md) and the binding
[release contract](references/release-contract.md). Run the canonical watcher
without an invented timeout. Route its terminal result through Step 6.

## Step 6 — Address Feedback

Read [feedback handling](references/step-6-feedback.md). Read every review body
and inline comment in full before judging findings. Fix blocking policy
findings and CI failures. Copilot remains advisory; advisory-only feedback
creates no repair or re-review round. Ruled findings follow
[weighing preconditions](references/review-severity-carve-outs.md) and
[review mechanics](references/REVIEW_DETAILS.md).
After each required push, re-request Copilot and return to Step 5.
Proceed immediately to Step 7 after readiness and all reading obligations hold.

## Step 7 — Deliver

Read [delivery](references/step-7-delivery.md),
[publication](references/PUBLICATION.md) and the
[release contract](references/release-contract.md).
Capture each required registry baseline before merge. Merge only the ready,
reviewed head; fast-forward the base checkout before removing its worktree and
branch. Bind and watch each publisher by its exact source/event/ref. Confirm
each channel's artifacts; Tessl also requires registry advance and moderation.
Use [CI carve-outs](references/ci-safety-carve-outs.md) only under their stated
preconditions. The [skill review gate](references/skill-review-gate.md) remains
binding for skill changes. Script output interpretation follows
[output contracts](references/script-output-contracts.md).
Report the merged PR, published version and each channel's confirmation.
Finish here only after the delivery reference's obligations are satisfied.
