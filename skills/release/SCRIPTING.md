# Release-Skill Scripting Reference

When the agent's interactive Step-2 (PR create) and Step-7 (merge + cleanup) instructions get encoded into a reusable script that other devs run unattended (`release.sh`, `merge-and-cleanup.sh`, etc.), the script has to carry the SAME gates the interactive agent does — otherwise the script bypasses the conventions this skill exists to hold. Pulled out of SKILL.md to keep the main workflow scannable.

## Wrapping Step 2 (PR create) in a script

The script MUST enforce all of:

- **Step-1 readiness gate** — run the project's tests AND linter before pushing. Fail loudly if either fails. Never push from a script that doesn't gate on green tests + clean lint.
- **Conventional-commits PR title** — construct or validate the title against `<type>(<scope>): <imperative summary>`. Taking the title as a raw argument and passing it straight to `gh pr create` defeats the convention; either build the title from `<type>`, `<scope>`, `<summary>` inputs, or regex-validate the supplied title before push.
- **`**Author-Model:**` line, preferred bold form** — the script should preserve or emit the bold marker (`**Author-Model:**`) per `rules/author-model-declaration.md`'s "Explicit (preferred)" form. The reviewer prompts also accept bare `Author-Model:` as a fallback, so the script won't break by emitting the unstyled form, but a wrapper that drops the marker line entirely turns every reviewer run into an early `REQUEST_CHANGES` (the rule's "Neither present is a policy violation" clause).

## Wrapping Step 7 (merge + cleanup) in a script

The script MUST enforce all of:

- **Pre-merge gates** — same conditions the interactive agent checks: CI status is `success` (or `none`), every bot review is `APPROVED` or non-blocking `COMMENTED`, no review thread is unresolved. Fail loudly if any gate is red instead of proceeding.
- **Safe local-branch delete** — `git branch -d <branch>` (refuses to drop unmerged work), never `git branch -D`. The whole point of the cleanup is "ship and tidy"; clobbering an unmerged branch with `-D` defeats the safety the merge gate just established.
- **Post-merge verification** — confirm `main` actually advanced to the merge commit AND the publish/release workflow fired. Fire-and-forget scoring is zero because a silent publish failure is the exact problem this automation exists to catch.

## Why these gates have to be in the script, not just the skill prose

A scripted run is unattended. Anything the interactive agent enforces by reading SKILL.md only protects the sessions where the SKILL.md is in the agent's context. A script that doesn't carry the same gates internally hands every consumer a sharper version of the bypass-by-automation problem.
