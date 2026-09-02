# jbaruch/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fcoding-policy)](https://tessl.io/registry/jbaruch/coding-policy)

Coding policy plugin for Baruch's AI agents. Language-agnostic code quality rules plus Tessl-specific plugin authoring standards — covering commits, testing, error handling, skill structure, and script delegation.

## What's New

- `herdr-teamlead` skill + `agent-team-operation` rule + `herdr-team-status` hook — run a three-agent team round inside [Herdr](https://herdr.dev): measure each worker's subscription headroom, assign developer / tester / reviewer by measured headroom, clear each worker's context and send a fresh role brief, wait on the report file plus its `REPORT: ` marker, and hand the merge to `release`
- `stop-handoff-hygiene` hook — a `Stop` hook (Claude Code + Codex) that blocks the handoff once (loop-safe via `stop_hook_active`) when it finds leftover local branches (merged, upstream deleted), orphaned worktrees, or diagnostics findings in the changed set; a dirty working tree is reported, not blocked
- `check-tessl-latest` hook — a Tessl `SessionStart` hook that warns when a consumer's `tessl.json` pins a `jbaruch/*` dependency instead of `latest`; it's the deterministic enforcement for the Runtime-Managed Manifest Carve-Out (`rules/dependency-management.md`). Informative only, never blocks
- `check-git-sync` hook — a Tessl `SessionStart` hook that fetches origin (throttled) and warns when the local default branch is behind `origin/<default>`, mechanizing `rules/sync-before-work.md`. Informative only, never blocks
- `check-policy-freshness` hook — a Tessl `SessionStart` hook that runs `tessl outdated` and warns (throttled to once/day) when installed plugins are behind the registry, so repos don't silently drift onto stale policy. Informative only, never blocks
- Policy review runs on the OpenAI Codex CLI authenticated by a ChatGPT subscription (no API key) via `.github/workflows/review-codex.yml`, reviewing every PR against the in-tree `rules/*.md`; Copilot stays as the complementary code-quality lane
- 26 rules — 20 always-on, 6 conditional (scoped via `applyTo:` to the files where the rule's prescriptions actually fire). Breakdown: 10 covering code quality, 7 covering plugin authoring, 1 covering concurrency, 1 covering review discipline, 1 covering reviewer-feedback reading, 1 covering review severity, 1 covering external-repo action scope, 1 covering response communication, 1 covering merge/ship autonomy, 1 covering hook-status reporting, 1 covering multi-agent team operation
- `release` skill — structured PR + merge workflow gated on the Codex policy review's blocking findings; Copilot is the complementary code-quality lane and is always advisory
- `onboard-repo` skill (renamed from `install-reviewer`) — bootstrap a consumer repo onto coding-policy: enroll it in the central fleet policy reviewer, pin its `jbaruch/*` tessl deps to `latest`, and add the tessl-generated-artifacts `.gitignore` block so agents never commit per-developer output
- `adopt-fork-pr` skill — bring a fork PR's branch into the base repo as a same-repo PR the reviewer can run on
- 0.3.0 added `onboard-repo` upgrade mode (`--override`) — refreshes the reviewer artifacts in place instead of requiring a manual `git rm`-and-rerun
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
| Review | [review-severity](rules/review-severity.md) | Findings carry a severity — blocking (correctness, security, contract) gates the merge; advisory (prose, style, Copilot) never does; read all, act by severity, never burn a round on a lone advisory |
| Concurrency | [agent-worktree-isolation](rules/agent-worktree-isolation.md) | Mandatory git worktrees for concurrent agent work; cleanup; read-only exception |
| Teamwork | [agent-team-operation](rules/agent-team-operation.md) | Multi-agent team rounds: headroom-driven role rotation, one writer per worktree, report files as the only worker channel, never type into a working or blocked agent, internal review before the PR opens |
| Discipline | [boy-scout](rules/boy-scout.md) | Leave it better than you found it; "pre-existing" is not a valid concept; in-scope cleanups bundle, out-of-scope ones get filed |
| Scope | [external-repo-contributions](rules/external-repo-contributions.md) | Default deny on issues, PRs, comments, reactions, and discussions in repos the operator does not own; explicit permission required per repo and action type |
| Communication | [response-clarity](rules/response-clarity.md) | Shape responses action-first: lead with the command, number steps, show progress, plain errors, one concrete next step, no preamble or closers (exceptions for explanations, destructive actions, debug, ambiguity) |
| Discipline | [ship-on-green](rules/ship-on-green.md) | Green gate is the approval — merge, never ask; asking in a costume (flag/confirm/"say go") is deciding not to ship; stakes raise care not permission; three objective exits only — Red / No undo / Murky |
| Automation | [hook-action-reporting](rules/hook-action-reporting.md) | Relay any `Session-start status —` hook payloads to the user once at session start, then act on any action they name (a SessionStart hook's output reaches the model, not the transcript) |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | PR creation, Codex (subscription-CLI) policy review + Copilot code-quality review, merge + cleanup workflow |
| [onboard-repo](skills/onboard-repo/SKILL.md) | Bootstrap a consumer repo onto coding-policy, then open a PR. Scaffolds the fleet reviewer (`.github/fleet-review-enabled` marker, a thin `.github/workflows/review-trigger.yml` that fires an immediate PR-time review in `coding-policy`, `.github/copilot-instructions.md`); pins `jbaruch/*` tessl deps to `latest` (third-party pins left as-is); and adds the tessl-generated-artifacts `.gitignore` block (keeping `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` committed). The `coding-policy-fleet-reviewer` GitHub App reviews against the `jbaruch/coding-policy` rules with the Codex CLI (no API key); the Codex credential lives only in `coding-policy`; the consumer sets one `FLEET_DISPATCH_TOKEN` PAT. Supports `--override` for in-place upgrades. |
| [adopt-fork-pr](skills/adopt-fork-pr/SKILL.md) | Classify a PR by number. Same-repo PRs pass through to the reviewer; fork PRs get adopted into the base repo as a same-repo PR, preserving the contributor's commits. |
| [migrate-to-plugin](skills/migrate-to-plugin/SKILL.md) | Migrate a legacy `tile.json` plugin to the `.tessl-plugin/plugin.json` form: runs `tessl plugin migrate`, renames `.tileignore`, removes the obsolete `tile.json`, re-lints, then reconciles residual "tile" wording to "plugin" while preserving contract surfaces. |
| [herdr-teamlead](skills/herdr-teamlead/SKILL.md) | Run one task round as the lead of three coding agents in [Herdr](https://herdr.dev): roster the named workers, verify repo authority, measure each one's subscription headroom, assign developer / tester / reviewer by measured headroom, compose the role briefs and provision their worktrees through scripts, dispatch, wait on the report file plus its `REPORT: ` marker (never on a single idle observation), then gate the round on the post-push reviewer and tester reports and hand the PR + merge to `release`. |

### Hooks

| Hook | Event | Description |
| ---- | ----- | ----------- |
| [check-policy-freshness](hooks/check-policy-freshness.sh) | SessionStart | Warns (throttled once/day) when installed Tessl plugins are behind the registry — a `tessl update` reminder at session start. Informative only, never blocks. |
| [check-git-sync](hooks/check-git-sync.sh) | SessionStart | Fetches origin (throttled once/hour per repo) and warns when the local default branch is behind `origin/<default>` — a `rules/sync-before-work.md` reminder at session start. Informative only, never blocks. |
| [check-tessl-latest](hooks/check-tessl-latest.sh) | SessionStart | Warns when `tessl.json` pins a `jbaruch/*` dependency instead of `latest` — the deterministic enforcement for the Runtime-Managed Manifest Carve-Out (`rules/dependency-management.md`). Third-party pins are out of scope. Informative only, never blocks. |
| [stop-handoff-hygiene](hooks/stop-handoff-hygiene.sh) | Stop (Claude Code + Codex) | Blocks the handoff once (loop-safe via `stop_hook_active`) on leftover local branches (merged, upstream gone), orphaned worktrees, or diagnostics findings in the changed set (uncommitted `.sh`/`.py`, linted with shellcheck/pyright). A dirty working tree is reported, not blocked. Fail-open. |
| [herdr-team-status](hooks/herdr-team-status.sh) | SessionStart | Names the live Herdr team: each named worker, its kind, and its lifecycle state. Silent outside Herdr and when no other named worker is live. Informative only, never blocks. |

## Philosophy

- **Language-agnostic code rules.** The code quality rules (commits through formatting) apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **Tessl-specific authoring rules.** The Authoring-category rules in the table above are specific to the Tessl plugin workflow. They codify how to build, test, and ship plugins.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories and the Tessl plugin authoring workflow. They solve problems that actually come up when agents write and ship code.
- **Loaded by default; scoped by intent.** Universal rules are `alwaysApply: true`. Rules whose prescriptions only fire in specific files are `alwaysApply: false` with `applyTo:` declaring the scope — the agent's model reads the frontmatter and narrows when to act. See `rules/rule-frontmatter.md`.
