---
alwaysApply: true
description: A green gate is the go-ahead; pausing to ask for approval the gate already granted is the violation.
---

# Autonomous Shipping

## Green Gate Is the Approval

- A shipping action is the autonomous next step once its gate reads green
- Read the gate to resolve doubt; never ask the operator what reading it answers
- Authorization to ship is standing — it lives in this policy and the operator's durable config, in no conversational turn. A challenging question, an ambiguous remark, an earlier "ship it when green", or silence is never the grant
- No standing grant covers the action ⇒ the gate is absent, not green (see What Still Gates)
- A harness gate this policy does not govern (safety classifier, permission prompt) is a red gate — surface what it blocked; never re-attempt through another tool

## What Counts as Approval

- Review is the paired bot fleet, not a human sign-off — a merge needs no human approval, except where another always-on rule names an owner-approval precondition (see What Still Gates)
- The merge gate has two halves, both required: `skills/release/SKILL.md` Step 7's executable preconditions (watcher-checked) AND every What Still Gates condition (agent-checked). Load Step 7 and confirm What Still Gates before merging; this rule restates neither
- A gating bot that cannot `APPROVE` posts its all-clear as `COMMENTED` — `github-actions[bot]` gets HTTP 422 (see `rules/ci-safety.md` Superseded-Bot-Review Dismissal Carve-Out)

## The Autonomous Actions

- **Open the PR** — Step 1 readiness checks pass (`skills/release/SKILL.md`) ⇒ push and open
- **Dismiss superseded bot reviews** — a bot's `CHANGES_REQUESTED` its own later all-clear superseded ⇒ dismiss it via `skills/release/dismiss-stale-reviews.sh`, or a hand dismissal meeting both preconditions (see `rules/ci-safety.md` Superseded-Bot-Review Dismissal Carve-Out)
- **Merge** — Step 7's preconditions hold ⇒ merge and clean up
- **Watch through publish** — the merge lands ⇒ watch the run, the registry advance, the moderation clear per `rules/ci-safety.md` Always Watch CI; part of the merge, not a separate decision

## Asking vs Surfacing

- An ask on a green gate is a defect — the ask manufactures the very gate the policy already opened. Report the action taken, never propose it; never re-ask what the policy grants standing
- Surface every decision the gate does NOT answer — scope, base branch, a human's prose hold, whether an out-of-scope discovery belongs in the task. These are absent grants, not green ones; re-reading this policy never resolves one. Uncertainty that the rule covers an action means it does not — report and stop

## What Still Gates

- These block a merge even when Step 7's gate reads green; the agent checks them, the watcher does not
- A platform-rejected merge — never `--admin`, never a ruleset or branch-protection bypass; surface the blocking rule
- An owner-approval precondition named by another always-on rule (`rules/ci-safety.md` Bootstrap-Red Carve-Out); no bullet here retires it
- An unplanned CI-config edit, even to turn a red gate green (`rules/ci-safety.md` Hands Off CI Config)
- Force-push, history rewrite, tag or release deletion — any action with no undo
- Direct push to a protected branch outside `rules/ci-safety.md` Content-Only Direct-Push Carve-Out
- A human reviewer's `CHANGES_REQUESTED`, a prose hold ("wait", "I'll approve after X"), or a `PENDING` human review — autonomous dismissal covers bot reviews only
- A PR marked not-ready — draft status, a `do-not-merge` or WIP label, a title marker
- A base branch the task never named — a stacked or mis-based PR is not the agreed task
- A repo the operator does not own (`rules/external-repo-contributions.md`)
- Scope never granted — an out-of-scope boy-scout discovery and drive-by dependency work are new tasks, not ship authorization (in-scope cleanup rolls into the PR per `rules/boy-scout.md` How to Apply)
