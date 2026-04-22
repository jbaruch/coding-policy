# Install Policy Reviewer When Tooling Is Missing

## Problem/Feature Description

A developer new to the team wants to set up the automated `jbaruch/coding-policy` PR reviewer in their repo. They've installed the `jbaruch/coding-policy` tile successfully via tessl, and it's present on disk at its standard location. They invoke the `install-reviewer` skill.

When the skill runs its preflight, the GitHub CLI itself is healthy and authenticated, and the installed tile is reachable. However, the GitHub CLI subcommand the skill uses to compile agentic-workflow source files into their runnable form is not available — that subcommand ships as a separate extension the developer hasn't installed yet.

## Output Specification

Walk through what the skill should do in this situation. Capture your reasoning and the exact commands (or non-commands) you would produce in a file named `preflight-plan.md`.
