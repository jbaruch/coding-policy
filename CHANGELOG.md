# Changelog

## Unreleased

### Skills

- **install-reviewer** — Scaffold the gh-aw PR policy review workflow into a consumer repo. Ships `skills/install-reviewer/review-workflow.md` as the packaged template; the skill copies it into the consumer's `.github/workflows/`, compiles with `gh aw compile`, commits, and opens a PR. The workflow itself runs `tessl install jbaruch/coding-policy` as a pre-agent step so every consumer PR is reviewed against the latest published policy — not bleeding from `main`.

## 0.2.0

Add context artifact authoring rules and eval-authoring skill. Self-audited the tile against its own rules and iterated until 99% eval average.

### Rules

- **context-artifacts** — Plugin structure, rule format spec, lifecycle guarantees, mandatory review (with iteration guidance), mandatory evals, surface sync checklist, consistency checks
- **skill-authoring** — SKILL.md structure, frontmatter, step numbering with descriptive titles, typed calls, tile.json manifest reference
- **script-delegation** — Deterministic → script, reasoning → LLM, the regex trap, script requirements (references file-hygiene baseline)
- **plugin-evals** — No bleeding (with concrete example), no leaking (public API distinction), persistent eval coverage, negative case guidance, fixture hygiene

### Skills

- **eval-authoring** — 10-step workflow: generate, review, fix, fill coverage gaps, run evals, analyze scores, iterate. Review checklist extracted to separate file for progressive disclosure

### Changed

- **release** skill: headings aligned to `## Step N — Title` format, sequential preamble added, GraphQL queries extracted to `COPILOT_REVIEW_GRAPHQL.md`
- CI pipeline reviews both skills before publish
- Removed `docs/index.md` — all documentation consolidated in README.md entrypoint
- 9 eval scenarios: 5 positive (release), 2 negative (release), 1 quality audit (eval-authoring), 1 coverage gap analysis (eval-authoring)

## 0.1.0

Initial release.

### Rules

- **commit-conventions** — Imperative mood, ~50 char subject, body = "why", one logical change per commit, PR hygiene
- **testing-standards** — Outcome-based assertions, deterministic tests, no binary fixtures, test independence
- **error-handling** — Specific exceptions, actionable messages, graceful fallback, structured logging
- **dependency-management** — Stdlib-first, pinned versions, lock files, separate test/dev groups
- **file-hygiene** — Proper .gitignore, no generated files committed, standalone scripts, exit codes
- **ci-safety** — Never modify CI without asking, never skip tests, branch naming conventions
- **no-secrets** — No credentials in code, env vars or secrets manager, pre-commit scanning
- **code-formatting** — Use project's formatter, don't mix formatting with functional changes

### Skills

- **release** — Structured PR creation, Copilot review, merge + cleanup workflow
