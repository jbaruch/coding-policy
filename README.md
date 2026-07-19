# jbaruch/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fcoding-policy)](https://tessl.io/registry/jbaruch/coding-policy)

Coding policy plugin for Baruch's AI agents. Language-agnostic code quality rules plus Tessl-specific plugin authoring standards — covering commits, testing, error handling, skill structure, and script delegation.

## What's New

- Policy review runs on the native OpenAI Codex code-review app (ChatGPT subscription, no API key), steered by `AGENTS.md ## Review guidelines`; Copilot stays as the complementary code-quality lane
- 21 rules — 15 always-on, 6 conditional (scoped via `applyTo:` to the files where the rule's prescriptions actually fire). Breakdown: 10 covering code quality, 7 covering plugin authoring, 1 covering concurrency, 1 covering review discipline, 1 covering reviewer-feedback reading, 1 covering external-repo action scope
- `release` skill — structured PR + merge workflow gated on the Codex policy review and Copilot code-quality review
- `install-reviewer` skill — set up the native Codex PR reviewer (plus the Copilot lane) in a consumer repo
- `adopt-fork-pr` skill — bring a fork PR's branch into the base repo as a same-repo PR the reviewer can run on
- 0.3.0 added `install-reviewer` upgrade mode (`--override`) — refreshes the reviewer artifacts in place instead of requiring a manual `git rm`-and-rerun
- Language-agnostic: works with any stack, no Python/JS assumptions

See [CHANGELOG.md](CHANGELOG.md) for full version history.

## Installation

```
tessl install jbaruch/coding-policy
```

## What's Included

| Category | Rule | Summary |
|----------|------|---------|
| Git | [commit-conventions](rules/commit-conventions.md) | Imperative mood, one change per commit, PR hygiene |
| Git | [sync-before-work](rules/sync-before-work.md) | Fetch and sync the local checkout to the remote default before reading, planning, or editing; branch from the fresh default |
| Testing | [testing-standards](rules/testing-standards.md) | Outcome-based, deterministic, no binary fixtures |
| Errors | [error-handling](rules/error-handling.md) | Specific exceptions (with outer-boundary process-contract carve-out), actionable messages, structured logging |
| Deps | [dependency-management](rules/dependency-management.md) | Stdlib-first, pinned versions kept fresh via a renewal mechanism, lock files |
| Files | [file-hygiene](rules/file-hygiene.md) | Proper .gitignore, no generated files committed |
| CI | [ci-safety](rules/ci-safety.md) | Never skip tests, never modify CI without asking |
| Secrets | [no-secrets](rules/no-secrets.md) | No credentials in code, env vars or secrets manager |
| Style | [code-formatting](rules/code-formatting.md) | Use project's formatter, don't mix style with logic |
| Types | [language-diagnostics](rules/language-diagnostics.md) | Enable the project's language server; its findings are non-dismissible without cause; gate the headless checker in CI at zero findings |
| Authoring | [context-artifacts](rules/context-artifacts.md) | Plugin structure, rule format, review iteration, surface sync, consistency checks |
| Authoring | [context-writing-style](rules/context-writing-style.md) | Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format. CHANGELOG entries follow looser archive discipline |
| Authoring | [rule-frontmatter](rules/rule-frontmatter.md) | Frontmatter conventions for rule files — passthrough model, per-agent field map, when to path-scope |
| Authoring | [skill-authoring](rules/skill-authoring.md) | SKILL.md structure, step numbering, typed calls, plugin.json reference |
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic → script, reasoning → LLM, the regex trap |
| Authoring | [script-as-black-box](rules/script-as-black-box.md) | Skills reference the script's contract (inputs/outputs/exit codes), not its internal logic — thresholds and predicates live in the script |
| Authoring | [stateful-artifacts](rules/stateful-artifacts.md) | Cross-invocation state: schema, owner skill, schema_version, hints-not-authority, migration |
| Review | [reviewer-feedback-reading](rules/reviewer-feedback-reading.md) | A review's state classifies merge-gating, not whether its body must be read; read every reviewer's body before declaring merge-ready, COMMENTED-with-zero-inline included |
| Concurrency | [agent-worktree-isolation](rules/agent-worktree-isolation.md) | Mandatory git worktrees for concurrent agent work; cleanup; read-only exception |
| Discipline | [boy-scout](rules/boy-scout.md) | Leave it better than you found it; "pre-existing" is not a valid concept; in-scope cleanups bundle, out-of-scope ones get filed |
| Scope | [external-repo-contributions](rules/external-repo-contributions.md) | Default deny on issues, PRs, comments, reactions, and discussions in repos the operator does not own; explicit permission required per repo and action type |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | PR creation, Codex policy review + Copilot code-quality review, merge + cleanup workflow |
| [install-reviewer](skills/install-reviewer/SKILL.md) | Set up the native OpenAI Codex PR reviewer (plus the Copilot lane) in a consumer repo — commits an `AGENTS.md` `## Review guidelines` block that steers Codex to review every PR against the installed `jbaruch/coding-policy` rules, plus `.github/copilot-instructions.md`, then opens a PR and hands the operator the Codex-UI setup checklist. Supports `--override` for in-place upgrades. |
| [adopt-fork-pr](skills/adopt-fork-pr/SKILL.md) | Classify a PR by number. Same-repo PRs pass through to the reviewer; fork PRs get adopted into the base repo as a same-repo PR, preserving the contributor's commits. |
| [migrate-to-plugin](skills/migrate-to-plugin/SKILL.md) | Migrate a legacy `tile.json` plugin to the `.tessl-plugin/plugin.json` form: runs `tessl plugin migrate`, renames `.tileignore`, removes the obsolete `tile.json`, re-lints, then reconciles residual "tile" wording to "plugin" while preserving contract surfaces. |

## Philosophy

- **Language-agnostic code rules.** The code quality rules (commits through formatting) apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **Tessl-specific authoring rules.** The Authoring-category rules in the table above are specific to the Tessl plugin workflow. They codify how to build, test, and ship plugins.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories and the Tessl plugin authoring workflow. They solve problems that actually come up when agents write and ship code.
- **Loaded by default; scoped by intent.** Universal rules are `alwaysApply: true`. Rules whose prescriptions only fire in specific files are `alwaysApply: false` with `applyTo:` declaring the scope — the agent's model reads the frontmatter and narrows when to act. See `rules/rule-frontmatter.md`.
