# PR Status Monitor Script

## Problem/Feature Description

Your team uses GitHub pull requests as the primary gate for shipping code. Once a PR is open, the developer needs to wait for two independent signals before they can act: the CI pipeline (which runs tests, linters, and security checks) and an automated code reviewer that inspects the diff and leaves inline comments. Today, team members check these manually by visiting the GitHub UI and refreshing, which means they often miss feedback or start fixing things before the review is complete.

You have been asked to write a shell script that a developer can run after opening a PR to monitor both CI and the automated review status from their terminal. The script should tell the developer when both are finished and surface the review feedback — specifically the review decision state and any inline comments — so the developer knows what they need to address before they can merge.

## Output Specification

Produce a shell script named `monitor_pr.sh` that accepts a PR number as its first argument (e.g. `./monitor_pr.sh 42`). The script should be written for a repository whose GitHub owner and repo name are configurable (either hardcoded placeholders or environment variables — your choice). The script should:

1. Wait for CI checks to finish
2. Retrieve the review state(s) from the automated reviewer
3. Retrieve any inline comments left on the PR
4. Print a summary of what was found so the developer knows what to address

Also produce a `README.md` explaining how to use the script and what each output section means.
