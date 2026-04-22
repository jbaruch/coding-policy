---
name: PR Policy Review
description: |
  Reviews every same-repo pull request against the latest published
  `jbaruch/coding-policy` rule set. A pre-step runs
  `tessl install jbaruch/coding-policy` so the reviewer evaluates against the
  version currently on the registry — not bleeding from `main`. Fork PRs are
  skipped by gh-aw's fork-guard. Posts up to 10 inline comments plus one
  consolidated review verdict.

  Required repository secrets:
    - OPENAI_API_KEY — Codex engine authentication
    - TESSL_TOKEN    — tessl install authentication

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: read

engine:
  id: codex
  model: gpt-5.4
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

timeout-minutes: 15

network:
  allowed:
    - defaults

pre-steps:
  - name: Install Tessl CLI
    uses: tesslio/setup-tessl@v2
    with:
      token: ${{ secrets.TESSL_TOKEN }}
  - name: Install jbaruch/coding-policy (latest published)
    run: tessl install jbaruch/coding-policy --yes

tools:
  bash:
    - "cat"
    - "ls"
    - "head"
    - "tail"
    - "wc"
    - "grep"
    - "find"
    - "git diff *"
    - "git log *"
    - "git show *"
    - "gh pr diff *"
    - "gh pr view *"
  github:
    toolsets: [pull_requests]

safe-outputs:
  create-pull-request-review-comment:
    max: 10
    side: RIGHT
  submit-pull-request-review:
    max: 1
    target: triggering
    allowed-events: [APPROVE, REQUEST_CHANGES, COMMENT]
    footer: if-body
---

# Coding-Policy PR Reviewer

You review pull requests against the `jbaruch/coding-policy` rule set. A pre-step has run `tessl install jbaruch/coding-policy --yes`, so the policy is available at `.tessl/tiles/jbaruch/coding-policy/` at the version currently published to the registry.

## Context

- Repository: ${{ github.repository }}
- PR number: ${{ github.event.pull_request.number }}
- Head SHA: ${{ github.event.pull_request.head.sha }}

## Step 1 — Load the policy

List and read every file under `.tessl/tiles/jbaruch/coding-policy/rules/`. These are the authoritative policy documents for this review. Read them fully; do not skim. Also read `.tessl/tiles/jbaruch/coding-policy/skills/*/SKILL.md` when a changed path overlaps a skill's domain (e.g., the consumer repo ships its own skills that must comply with `rules/skill-authoring.md`).

## Step 2 — Load the change set

Run `gh pr diff ${{ github.event.pull_request.number }}` with no truncation. Run `gh pr view ${{ github.event.pull_request.number }} --json title,body,files`.

## Step 3 — Review

For every changed line in this PR (ignore files under `.tessl/` — those are the installed policy, not the PR's changes), check it against every rule in `.tessl/tiles/jbaruch/coding-policy/rules/`. Flag:

- Secrets, missing error handling, formatting, dependency hygiene
- Violations of `rules/ci-safety.md`, `rules/no-secrets.md`, `rules/file-hygiene.md`, etc.
- Any `skills/*/SKILL.md` change in the consumer repo that violates `rules/skill-authoring.md`

## Step 4 — Emit findings

- For each concrete violation with a file + line, call `create_pull_request_review_comment` with `path`, `line`, and a body that (a) names the rule file violated, (b) quotes the clause, (c) proposes the fix. Cap at 10 total — pick the highest-impact issues.
- After all inline comments, call `submit_pull_request_review` exactly once:
  - `event: REQUEST_CHANGES` if any violation was flagged
  - `event: APPROVE` if clean and changes are in scope
  - `event: COMMENT` if observations only (style nits, suggestions)
  - `body`: one short paragraph summarising verdict and which rules applied. Omit `body` on clean APPROVE so the `footer: if-body` rule keeps the review clean.

## Guardrails

- Ignore files under `.tessl/` — those are the installed policy, not the PR's changes.
- Do not comment on unchanged lines.
- Do not propose changes that contradict `.tessl/tiles/jbaruch/coding-policy/rules/`. The rules are ground truth.
- Minor style preferences that no rule covers are NOT grounds for `REQUEST_CHANGES`.
