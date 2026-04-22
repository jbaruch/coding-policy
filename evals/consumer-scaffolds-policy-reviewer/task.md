# Wire Up Automated Policy Review in a Consumer Repo

## Problem/Feature Description

A platform team governs its coding agents' behaviour with the `jbaruch/coding-policy` tile — every agent follows those rules as steering. The team wants every pull request in the same repo reviewed automatically against that same policy, so drift doesn't sneak in when someone edits code by hand or when an agent misbehaves.

The repo is fresh for this purpose: no automated review workflow exists yet. The GitHub CLI is installed and authenticated, the tooling that compiles agentic workflows is available, and the `jbaruch/coding-policy` tile has already been installed via tessl.

The desired end state is a pull request open against the repo that introduces the reviewer automation, with the PR description making clear to the human reviewer any repository-level configuration they need to complete before merging — otherwise the new workflow will fail on its first real run.

## Output Specification

Walk through the concrete commands you would run to reach that end state, in order. Capture your plan and the full command sequence in a file named `scaffold-plan.md`.
