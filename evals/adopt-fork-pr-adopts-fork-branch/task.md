# Get an External Contributor's PR Reviewed by the Policy

## Problem/Feature Description

A repository has the `jbaruch/coding-policy` automated PR reviewer installed and running — every pull request normally gets a policy verdict from it.

An outside contributor opened pull request #6 against the repo, proposing a change from their own copy of the project under their personal GitHub account. The maintainer expects the automated policy review to weigh in, but nothing from the reviewer ever appears on #6 — no verdict, no inline comments, not even a skipped-review note. Other pull requests in the repo get reviewed normally.

The maintainer wants this contribution to receive the same automated policy review every other pull request gets, and wants the contributor to keep credit for their work. The GitHub CLI is installed and authenticated with write access to the repo.

## Output Specification

Walk through what you would do, in order, to get this contribution reviewed by the policy reviewer. Name the concrete commands you would run. Capture your plan and command sequence in a file named `review-plan.md`.
