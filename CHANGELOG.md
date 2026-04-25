# Changelog

## Unreleased

## 0.2.0

First formal minor since 0.1.0 — promotes everything accumulated across the 0.1.x patch line into a tagged release, plus eval-driven skill tuning that lifted the with-context aggregate from 93 to 98 (lift +17 → +22). Per-scenario wins from the tuning pass: Coverage Gap 74 → 100 (+26), Review Response 85 → 97 (+12), Copilot via GraphQL 89 → 97 (+8), PR Merge Cleanup verified at 99 (held). Three negative-lift / low-with-context regressions in v0.1.20 were diagnosed against the run-019dbffe artifacts and fixed at the skill layer rather than by softening criteria.

### Skill tuning (driven by eval log analysis)

- **eval-authoring** — Step 7 (Fill Coverage Gaps) now spells out the `criteria.json` wrapper format (`{context, type: "weighted_checklist", checklist: [...]}`) with a code example, requires `max_score` weights to sum to exactly 100, and warns against mirroring plain-array seed fixtures from the test repo under evaluation. Step 7 also makes the Steps-4–5 review/fix loop explicit for newly-authored scenarios — the prior "Repeat Steps 4–6" got skipped on self-authored content. Step 5 (Fix Issues) now states that a misaligned, leaking, or otherwise unsalvageable criterion must be **removed and the remaining criteria reweighted to sum to 100** — bumping a bad criterion's weight to keep the math tidy makes the scenario worse, not better. Eliminated the Coverage Gap regression (74 → 100) and the Quality Audit "increase the misaligned criterion's weight" failure mode.
- **install-reviewer** — Step 1 (Preflight) now states the gh-aw extension's canonical owner in SKILL.md prose (`gh extension install github/gh-aw`, with an explicit note that the extension lives under the `github` org, not the tile owner). Previously this string lived only inside `preflight.sh`'s failure message, where the agent overlooked it and substituted `jbaruch/gh-aw` from tile-owner pattern-matching.
- **release** — Step 2 (Create PR) and Step 7 (Merge + Cleanup) each gained a "When wrapping this step in a reusable script" paragraph: scripts that automate PR creation must enforce the tile's readiness gate (tests + linter), construct/validate the conventional-commits title format, and pass through the `Author-Model:` line; scripts that automate merge must enforce the same pre-merge gates the agent does (CI green, every bot review `APPROVED` or non-blocking `COMMENTED`, no unresolved threads), use `git branch -d` (never `-D`) for the local-branch delete, and verify the publish workflow fired before printing the final summary. Step 6 (Address Feedback) reinforces the prescribed reply literals: accept replies must begin with the literal phrase `Fixed in <sha>` (`Done` / `Resolved` / `Accepted and fixed` do NOT satisfy); decline replies must begin with `Declining — <reason>` using a U+2014 em dash followed by a space (NOT a period, hyphen, or en dash). Eliminated the Copilot-via-GraphQL low-with-context regression and the PR Merge Cleanup mid-iteration regression; recovered Review Response Guide to ~100.

### Rules

- **author-model-declaration** — Every PR declares its author model via a `**Author-Model:**` line in the body (preferred) or a `Co-authored-by:` git trailer (fallback). Defines model families (anthropic, openai, google), plus a special `human` value that maps to no family, and mixed-authorship semantics so the paired reviewers can pick a cross-family reviewer and dodge self-review bias. Missing declaration blocks the PR via early `REQUEST_CHANGES` before the diff is read.
- **stateful-artifacts** — Cross-invocation JSON state files a skill writes and reads between runs. Every artifact has a documented schema, a single owner skill, a `schema_version` field, and a writer/reader contract. Artifacts are hints, not authority — verify against the live source before acting on a recalled value. Migrations happen under the owner skill, not as a side effect of unrelated runs.

### Rule tightenings

