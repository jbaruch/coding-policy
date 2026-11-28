---
alwaysApply: true
---

# Hook Action Reporting

## Hook Ownership

- SessionStart hooks own startup checks and automatic maintenance.
- Do not repeat a hook's work to recreate its status.

## Relay Startup Status

- Before substantive action, relay all startup `additionalContext` starting with `Session-start status — ` as one block.
- Keep each payload verbatim, including continuation lines.
- Report no status block if the hooks emit none.

## Act Within Authority

- After relaying the status, take only actions authorized under their governing rules.
- Get an explicit user decision for every listed worktree or branch.
- Offer applicable choices to commit, push, open a pull request, delete or keep the work.
- Never commit, push, open a pull request, stash, delete or discard listed work unasked.
