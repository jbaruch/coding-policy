---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md, tile.json — when authoring or modifying skills"
description: SKILL.md structure, frontmatter, execution-mode preamble, flat step numbering, typed Skill() calls, tile.json reference
---

# Skill Authoring

## SKILL.md Frontmatter

- Required fields: `name`, `description` (include trigger phrases so the agent knows when to activate)
- Optional fields: `allowed-tools`, `disable-model-invocation`, `user-invocable` (set to `false` for background-knowledge skills the runtime loads as context but the user should never invoke directly)
- The `description` field is your discovery surface — write it for the agent, not a human audience

## Title and Preamble

- Start the body with an `# H1` title naming the skill (e.g., `# Release Skill` for `skills/release/SKILL.md`)
- The first content line after the H1 declares the execution mode and prevents the agent from parallelizing or freelancing
- Sequential workflows (default — release, deploy, migrate): preamble `Process steps in order. Do not skip ahead.`
- Action routers (group-management, scheduler-config — agent picks one of several alternatives by user intent): preamble `This skill is an action router — pick the step that matches the user's intent and execute only that step. Do not run other steps; do not parallelize.` List the available actions in the skill's `description` so the runtime can match intent

## Step Structure

- Use flat numbered headings with descriptive titles: `## Step 1 — Verify Readiness`, `## Step 2 — Create PR`
- The same flat-numbering format applies to both sequential workflows and action routers — in routers, "Step N" labels each alternative action rather than each phase of a workflow
- No decimals, no sub-steps — flat numbering only
- When inserting a step, renumber all subsequent steps
- Each step is one action. The check is a title heuristic: if the step's title combines two verbs with "and" or "&" (e.g., "Build and Deploy"), split it. A step's body may list sub-tasks all serving one cohesive action
- Action-router preambles are exhaustive on chaining: if a step chains to another ("after registering a group, also configure mounts"), say so explicitly. Default in routers: execute only the chosen step and finish

## Step Continuity

- Default handoff between steps is "continue immediately" — never "pause and wait for the user"
- State continuations explicitly ("Proceed immediately to Step N"); do not rely on implicit reading order
- If a step can legitimately end the skill, say so explicitly ("Finish here")

## Keep Skills Compact

- SKILL.md is the execution plan, not a reference manual
- Move detailed reference material (API docs, long examples, lookup tables) to separate files
- Reference them with typed code blocks and full relative paths

## Typed Calls

- Invoke other skills with typed `Skill(skill: "name")` calls, never prose references
- Never call `Skill()` on a rule (rules are auto-loaded, not invoked via Skill)

## Silence Instructions

- If a step can legitimately produce no output, say so explicitly: "If no issues found, proceed silently"

## Script References

- Deterministic operations must be executable script files, not inline code blocks for the agent to copy-paste — see `rules/script-delegation.md`
- In rule prose, documentation, and skill cross-references, use repo-relative paths (`skills/<name>/<file>.<ext>`)
- In step bodies, use the path that resolves at the invocation site
- Repo-relative when the skill runs from a clone of this repo: `skills/release/poll-pr-reviews.sh`
- Tile-mount path when the skill runs inside a consumer: `.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh`
- Don't mix conventions inside one SKILL.md — if one step invokes via a mount path, every other script-invoking step must too
- Include the expected input/output contract in the step description

## tile.json Manifest Reference

Required fields:
- `name` — `<workspace>/<tile-name>` format
- `version` — semver string
- `summary` — one-line description of the tile
- `entrypoint` — path to the tile's README (typically `README.md`)

Optional fields:
- `private` — `true` to prevent publishing to the public registry
- `docs` — path to extended documentation (avoid; keep docs in the entrypoint)
- `keywords` — array of discovery tags
- `skills` — map of skill names to `{ "path": "skills/<name>/SKILL.md" }`
- `steering` — map of rule names to `{ "rules": "rules/<name>.md", "alwaysApply": true }`
