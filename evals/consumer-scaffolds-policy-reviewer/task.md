# Wire Up Automated Policy Review in a Consumer Repo

## Problem/Feature Description

A platform team's agent workflows are governed by the `jbaruch/coding-policy` tile — every coding agent in the repo follows those rules as steering. The team wants every pull request automatically reviewed against the same policy, so drift doesn't sneak in when a human (or a misbehaving agent) edits code by hand.

The repo is fresh: no `.github/workflows/review.md` or `.github/workflows/review.lock.yml` exist yet. The GitHub CLI is installed and authenticated, the `github/gh-aw` extension is installed, and `tessl install jbaruch/coding-policy` has already been run so the tile is available under `.tessl/tiles/jbaruch/coding-policy/`.

The team wants the end state to be: a pull request is open in their repo that adds the reviewer workflow, and the PR body tells the reviewer (a human team lead) exactly which repository secrets must be set before merge.

## Output Specification

Walk through the concrete commands you would run to deliver that outcome. Name each file you create and each `gh` / `git` command you invoke, in order. Capture your plan in a file named `scaffold-plan.md`.
