# Agent Rules

This repository **is** the `jbaruch/coding-policy` plugin. The authoritative policy is the
in-tree `rules/*.md`; skills live under `skills/<name>/SKILL.md`. Agents working here read
and edit the live rule files directly (see `.claude/CLAUDE.md`).

## Review guidelines

These guide the Codex reviewer (`.github/workflows/review-codex.yml`, Codex CLI on a ChatGPT
subscription). This repo is the policy, so review every pull request
against its **own in-tree `rules/*.md` on the PR head branch** — read them fully, they are the
authoritative policy for the review.

- Read every `rules/*.md`. Also read any `skills/*/SKILL.md` that governs a changed path and
  check it against `rules/skill-authoring.md`.
- A new or changed `rules/*.md` must satisfy the rules it itself declares — self-consistency is
  non-negotiable, because this repo is the policy.
- Flag secrets, missing error handling, dependency hygiene, and violations of `rules/ci-safety.md`,
  `rules/no-secrets.md`, `rules/code-formatting.md`, and the rest.
- Cite each finding as `rule: <name>` (e.g. `rule: ci-safety`) with the file, the line, the clause
  violated, and the fix. Rules are ground truth — never propose a change that contradicts them.
- General code quality (correctness, bugs, security, tests) is Copilot's lane
  (`.github/copilot-instructions.md`). Focus these guidelines on policy compliance.
