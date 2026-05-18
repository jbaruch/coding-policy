# jbaruch/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fcoding-policy)](https://tessl.io/registry/jbaruch/coding-policy)

Coding policy tile for Baruch's AI agents. Language-agnostic code quality rules plus Tessl-specific plugin authoring standards — covering commits, testing, error handling, skill structure, script delegation, and eval quality.

## What's New

- 17 always-on steering rules: 8 covering code quality, 6 covering plugin authoring, 1 covering author-model declaration, 1 covering concurrency, 1 covering review discipline
- `release` skill — structured PR + merge workflow with Copilot review and paired-reviewer cross-family enforcement
- `eval-authoring` skill — generate, review, and curate eval scenarios with score-driven iteration
- `install-reviewer` skill — scaffold the paired gh-aw PR review workflows (OpenAI + Anthropic) into a consumer repo
- 0.3.0 added `install-reviewer` upgrade mode (`--override`) — refreshes scaffolded reviewer files in place instead of requiring a manual `git rm`-and-rerun
- 0.2.0 lifted with-context attainment from 93 to 98 (3× avg, lift +17 → +22) by tuning skill prose against eval log analysis
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
| Testing | [testing-standards](rules/testing-standards.md) | Outcome-based, deterministic, no binary fixtures |
| Errors | [error-handling](rules/error-handling.md) | Specific exceptions (with outer-boundary process-contract carve-out), actionable messages, structured logging |
| Deps | [dependency-management](rules/dependency-management.md) | Stdlib-first, pinned versions, lock files |
| Files | [file-hygiene](rules/file-hygiene.md) | Proper .gitignore, no generated files committed |
| CI | [ci-safety](rules/ci-safety.md) | Never skip tests, never modify CI without asking |
| Secrets | [no-secrets](rules/no-secrets.md) | No credentials in code, env vars or secrets manager |
| Style | [code-formatting](rules/code-formatting.md) | Use project's formatter, don't mix style with logic |
| Authoring | [context-artifacts](rules/context-artifacts.md) | Plugin structure, rule format, review iteration, surface sync, consistency checks |
| Authoring | [context-writing-style](rules/context-writing-style.md) | Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format. CHANGELOG entries follow looser archive discipline |
| Authoring | [skill-authoring](rules/skill-authoring.md) | SKILL.md structure, step numbering, typed calls, tile.json reference |
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic → script, reasoning → LLM, the regex trap |
| Authoring | [plugin-evals](rules/plugin-evals.md) | No bleeding, no leaking, persistent eval coverage |
| Authoring | [stateful-artifacts](rules/stateful-artifacts.md) | Cross-invocation state: schema, owner skill, schema_version, hints-not-authority, migration |
| Review | [author-model-declaration](rules/author-model-declaration.md) | PRs declare author model; paired reviewers pick the cross-family one |
| Concurrency | [agent-worktree-isolation](rules/agent-worktree-isolation.md) | Mandatory git worktrees for concurrent agent work; cleanup; read-only exception |
| Discipline | [boy-scout](rules/boy-scout.md) | Leave it better than you found it; "pre-existing" is not a valid concept; in-scope cleanups bundle, out-of-scope ones get filed |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | PR creation, Copilot review, merge + cleanup workflow |
| [eval-authoring](skills/eval-authoring/SKILL.md) | Generate, review, iterate on eval scenarios with score-driven feedback |
| [eval-curation](skills/eval-curation/SKILL.md) | Prune an existing eval suite — run, compute per-scenario lift, diagnose weak scenarios, retire / fix / rewrite, verify the curated suite still pulls weight |
| [install-reviewer](skills/install-reviewer/SKILL.md) | Scaffold the paired gh-aw PR review workflows (OpenAI + Anthropic) into a consumer repo — reviews every PR against the latest published `jbaruch/coding-policy` with cross-family enforcement. Supports `--override` for in-place upgrades. |

## Philosophy

- **Language-agnostic code rules.** The code quality rules (commits through formatting) apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **Tessl-specific authoring rules.** The authoring rules (context-artifacts through plugin-evals) are specific to the Tessl plugin workflow. They codify how to build, test, and ship tiles.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories and the Tessl plugin authoring workflow. They solve problems that actually come up when agents write and ship code.
- **Always on.** Every rule is `alwaysApply: true`. Agents follow these conventions without being reminded.
