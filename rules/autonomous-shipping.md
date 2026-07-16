---
alwaysApply: true
description: A green gate is the go-ahead; pausing to ask for approval the gate already granted is the violation.
---

# Autonomous Shipping

## Green Gate Is the Approval

- A shipping action is the autonomous next step once its gate reads green
- The gate is the approval artifact — no human ack stands between a green gate and the action it gates
- A gate is a named conjunction of fields, never an impression — What Counts as Approval names the merge gate's fields; each action below names the gate it reads
- Read the gate to resolve doubt about whether it is green; never ask the operator to resolve it
- Authorization is standing — it lives in this policy and in the operator's own durable config, never in the operator's latest message
- Never read a challenging question, an ambiguous remark, or silence as the grant — "is that in the rules?" interrogates the policy, it does not authorize an action under it
- No standing grant covers the action — the gate is absent, not green (see What Still Gates)
- A harness gate this policy does not govern (a safety classifier, a permission prompt) is a red gate like any other — surface it and name what it wants; never re-attempt the action to route around it

## What Counts as Approval

- Review in these repos is the paired bot fleet, not a human sign-off — a merge needs no human approval, and its absence is never a reason to hold a green PR
- Merge approval is the conjunction, all required: both gating bots' latest verdicts posted for the head SHA and non-`CHANGES_REQUESTED`, CI `success` or `none`, merge state mergeable, every review body read, every inline comment answered
- A verdict approves only the commit it reviewed — a review whose `commit_id` is not the PR's head SHA has not approved the current code, and reads as absent rather than clean
- A reviewer that has posted no verdict for the current head SHA has not reviewed it; a stale verdict never stands in for the missing one
- A gating bot that cannot `APPROVE` posts its all-clear as `COMMENTED` — `github-actions[bot]` gets HTTP 422 from GitHub (see `rules/ci-safety.md` Dismissing Superseded Bot Reviews)
- A human reviewer's `CHANGES_REQUESTED` overrides every bot verdict (see What Still Gates)

## The Autonomous Actions

- **Open the PR** — Step 1 readiness checks pass (`skills/release/SKILL.md`) ⇒ push the branch and open the PR
- **Dismiss superseded bot reviews** — a gating bot's `CHANGES_REQUESTED` that the same bot's later all-clear superseded ⇒ run `skills/release/dismiss-stale-reviews.sh` (see `rules/ci-safety.md` Dismissing Superseded Bot Reviews)
- **Merge** — the What Counts as Approval conjunction holds ⇒ merge and clean up (`skills/release/SKILL.md` Step 7)
- **Watch through publish** — the merge lands ⇒ watch the run, the registry advance, and the moderation clear per `rules/ci-safety.md` Always Watch CI. The watch is part of the merge, not a separate decision

## Asking Is the Violation

- An ask on a green gate is a defect, not a courtesy
- The ask manufactures the approval gate — an action the policy authorized becomes blocked the moment the agent surfaces it as a question
- "The owner may want to weigh in" is not a reason to ask; the owner's position is the policy, already written
- Report the action taken, never propose it — `Dismissed 2 superseded reviews, merged #19` over `Merge #19? It needs the stale reviews dismissed first`
- A gate the operator already answered stays answered — never re-ask per PR what the policy grants standing

## What Still Gates

- Red, absent, or ambiguous gate — fix it, or surface the specific field that is not green
- An unplanned CI-config edit, even to turn a red gate green (see `rules/ci-safety.md` Hands Off CI Config)
- Force-push to a shared branch, history rewrite, tag or release deletion, any action with no undo
- Direct push to a protected branch outside `rules/ci-safety.md` Content-Only Direct-Push Carve-Out
- A human reviewer's `CHANGES_REQUESTED` — autonomous dismissal covers bot reviews only
- Action in a repo the operator does not own (see `rules/external-repo-contributions.md`)
- Scope the operator never granted — this rule governs how to ship the agreed task, never which task to take on
