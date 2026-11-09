# Release-Skill Scripting Reference

A script that runs Step 2 or Step 7 unattended (`release.sh`, `merge-and-cleanup.sh`) keeps every gate the interactive flow keeps. It calls the same scripts in the same order, gates on their exit codes, and fails loudly on any unmet gate.

- **Readiness** — gate the push on green tests and clean lint (Step 1).
- **PR title** — build or validate `<type>(<scope>): <imperative summary>`; never pass a raw title straight to `gh pr create`.
- **Watch** — run Step 5's watcher and continue only on `ready`. The watcher owns readiness; the wrapper never duplicates its conditions.
- **Reading halts the merge** — stop before the merge command while any `review_bodies[].body` or non-empty `inline_comment_bodies[].body` is unread, from every author, page and state. Resume after an agent or human reads every body and blocking findings are addressed through Step 6. The release agent may be the reader. Ordinary advisory replies and thread resolution stay outside the merge predicate. Counts, states and displayed text never satisfy this gate.
- **Branch delete** — `git branch -d`, never `-D`.
- **Worktree teardown** — from an additional worktree (`git rev-parse --git-dir` differs from `--git-common-dir`), follow Step 7 path (B) in order.
- **Publication** — run every selected channel's sequence from `skills/release/references/PUBLICATION.md`. Report the release confirmed only when every channel confirms.
