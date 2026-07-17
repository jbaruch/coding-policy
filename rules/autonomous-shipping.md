---
alwaysApply: true
description: A green gate is the go-ahead; pausing to ask for approval the gate already granted is the violation.
---

# Autonomous Shipping

## Green Gate Is the Approval

- A shipping action is the autonomous next step once its gate reads green
- A gate is a named conjunction of fields, never an impression — What Counts as Approval names every field of the merge gate
- Read the gate to resolve doubt about whether it is green
- Never ask the operator to resolve what reading the gate answers
- Authorization to ship is standing — it lives in this policy and in the operator's own durable config, and in no conversational turn
- Never read a challenging question, an ambiguous remark, an earlier "ship it when green", or silence as the grant
- Conversation sets the task's scope; it never supplies the grant to ship
- No standing grant covers the action — the gate is absent, not green (see What Still Gates)
- A harness gate this policy does not govern (a safety classifier, a permission prompt) is a red gate — surface it and name what it blocked
- Never re-attempt a harness-blocked action through another tool

## What Counts as Approval

- Review in these repos is the paired bot fleet, not a human sign-off — a merge needs no human approval, except where another always-on rule names an owner-approval precondition (see What Still Gates)
- Merge approval is `skills/release/SKILL.md` Step 7's preconditions — load Step 7 before merging; this rule never restates them and never adds an approval field
- Approval is not the whole test — What Still Gates lists conditions that block a merge even when Step 7's gate reads green, and no green gate retires them
- A gating bot that cannot `APPROVE` posts its all-clear as `COMMENTED` — `github-actions[bot]` gets HTTP 422 from GitHub (see `rules/ci-safety.md` Superseded-Bot-Review Dismissal Carve-Out)
- A human reviewer's `CHANGES_REQUESTED` overrides every bot verdict (see What Still Gates)

## The Autonomous Actions

- **Open the PR** — Step 1 readiness checks pass (`skills/release/SKILL.md`) ⇒ push the branch and open the PR
- **Dismiss superseded bot reviews** — a gating bot's `CHANGES_REQUESTED` that the same bot's later all-clear superseded ⇒ run `skills/release/dismiss-stale-reviews.sh` (see `rules/ci-safety.md` Superseded-Bot-Review Dismissal Carve-Out)
- **Merge** — `skills/release/SKILL.md` Step 7's preconditions hold ⇒ merge and clean up. What Counts as Approval is that same conjunction, never a subset
- **Watch through publish** — the merge lands ⇒ watch the run, the registry advance, and the moderation clear per `rules/ci-safety.md` Always Watch CI. The watch is part of the merge, not a separate decision

## Asking Is the Violation

- This section governs approval questions the gate already answers, and nothing else
- An ask on a green gate is a defect, not a courtesy
- The ask manufactures the approval gate — an action the policy authorized becomes blocked the moment the agent surfaces it as a question
- "The owner may want to weigh in" is not a reason to ask; the owner's position is the policy, already written
- Report the action taken, never propose it
- Never re-ask per PR what the policy grants standing

## Surfacing Is Required

- Surface every decision the gate does not answer — this is the complement of the section above, never an exception to it
- A question outside What Counts as Approval is an absent grant, never a green one: scope, base branch, a human's prose hold, whether an out-of-scope discovery belongs in this task
- Re-reading this policy never resolves an absent grant — the answer is not in it
- Uncertainty about whether the rule covers an action means it does not — report and stop

## What Still Gates

- These block a merge even when Step 7's gate reads green. The watcher does not check them — the agent does, before merging
- Red, absent, or ambiguous gate — fix it, or surface the specific field that is not green
- A platform-rejected merge is a red gate, never an obstacle to route around — never `--admin`, never a ruleset or branch-protection bypass, never "merge despite failing requirements". Surface the blocking rule
- An owner-approval precondition named by another always-on rule — `rules/ci-safety.md` Bootstrap-Red Carve-Out requires the repo owner's explicit approval, and no bullet in this rule retires it
- An unplanned CI-config edit, even to turn a red gate green (see `rules/ci-safety.md` Hands Off CI Config)
- Force-push to a shared branch, history rewrite, tag or release deletion, any action with no undo
- Direct push to a protected branch outside `rules/ci-safety.md` Content-Only Direct-Push Carve-Out
- A human reviewer's `CHANGES_REQUESTED` — autonomous dismissal covers bot reviews only
- A human's hold stated in prose — a `COMMENTED` review or comment asking to wait gates the merge as firmly as `CHANGES_REQUESTED`; a human review still `PENDING` gates it too
- A PR marked not-ready — draft status, a `do-not-merge` or WIP label, a title marker
- A base branch the task never named — merging a stacked or mis-based PR is not the agreed task
- Action in a repo the operator does not own (see `rules/external-repo-contributions.md`)
- Scope the operator never granted — this rule governs how to ship the agreed task, never which task to take on. An out-of-scope boy-scout discovery and drive-by dependency work are new tasks, never standing ship authorization (in-scope cleanup rolls into the current PR per `rules/boy-scout.md` How to Apply)
