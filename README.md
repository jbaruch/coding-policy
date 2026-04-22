# jbaruch/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fcoding-policy)](https://tessl.io/registry/jbaruch/coding-policy)

Coding policy tile for Baruch's AI agents. Language-agnostic code quality rules plus Tessl-specific plugin authoring standards — covering commits, testing, error handling, skill structure, script delegation, and eval quality.

## What's New

- 13 always-on steering rules: 8 covering code quality, 4 covering plugin authoring, 1 covering author-model declaration
- `release` skill — structured PR + merge workflow with Copilot review
- `eval-authoring` skill — generate, review, and curate eval scenarios
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
| Errors | [error-handling](rules/error-handling.md) | Specific exceptions, actionable messages, structured logging |
| Deps | [dependency-management](rules/dependency-management.md) | Stdlib-first, pinned versions, lock files |
| Files | [file-hygiene](rules/file-hygiene.md) | Proper .gitignore, no generated files committed |
| CI | [ci-safety](rules/ci-safety.md) | Never skip tests, never modify CI without asking |
| Secrets | [no-secrets](rules/no-secrets.md) | No credentials in code, env vars or secrets manager |
| Style | [code-formatting](rules/code-formatting.md) | Use project's formatter, don't mix style with logic |
| Authoring | [context-artifacts](rules/context-artifacts.md) | Plugin structure, rule format, review iteration, surface sync, consistency checks |
| Authoring | [skill-authoring](rules/skill-authoring.md) | SKILL.md structure, step numbering, typed calls, tile.json reference |
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic → script, reasoning → LLM, the regex trap |
| Authoring | [plugin-evals](rules/plugin-evals.md) | No bleeding, no leaking, persistent eval coverage |
| Review | [author-model-declaration](rules/author-model-declaration.md) | PRs declare author model; paired reviewers pick the cross-family one |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | PR creation, Copilot review, merge + cleanup workflow |
| [eval-authoring](skills/eval-authoring/SKILL.md) | Generate, review, iterate on eval scenarios with score-driven feedback |
| [install-reviewer](skills/install-reviewer/SKILL.md) | Scaffold the paired gh-aw PR review workflows (OpenAI + Anthropic) into a consumer repo — reviews every PR against the latest published `jbaruch/coding-policy` with cross-family enforcement |

## Philosophy

- **Language-agnostic code rules.** The code quality rules (commits through formatting) apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **Tessl-specific authoring rules.** The authoring rules (context-artifacts through plugin-evals) are specific to the Tessl plugin workflow. They codify how to build, test, and ship tiles.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories and the Tessl plugin authoring workflow. They solve problems that actually come up when agents write and ship code.
- **Always on.** Every rule is `alwaysApply: true`. Agents follow these conventions without being reminded.