- **skill-authoring** — Document `user-invocable: false` as an optional frontmatter field for background-knowledge skills the runtime loads as context but the user should never invoke directly.
- **script-delegation** — Spell out the precheck-gating JSON contract with `wake_agent` as a boolean and `data` as an object (e.g., `{"wake_agent": true, "data": {}}` on the last line), the zero-token-cost rationale for no-op runs, and the single-fetch gate-and-payload pattern.
- **context-artifacts** — Enumerate the review rubric: frontmatter validity, sequential-execution preamble, flat step numbering, typed `Skill()` calls, silence-rule compliance, channel-appropriate formatting.
- **plugin-evals** — Reframe around the load-bearing shape: **task describes the SITUATION** (no technique), **criteria check whether the output matches the specific manner this tile prescribes** (flag choices, format literals, sequences, chosen identifiers). That conformance IS the tile's contribution — checking for it measures tile value, not testing reading. Bleeding is narrowed to strictly one case: a criterion literal appearing verbatim in the task description. Fix bleeding at the task (strip the leaked literal), keep the criterion. No-Leaking is narrowed to tile-internal implementation details only (action names, `.tessl/tiles/...` paths, tile-only identifiers); tile-prescribed conventions and invented literals are allowed — a competent engineer without the tile would not produce those specific choices, which is precisely why they measure tile value. Adds a "Lift, Not Attainment" section with a three-cause diagnosis for near-zero lift (coincidence with universal competence / task leaked the technique / criteria grade engineering-101 instead of tile-specific manner) — so "low lift = retire" is no longer the reflex; high-lift scenarios that test specific tile choices must be kept, not softened toward "testing reasoning". Includes the v0.1.1 → v0.1.19 regression diagnosis (lift halved from 26 → 13 absolute points) as the motivating data. The earlier in-session iterations that said tile-invented conventions were leaking or that criteria satisfied by grepping the skill files were "testing reading" were wrong and are superseded by this wording.

### Skills

- **install-reviewer** — Scaffold the paired gh-aw PR policy reviewers (OpenAI Codex + Anthropic Claude Code) into a consumer repo. Ships `review-openai.md` and `review-anthropic.md` as packaged templates; the skill copies both into the consumer's `.github/workflows/`, compiles atomically with `gh aw compile`, commits all six artifacts, and opens a PR. Each workflow runs `tessl install jbaruch/coding-policy` as a pre-step, then self-gates on the PR's `Author-Model:` declaration: the same-family reviewer emits a one-line "skipping: self-review-bias" COMMENT and exits; the cross-family reviewer reviews. Each reviewer's verdict now begins with a one-line load indicator (`"Policy loaded: N rule files from .tessl/tiles/jbaruch/coding-policy/rules/ (installed tile)."`), so a passing review visibly confirms `tessl install` reached the runtime. A missing or empty installed-tile path causes an early `REQUEST_CHANGES` with a diagnostic rather than a silent zero-context review. Preflight now emits a `warnings` array alongside `failures`; a committed root-level `.mcp.json` raises a `root-mcp-json-present` advisory (the Anthropic reviewer can't launch sandbox-unreachable MCP servers like `tessl mcp start`), and the install-reviewer PR body surfaces every warning verbatim in an "Action required before merge" section so the consumer acts before the first workflow run fails. The skill deliberately does not auto-modify the consumer's `.mcp.json` or `.gitignore` — the consumer decides. Three secrets required: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TESSL_TOKEN`. Clean-verdict reviews use `event: COMMENT` with a pass body (GitHub rejects `APPROVE` from `github-actions[bot]` with HTTP 422).
- **release** — Step 2 PR body template now includes a mandatory `**Author-Model:**` line per `rules/author-model-declaration.md`; Step 4 updated to describe the paired reviewers and their self-gating behavior.

### Evals

