# Brief — Release

Your role this round is **release**: open the pull request for `{{ISSUE}}`
and take it through the bots to merge. Read the team protocol in full before
this file.

## Setup

Your worktree is `{{WORKTREE}}`, on branch `{{BRANCH}}`, already pushed. The
lead created it. You do not.

1. Confirm where you are before anything else:

   ```bash
   cd {{WORKTREE}} && pwd && git status -sb && git log --oneline -1
   ```

2. Work only inside `{{WORKTREE}}`. Prefix every command with
   `cd {{WORKTREE}} &&`.
3. Run no git command against `{{SHARED_CHECKOUT}}` — not `pull`, not
   `worktree remove`, not a read. It is another agent's checkout. The lead
   fast-forwards it and removes your worktree after the merge.

## Before You Open the PR

Read, in `{{REPORTS_DIR}}`, the reviewer's and the tester's reports against the
pushed tip. Fix every item they mark blocking first: focused commits, the
gates `CONTRIBUTING.md` names, push. Fold an advisory in only if you are
committing anyway; otherwise list it under "Deferred advisories" in the PR
body with the follow-up issue you filed.

## Release

Run `Skill(skill: "release")` from Step 1 through Step 7, in order. The PR
title, body template, review polling, thread replies, and merge procedure are
that skill's contract; do not improvise around it. The PR body's contribution
declaration names the tools that did the work. Never paste a report file into
the PR.

## Report

Write `{{REPORT}}` covering:

- The PR URL and every review round: who posted, the verdict, what changed,
  and the reply on each thread.
- The merge commit SHA.
- Anything you chose not to do, and why.

Final chat message ends with exactly:

```
REPORT: {{REPORT}}
```
