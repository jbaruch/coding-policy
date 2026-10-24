# jbaruch/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Fcoding-policy)](https://tessl.io/registry/jbaruch/coding-policy)

Coding policy plugin for Baruch's AI agents. Language-agnostic code quality rules plus Tessl-specific plugin authoring standards — covering commits, testing, error handling, skill structure, and script delegation.

## What's New

- Worktree and branch cleanup — anything origin can restore is removed without a word; work that exists nowhere else is reported, never touched
  - `check-leftover-worktrees` runs at session start for the session's own repository
  - An idle clean worktree whose HEAD origin holds is removed
  - A local branch origin holds or merged is deleted
  - An unprotected branch on origin merged with no open pull request is deleted
  - An idle dirty or unpushed worktree, an unpushed local branch, and a stale unmerged branch on origin with no pull request are listed for the operator
  - `sweep-worktrees.sh` applies the worktree and local-branch rules to every repository with a worktree directory under `~/.worktrees` in a Herdr round preflight
- `stop-handoff-hygiene` hook — a `Stop` hook (Claude Code + Codex) that blocks the handoff once (loop-safe via `stop_hook_active`) when `prune-worktrees.sh --dry-run` would remove leftover worktrees or local branches, or on diagnostics findings in the changed set; a dirty working tree is reported, not blocked
- `check-acr-latest` hook — the ACR counterpart of `check-tessl-latest`: runs `acr freshness run --policy install` in a project with `agents.yaml`, so ACR dependencies at `latest` update at session start instead of being reported as behind. Never blocks
- `check-tessl-latest` hook — runs `tessl update` every session and reports each `jbaruch/*` dependency's version, flagging one pinned instead of `latest` (`rules/dependency-management.md`). Never blocks
- `session-start` hook — the plugin's one `SessionStart` entry, native for Claude Code and Codex and portable for other agents: runs every SessionStart hook and merges their statuses into one payload
- `check-git-sync` hook — fetches origin every session and fast-forwards a local default branch strictly behind `origin/<default>` (`rules/sync-before-work.md`). Diverged and Herdr-worker sessions are reported only. Never blocks
- Policy review runs on the OpenAI Codex CLI authenticated by a ChatGPT subscription (no API key) via `.github/workflows/review-codex.yml`, reviewing every PR against the in-tree `rules/*.md`; Claude stays as the complementary code-quality lane
- 26 rules — 20 always-on, 6 conditional (scoped via `applyTo:` to the files where the rule's prescriptions actually fire). Breakdown: 10 covering code quality, 7 covering plugin authoring, 1 covering concurrency, 1 covering review discipline, 1 covering reviewer-feedback reading, 1 covering review severity, 1 covering external-repo action scope, 1 covering response communication, 1 covering merge/ship autonomy, 1 covering hook-status reporting, 1 covering delegation boundaries
- `release` skill — structured PR + merge workflow with separate Codex policy and Claude code-quality reviews; blocking findings from either hold the merge
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
| Delegation | [agent-delegation](rules/agent-delegation.md) | Direct standalone work, authorized Herdr delegation and the native-spawn restriction |
| Git | [sync-before-work](rules/sync-before-work.md) | Fetch and branch from the fresh remote default |
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
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic → script, fixed answers read by meaning → bounded classification, everything else → LLM. Standing operator approval permits direct routing within recorded question, model, score and action bounds. Script stdout carve-outs for paths, versions and wrapped commands |
| Authoring | [script-as-black-box](rules/script-as-black-box.md) | Skills reference the script's contract (inputs/outputs/exit codes), not its internal logic — thresholds and predicates live in the script |
| Authoring | [stateful-artifacts](rules/stateful-artifacts.md) | Cross-invocation state: schema, owning skill/runtime/service, schema_version, reader contracts and migrations; runtime-owned tables use their migration schema |
| Review | [reviewer-feedback-reading](rules/reviewer-feedback-reading.md) | A review's state classifies merge-gating, not whether its body must be read; read every reviewer's body before declaring merge-ready, COMMENTED-with-zero-inline included |
| Review | [review-severity](rules/review-severity.md) | Findings carry a severity — blocking (correctness, security, contract) gates the merge; advisory (prose, style) never does; read all, act by severity, never burn a round on a lone advisory |
| Concurrency | [agent-worktree-isolation](rules/agent-worktree-isolation.md) | Mandatory git worktrees for concurrent agent work; cleanup; read-only exception |
| Discipline | [boy-scout](rules/boy-scout.md) | Fix coherent in-scope defects; record unrelated blocking risks once; advisories and maintenance create no delivery obligation |
| Scope | [external-repo-contributions](rules/external-repo-contributions.md) | Default deny on issues, PRs, comments, reactions, and discussions in repos the operator does not own; explicit permission required per repo and action type |
| Communication | [response-clarity](rules/response-clarity.md) | Shape responses action-first: lead with the command, number steps, show progress, plain errors, one concrete next step, no preamble or closers (exceptions for explanations, destructive actions, debug, ambiguity) |
| Discipline | [ship-on-green](rules/ship-on-green.md) | Green gate is the approval — merge, never ask; asking in a costume (flag/confirm/"say go") is deciding not to ship; stakes raise care not permission; three objective exits only — Red / No undo / Murky |
| Automation | [hook-action-reporting](rules/hook-action-reporting.md) | Relay any `Session-start status —` hook payloads to the user once at session start, then act on any action they name (a SessionStart hook's output reaches the model, not the transcript) |

#Herdr team coordination is distributed separately as [jbaruch/herdr-policy](https://github.com/jbaruch/herdr-policy). Standalone consumers need only this package. Team upgrades follow the extracted plugin's migration procedure.

## Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | PR creation or explicit source-verified existing-PR resumption, Codex policy review + Claude code-quality review, merge + publication confirmation and cleanup |
| [onboard-repo](skills/onboard-repo/SKILL.md) | Bootstrap a consumer repo onto coding-policy, then open a PR. Scaffolds the fleet reviewer (`.github/fleet-review-enabled` marker, a thin `.github/workflows/review-trigger.yml` that fires immediate Codex policy and Claude quality reviews in `coding-policy`); pins `jbaruch/*` tessl deps to `latest` (third-party pins left as-is); and adds the tessl-generated-artifacts `.gitignore` block (keeping `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` committed). The `coding-policy-fleet-reviewer` GitHub App reviews policy with Codex; the separate `jbaruch-claude-reviewer` App reviews code quality with Claude; both subscription credentials live only in `coding-policy`; the consumer sets one `FLEET_DISPATCH_TOKEN` PAT. Supports `--override` for in-place upgrades. |
| [adopt-fork-pr](skills/adopt-fork-pr/SKILL.md) | Classify a PR by number. Same-repo PRs pass through to the reviewer; fork PRs get adopted into the base repo as a same-repo PR, preserving the contributor's commits. |
| [migrate-to-plugin](skills/migrate-to-plugin/SKILL.md) | Migrate a legacy `tile.json` plugin to the `.tessl-plugin/plugin.json` form: runs `tessl plugin migrate`, renames `.tileignore`, removes the obsolete `tile.json`, re-lints, then reconciles residual "tile" wording to "plugin" while preserving contract surfaces. |

### Hooks

The release skill owns the hooks and their packaged [throttle-stamp schema](skills/release/state-schema.md).

| Hook | Event | Description |
| ---- | ----- | ----------- |
| [session-start](skills/release/hooks/session-start.sh) | SessionStart (native: Claude Code + Codex; portable: other agents) | The plugin's only SessionStart entry. Native for Claude Code and Codex, so it keeps the session's environment; the portable entry serves other agents and exits under a native one, so each session runs it once. Runs the four hooks below in order and merges their statuses into one payload. A failed hook is reported as its own status line. Never blocks. |
| [check-git-sync](skills/release/hooks/check-git-sync.sh) | SessionStart (via session-start) | Fetches origin every session (a `SYNC_THROTTLE_HOURS` window is optional) and fast-forwards a local default branch strictly behind `origin/<default>`; git refuses a non-fast-forward or an overwrite of local changes, and the refusal is reported. Repo hooks do not run. Diverged and Herdr-worker sessions are reported only. Never blocks. |
| [check-tessl-latest](skills/release/hooks/check-tessl-latest.sh) | SessionStart (via session-start) | Runs `tessl update --yes` and reports every `jbaruch/*` dependency's version, flagging one pinned instead of `latest` (`rules/dependency-management.md`). Third-party pins are out of scope. Never blocks. |
| [check-acr-latest](skills/release/hooks/check-acr-latest.sh) | SessionStart (via session-start) | In a project with `agents.yaml`, runs `acr freshness run --policy install` once the checkout is freshly fetched, contains origin's default branch, and has a clean tree: reconciles ACR dependencies declared `latest`, realizes changed files, and reports ACR's output (a `restart_required` notice included). An unsafe checkout gets a "not updated" status naming why, and nothing changes. Pinned tags and commits never move; `agents.yaml` is not rewritten. An `acr` older than `ACR_MIN_VERSION` (top of the script) is reported, not run. It is the deterministic check for the Runtime-Managed Manifest Carve-Out's consumer `agents.yaml` (`rules/dependency-management.md`): it names `github:jbaruch/*` dependencies not at `latest` and refuses to update while `.agents/registry.lock` is committed or not gitignored. In a Herdr session, and under tessl for other agents, it reports the check's findings but never updates. Silent without `agents.yaml`. Never blocks. |
| [check-leftover-worktrees](skills/release/hooks/check-leftover-worktrees.sh) | SessionStart (via session-start) | Cleans the session's own repository through its owner scripts, `skills/release/lib/prune-worktrees.sh` and `skills/release/lib/prune-remote-branches.sh`. Idle clean worktrees origin holds, local branches origin holds or merged, and unprotected merged branches on origin with no open pull request go silently. Lists only what the operator must decide: idle dirty or unpushed worktrees, unpushed local branches, stale unmerged branches on origin with no pull request. A failed, timed-out or tool-less check is one "could not check" line. Deletes nothing in a Herdr worker session. Under tessl a main checkout only reports, from a dry run, and a linked worktree is skipped. Runs under `skills/release/lib/bounded-run.sh` and never blocks. |
| [stop-handoff-hygiene](skills/release/hooks/stop-handoff-hygiene.sh) | Stop (Claude Code + Codex) | Blocks the handoff once (loop-safe via `stop_hook_active`) on worktrees and local branches `skills/release/lib/prune-worktrees.sh --dry-run` would remove (naming the command that removes them), or diagnostics findings in the changed set (uncommitted `.sh`/`.py`, linted with shellcheck/pyright). Dirty or unpushed idle worktrees and a dirty working tree are reported, not blocked. Fail-open. |

## Philosophy

- **Language-agnostic code rules.** The code quality rules (commits through formatting) apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **Tessl-specific authoring rules.** The Authoring-category rules in the table above are specific to the Tessl plugin workflow. They codify how to build, test, and ship plugins.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories and the Tessl plugin authoring workflow. They solve problems that actually come up when agents write and ship code.
- **Loaded by default; scoped by intent.** Universal rules are `alwaysApply: true`. Rules whose prescriptions only fire in specific files are `alwaysApply: false` with `applyTo:` declaring the scope — the agent's model reads the frontmatter and narrows when to act. See `rules/rule-frontmatter.md`.

## ACR distribution

The retained general rules, skills and hooks are declared in `agent-plugin.yaml` at the same explicit version as the Tessl manifest. Install with `acr install github:jbaruch/coding-policy@latest`, then `acr realize`. Herdr teams additionally run `acr install github:jbaruch/herdr-policy@latest` and `acr realize` after both compatible releases are confirmed. The extracted pair requires coding-policy 0.4.x and Herdr 0.2.x; keep teams stopped during migration from the combined 0.3.x package. ACR v1 has no package dependency field, so the pair is selected explicitly.

The serialized publish workflow preserves the manifest version, confirms Tessl availability, then publishes the immutable ACR tag and release asset in a dependent job. Retry a failed ACR job on the same run with `gh run rerun <run-id> --failed`; keep the original successful Tessl job and registry baseline. A partial publication is reconciled at the same version and commit; never move an existing tag or infer one channel's success from the other. Subsequent releases must update both manifest versions together.
