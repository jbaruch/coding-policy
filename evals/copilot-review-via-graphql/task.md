# Automate PR Creation and Code Review Request

## Problem/Feature Description

A mid-sized engineering team has recently adopted GitHub Copilot for automated code review across all their repositories. The team's release process currently requires developers to manually open PRs and then navigate the GitHub UI to add reviewers — a tedious process that often results in PRs sitting unreviewed because developers forget the extra step. The team lead wants a reusable `release.sh` bash script that every developer can run locally to open a PR and immediately trigger a Copilot review, so nothing slips through.

The team has tried using `gh pr edit --add-reviewer` in the past but found bot accounts were silently ignored. They need an approach that reliably works with bot reviewers. The script should also handle the case where the bot's identifier becomes outdated over time, and it should confirm that the review request was actually registered before returning.

## Output Specification

Produce a `release.sh` bash script that:
- Accepts the repository owner, repository name, branch name, PR title, PR body (or constructs one from arguments), and PR number as inputs (via arguments or environment variables — your choice)
- Pushes the branch and creates the PR
- Requests a Copilot code review on that PR
- Verifies the review request was accepted
- Includes inline comments explaining any non-obvious steps

Also produce a `usage.md` file documenting how to invoke the script with example commands.

The script does not need to actually run successfully in this environment (no GitHub credentials available) — focus on correctness of the implementation.
