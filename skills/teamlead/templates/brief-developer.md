# Brief — Developer

Your role this round is **developer**. Read the team protocol in full before
this file.

## Task

Implement `{{ISSUE}}`.

## Setup

1. Create your worktree from the fresh remote default:

   ```bash
   git -C {{SHARED_CHECKOUT}} fetch origin
   git -C {{SHARED_CHECKOUT}} worktree add -b {{BRANCH}} {{WORKTREE}} origin/<default>
   ```

2. Work only inside `{{WORKTREE}}`. Prefix every command with
   `cd {{WORKTREE}} &&`.

## Before You Write Code

Read, in this order:

1. The issue `{{ISSUE}}` and every comment on it.
2. The consumers of the code you are about to change — who calls it, what they
   expect.
3. The architect's design note, posted as a comment on `{{ISSUE}}`.
4. The tester's plan in `{{REPORTS_DIR}}`.

If the design note and the tester's plan disagree, follow the design note and
record the conflict in your report.

## Implement

- Smallest change that satisfies the issue. No drive-by refactors, no
  reformatting of code you did not need to touch.
- Tests ship with the code, in the same commit series — deterministic, asserting
  outcomes.
- One logical change per commit. Imperative subject line under 72 characters, a
  body saying why.
- Run every gate `CONTRIBUTING.md` names, from inside the worktree. Green
  before you push.

## Push, Then Stop

```bash
cd {{WORKTREE}} && git push -u origin {{BRANCH}}
```

**Do not open the PR.** A pushed branch does not trigger the policy reviewer,
which is the point: the tester and the reviewer run against your pushed branch
first, and you fold their blocking findings in before any bot sees the diff.

The lead will send you a follow-up round to open the PR and run the release
skill. Until then your branch is finished work waiting for internal review.

## Report

Write `{{REPORT}}` covering:

- What you implemented and why, decision by decision.
- The branch name and every commit SHA.
- Gate output — the command you ran and its summary line.
- Anything you chose not to do, and why.
- Open questions for the reviewer.

Final chat message ends with exactly:

```
REPORT: {{REPORT}}
```
