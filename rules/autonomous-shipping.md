---
alwaysApply: true
description: A green gate is the go-ahead; pausing to ask for approval the gate already granted is the violation.
---

# Autonomous Shipping

## Green Gate Is the Approval

- A shipping action is the autonomous next step once its gate reads green
- The gate is the approval artifact — no human ack stands between a green gate and the action it gates
- A gate is the named conjunction of fields, never an impression — each action below names the fields that must read green
- Read the gate to resolve doubt about whether it is green; never ask the operator to resolve it

## The Autonomous Actions

- **Open the PR** — Step 1 readiness checks pass (`skills/release/SKILL.md`) ⇒ push the branch and open the PR
- **Dismiss superseded bot reviews** — a gating bot's `CHANGES_REQUESTED` that the same bot's later all-clear superseded ⇒ run `skills/release/dismiss-stale-reviews.sh` (see `rules/ci-safety.md` Dismissing Superseded Bot Reviews)
- **Merge** — Step 7's conditions hold: watcher returned `ready`, every review body read, every inline thread replied (`skills/release/SKILL.md`) ⇒ merge and clean up
- **Watch through publish** — the merge lands ⇒ watch the run, the registry advance, and the moderation clear per `rules/ci-safety.md` Always Watch CI. The watch is part of the merge, not a separate decision

## Asking Is the Violation

- An ask on a green gate is a defect, not a courtesy
- The ask manufactures the approval gate — an action the policy authorized becomes blocked the moment the agent surfaces it as a question
- "The owner may want to weigh in" is not a reason to ask; the owner's position is the policy, already written
- Report the action taken, never propose it — `Dismissed 2 superseded reviews, merged #19` over `Merge #19? It needs the stale reviews dismissed first`
- A gate the operator already answered stays answered — never re-ask per PR what the policy grants standing

## What Still Gates

- Red, absent, or ambiguous gate — fix it, or surface the specific field that is not green
- Force-push to a shared branch, history rewrite, tag or release deletion, any action with no undo
- Direct push to a protected branch outside `rules/ci-safety.md` Content-Only Direct-Push Carve-Out
- A human reviewer's `CHANGES_REQUESTED` — autonomous dismissal covers bot reviews only
- Action in a repo the operator does not own (see `rules/external-repo-contributions.md`)
- Scope the operator never granted — this rule governs how to ship the agreed task, never which task to take on
