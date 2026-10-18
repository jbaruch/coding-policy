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
Follow its result-specific recovery and continuation. Resolve the plugin in each
independent command block as that reference shows.

## Step 1 — Verify Readiness

Read the readiness reference:

```text
skills/release/references/step-1-readiness.md
```

Resolve only the selected checkout's authorized blockers; preserve unrelated
work. Proceed immediately to Step 2 after required readiness checks pass.

## Step 2 — Create PR

Read the pull request reference:

```text
skills/release/references/step-2-pull-request.md
```

Push the branch, create the PR in its origin repository and verify its issue
declaration. Wrappers follow the scripting requirements:

```text
skills/release/references/SCRIPTING.md
```

Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

Read the versioning reference:

```text
skills/release/references/step-3-versioning.md
```

Inspect each publication channel and apply its semver and publisher-mode
requirements. Proceed immediately to Step 4.

## Step 4 — Request Reviews

Read the review references:

```text
skills/release/references/step-4-review.md
skills/release/references/REVIEW_DETAILS.md
```

Confirm the policy reviewer was triggered and request Copilot.
Proceed immediately to Step 5.

## Step 5 — Watch PR State

Read the watcher and binding release contract:

```text
skills/release/references/step-5-watch.md
skills/release/references/release-contract.md
```

Run the canonical watcher without an invented timeout. Follow the watcher's
result-specific recovery and continuation in the step reference.

## Step 6 — Address Feedback

Read the feedback reference:

```text
skills/release/references/step-6-feedback.md
```

Read every review body and inline comment in full before judging findings.
Fix blocking policy findings and CI failures. Copilot remains advisory;
advisory-only feedback creates no repair or re-review round. Ruled findings
follow these weighing preconditions and review mechanics:

```text
skills/release/references/review-severity-carve-outs.md
skills/release/references/REVIEW_DETAILS.md
```

After each required push, re-request Copilot and return to Step 5.
Proceed immediately to Step 7 after readiness and reading obligations hold.

## Step 7 — Deliver

Read the delivery, publication and binding release contract:

```text
skills/release/references/step-7-delivery.md
skills/release/references/PUBLICATION.md
skills/release/references/release-contract.md
```

Capture each required registry baseline before merge. Merge only the ready,
reviewed head; fast-forward the base checkout before removing its worktree and
branch. Bind and watch each publisher by its exact source/event/ref. Confirm
each channel's artifacts; Tessl also requires registry advance and moderation.
Use CI carve-outs only under their stated preconditions. Skill review remains
binding for skill changes. Script output interpretation follows its contracts:

```text
skills/release/references/ci-safety-carve-outs.md
skills/release/references/skill-review-gate.md
skills/release/references/script-output-contracts.md
```

Report the merged PR, published version and each channel's confirmation.
Finish here only after the delivery reference's obligations are satisfied.
