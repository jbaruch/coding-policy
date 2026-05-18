# PR Status Monitor Script

## Problem/Feature Description

Your team uses GitHub pull requests as the primary gate for shipping code. Once a PR is open, the developer needs to wait for two independent signals before they can act: the CI pipeline (which runs tests, linters, and security checks) and an automated code reviewer that inspects the diff and leaves inline comments. Today, team members check these manually by visiting the GitHub UI and refreshing, which means they often miss feedback or start fixing things before the review is complete.

You have been asked to write a shell script that a developer can run after opening a PR to monitor both CI and the automated review status from their terminal. The script should tell the developer when both are finished and surface the review feedback — specifically the review decision state and any inline comments — so the developer knows what they need to address before they can merge.

## Output Specification

Produce a shell script named `monitor_pr.sh` that:

1. Accepts the PR number, repository owner, and repository name as arguments or environment variables (not hardcoded)
2. Waits for CI checks to finish
3. Retrieves the review state(s) from the automated reviewer(s)
4. Retrieves any inline comments left on the PR
5. Prints a summary of what was found so the developer knows what to address
6. Surfaces enough state for the developer to diagnose why CI may never have started (e.g., because GitHub could not build the test-merge ref), not just "still pending"
