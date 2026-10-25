---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  dual-lens automated review (the Codex code-review app for `rules/*.md`
  compliance + Claude for correctness and risk), merge, and branch cleanup. Covers
  readiness checks, version
  reasoning, review polling, feedback handling, and post-merge verification.
  Use when the user wants to open a pull request, resume an existing PR,
  ship code, merge a branch, or handle post-merge cleanup on GitHub.
argument-hint: "[existing-pr <GitHub PR URL>]"
---

# Release Skill

Process steps in order. Do not skip ahead.

Run from readiness through publication confirmation and cleanup in one session.
Read each step's reference in full. Execute its commands and follow its
result-specific recovery and continuation. The reference owns that step's
script contracts and binding supporting reads.

## Step 1 — Verify Readiness

```text
skills/release/references/step-1-readiness.md
```

Resolve the selected checkout's authorized blockers and preserve unrelated
work. Proceed immediately to Step 2 after required readiness checks pass.

## Step 2 — Select PR

```text
skills/release/references/step-2-pull-request.md
```

For an explicit `existing-pr <URL>` invocation, reuse the source-verified PR.
Otherwise create the PR in the checkout's origin repository. Verify its issue
declaration. Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

```text
skills/release/references/step-3-versioning.md
```

Apply the inspected channels' semver and publisher-mode requirements.
Proceed immediately to Step 4.

## Step 4 — Request Reviews

```text
skills/release/references/step-4-review.md
```

Confirm both automatic reviewers were triggered.
Proceed immediately to Step 5.

## Step 5 — Watch PR State

```text
skills/release/references/step-5-watch.md
```

Run the canonical watcher. Follow the reference's result-specific recovery
and continuation without an invented timeout.

## Step 6 — Address Feedback

```text
skills/release/references/step-6-feedback.md
```

Read every review body and inline comment in full before judging findings.
Fix blocking findings from either reviewer and CI failures;
advisory-only feedback creates no repair or re-review round.
After each required push, both reviewers run automatically; return to Step 5.
Proceed immediately to Step 7 after the reference's gates are satisfied.

## Step 7 — Deliver

```text
skills/release/references/step-7-delivery.md
```

Complete its pre-merge, merge, cleanup and channel-confirmation obligations.
Report the merged PR, published version and each channel's confirmation.
Finish here only after every selected channel is confirmed.
