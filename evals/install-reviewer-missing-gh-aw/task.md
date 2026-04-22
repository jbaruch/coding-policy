# Install Policy Reviewer When Tooling Is Missing

## Problem/Feature Description

A developer is new to the team and wants to set up the automated `jbaruch/coding-policy` PR reviewer in their repo. They've run `tessl install jbaruch/coding-policy` successfully — the tile is on disk at `.tessl/tiles/jbaruch/coding-policy/`. They invoke the `install-reviewer` skill to scaffold the workflow.

When the skill runs its preflight, `gh --version` returns fine and `gh auth status` succeeds. But `gh aw --version` fails with "unknown command" — they haven't installed the `github/gh-aw` GitHub CLI extension yet, which is what compiles the workflow source file into the runnable lock file.

## Output Specification

Walk through what the skill should do in this situation. Capture your reasoning and the commands (or non-commands) you would produce in a file named `preflight-plan.md`.
