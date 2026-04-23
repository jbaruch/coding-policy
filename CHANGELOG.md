# Changelog

## Unreleased

### Rules

- **author-model-declaration** — Every PR declares its author model via a `**Author-Model:**` line in the body (preferred) or a `Co-authored-by:` git trailer (fallback). Defines model families (anthropic, openai, google), plus a special `human` value that maps to no family, and mixed-authorship semantics so the paired reviewers can pick a cross-family reviewer and dodge self-review bias. Missing declaration blocks the PR via early `REQUEST_CHANGES` before the diff is read.
- **stateful-artifacts** — Cross-invocation JSON state files a skill writes and reads between runs. Every artifact has a documented schema, a single owner skill, a `schema_version` field, and a writer/reader contract. Artifacts are hints, not authority — verify against the live source before acting on a recalled value. Migrations happen under the owner skill, not as a side effect of unrelated runs.

### Rule tightenings

- **skill-authoring** — Document `user-invocable: false` as an optional frontmatter field for background-knowledge skills the runtime loads as context but the user should never invoke directly.
- **script-delegation** — Spell out the precheck-gating JSON contract with `wake_agent` as a boolean and `data` as an object (e.g., `{"wake_agent": true, "data": {}}` on the last line), the zero-token-cost rationale for no-op runs, and the single-fetch gate-and-payload pattern.
- **context-artifacts** — Enumerate the review rubric: frontmatter validity, sequential-execution preamble, flat step numbering, typed `Skill()` calls, silence-rule compliance, channel-appropriate formatting.
- **plugin-evals** — Sharpen the No-Bleeding and No-Leaking clauses after a round of policy-eval cleanup revealed the current wording was too permissive: add a task-level bleeding check (tasks demanding "exact format" or companion-doc contents force criteria to test reading), add a grep-based audit hint (if a criterion can be satisfied by grepping installed `skills/*/SKILL.md` for a literal, it is testing reading), and replace the single ambiguous "skill-prescribed approaches" carve-out with a pair of explicit clauses — public tool/API surfaces (`gh pr create`, `POST /pulls/<N>/requested_reviewers`, conventional-commits format) are allowed; tile-invented string conventions (specific reply templates, bot-ID literals, prescribed section headings) are leaking.

### Skills

- **install-reviewer** — Scaffold the paired gh-aw PR policy reviewers (OpenAI Codex + Anthropic Claude Code) into a consumer repo. Ships `review-openai.md` and `review-anthropic.md` as packaged templates; the skill copies both into the consumer's `.github/workflows/`, compiles atomically with `gh aw compile`, commits all six artifacts, and opens a PR. Each workflow runs `tessl install jbaruch/coding-policy` as a pre-step, then self-gates on the PR's `Author-Model:` declaration: the same-family reviewer emits a one-line "skipping: self-review-bias" COMMENT and exits; the cross-family reviewer reviews. Three secrets required: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TESSL_TOKEN`. Clean-verdict reviews use `event: COMMENT` with a pass body (GitHub rejects `APPROVE` from `github-actions[bot]` with HTTP 422).
- **release** — Step 2 PR body template now includes a mandatory `**Author-Model:**` line per `rules/author-model-declaration.md`; Step 4 updated to describe the paired reviewers and their self-gating behavior.

### Evals

- **install-reviewer** — 3 scenarios graded against the paired-workflow layout: 1 positive (`consumer-scaffolds-policy-reviewer`) covering the happy path through preflight → branch → copy both templates → compile → commit all six artifacts → PR with the three required secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TESSL_TOKEN`) and the cross-family rationale, and 2 negative (`install-reviewer-refuses-overwrite`, `install-reviewer-missing-gh-aw`) covering the skill's two decisional guards.

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