- **install-reviewer** — 3 scenarios graded against the paired-workflow layout: 1 positive (`consumer-scaffolds-policy-reviewer`) covering the happy path through preflight → branch → copy both templates → compile → commit all six artifacts → PR with the three required secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TESSL_TOKEN`) and the cross-family rationale, and 2 negative (`install-reviewer-refuses-overwrite`, `install-reviewer-missing-gh-aw`) covering the skill's two decisional guards.
- **Retired zero-lift scenarios** — removed `merge-with-failing-ci-rejection` and `release-on-main-branch-rejection`. Apr 18 eval run on v0.1.1 showed both scored 100/100 baseline, i.e. zero lift: the criteria grade responsible-engineer judgement (don't merge red CI, don't push to main) that any competent baseline LLM produces without this tile. Keeping them inflates the attainment average without measuring tile value.
- **Reduced task-level hints** — rewrote `coverage-gap-identification-and-fill` and `eval-scenario-quality-review-and-repair` to strip task text that enumerated the tile-specific techniques or defect taxonomy. Baseline agents can no longer pattern-match their way to the criteria from the task alone; the remaining scoring concentrates on tile-specific behaviour (correct weighted_checklist format, no bleeding in authored scenarios, negative-case coverage, discovering defects without being handed a taxonomy).
- **Restored tile-prescribed-conformance criteria on five scenarios** — after the corrected plugin-evals rule (see above) distinguished bleeding from conformance, restored specific-convention checks that were over-removed earlier. Each scenario now grades whether the output matches the specific manner the tile prescribes, NOT whether the agent can reason its way to some equivalent. Changes: (a) `pr-merge-and-post-merge-cleanup` — criteria now check `gh pr merge --merge --delete-branch`, `git pull --ff-only`, `git branch -d` (safe delete, not `-D`), `git remote prune origin`; (b) `review-feedback-addressing-conventions` — criteria check the tile's prescribed reply formats (`Fixed in <sha>` for accepts, `Declining — <reason>` for declines), with substance checks as complements; (c) `ci-and-review-status-polling` — criteria check the tile's prescribed mechanisms (`gh pr checks <N> --json name,bucket` with a caller polling loop, `gh api .../pulls/<N>/reviews`, `gh api .../pulls/<N>/comments`) with an explicit "NOT `/issues/<N>/comments`" check for the GitHub API trap; (d) `copilot-review-via-graphql` — criteria restore the tile's prescribed hot-path-plus-fallback bot-ID pattern (pinned `BOT_kgDOCnlnWA` with dynamic-discovery fallback) rather than the earlier "don't depend on hardcoded IDs" flip, which had inverted the tile's actual choice; (e) `version-bump-reasoning-and-manifest-upda` — task stripped of the "CI auto-bumps patch" leak (was teaching the agent the tile's policy inside the task text) so the agent must now apply that policy from the tile; criteria check the tile-prescribed CI delegation for patch, specific semver targets, and risk-based release sequencing. All rubrics still sum to 100.

### Skill updates

- **eval-authoring** — Step 4 (Review) and `REVIEW_CHECKLIST.md` rewritten around the new task/criteria shape: is the task a SITUATION (no technique leak) and do the criteria grade the specific manner the tile prescribes? Leaking narrowed to tile-internal implementation details only; tile-prescribed conventions and invented literals are explicitly allowed. Step 9 (Analyze Results) centres on lift (`with_context - baseline`) rather than attainment, with a three-cause diagnosis for near-zero lift: (1) coincidence with universal competence; (2) task leaked the technique — fix the task, not the criterion; (3) criteria grade engineering-101 rather than tile-specific manner — rewrite to grade the specific prescription, not retire. High-lift scenarios testing specific tile choices must be kept, not softened toward "testing reasoning" that baseline already does.

## 0.1.3

Add context artifact authoring rules and eval-authoring skill. Self-audited the tile against its own rules and iterated until 99% eval average. (Originally drafted as `## 0.2.0` in this changelog but actually shipped as patch 0.1.3 by the auto-bumper; relabelled in the 0.2.0 release to make the version timeline consistent with the registry — patches 0.1.4 through 0.1.20 fall between this entry and the new 0.2.0 above.)

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
