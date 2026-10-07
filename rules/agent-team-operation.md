---
alwaysApply: true
description: Standalone versus Herdr team round — the mode test, and where the team-round contract lives
---

# Agent Team Operation

## Two Modes

- **Standalone** — one agent working a task on its own, with no Herdr session around it. `HERDR_ENV` is unset
- **Herdr team round** — a nonworking foreman dispatching work across separate Herdr worker panes. `HERDR_ENV` is set
- Read `HERDR_ENV` to tell the modes apart
- Never infer the mode from how large or careful the task is
- **Every section of `skills/herdr-foreman/references/team-operation.md` governs a Herdr team round only**
- In standalone mode none of it applies — no roles, no rotation, no briefs, no report files, no worktree-per-writer, no judge seat
- A standalone agent does the task directly
- A standalone agent never simulates the roles, the briefs, or the reports
- Standalone work is still governed by every other rule in this plugin

## Delegation

- Authorized delegated work uses Herdr-managed agents through the existing foreman owners
- This section governs both modes and every role, including nested delegation and read-only research or review
- Native subagent, spawn, task and team mechanisms are forbidden delegation routes
- The prohibition covers invocation through tools, skills or CLI wrappers
- Claude Code, Codex CLI and other supported runtimes remain valid workers inside Herdr
- Role-specific delegation prohibitions and independence constraints remain binding
- An unmanaged agent's output never substitutes for a required Herdr report
- When Herdr or an eligible worker is unavailable, follow the existing recovery and escalation route
- Outside a Herdr round the requesting agent retains ownership of an unavailable delegation route
- An unavailable route never authorizes native fallback
- Direct work remains permitted only within the current role's authority and standalone rules
- Team-round staffing and worker requests follow `skills/herdr-foreman/references/team-operation.md` Delegation

## Foreman Role

- This section governs a Herdr team round only
- The foreman is a nonworking foreman
- It assigns the work
- It supervises the crew
- It accepts or rejects what the crew delivers
- In a team round the foreman dispatches the task work and never executes it itself
- The foreman's own execution covers reading the shared checkout, this plugin's owner scripts, and the foreman-owned records those scripts write
- Foreman-owned records are the task ledger, retrospective notes, attention items, and working memory
- A request whose answer is a task deliverable is dispatched, whatever its size, and whether or not the foreman already knows the answer
- A task deliverable is a written artifact, a recommendation, an assessment, or a repository edit, other than a foreman-owned record
- A request the foreman could answer only after a lookup, a file inspection, or research is dispatched
- Narrow exception for a bounded factual lookup.
- Applies to a read-only fact answer with no durable deliverable or substantive judgment
- Preconditions are binding: read `skills/herdr-foreman/references/team-operation.md` Bounded Factual Lookup before relying on it
- Every other lookup follows the dispatch requirement
- A bounded question routes to a specialist consultation under Team Composition
- A review of code already pushed for the task routes to the reviewer responsibility under Review Before PR
- A review of any other existing code routes to a read-only consultation under Specialist Consultations
- A repository edit routes to the developer responsibility under Writers and Checkouts
- A shortfall of eligible workers is a staffing decision to record under Team Composition, never authorization for the foreman to execute

## Team Round Contract

- A Herdr team round follows `skills/herdr-foreman/references/team-operation.md` in full
- That file is a must-read before any team-round action outside Bounded Factual Lookup
- Its sections bind a team round as rule content
- The foreman loads it through `skills/herdr-foreman/SKILL.md`
- Every worker brief names it as a required read
- A section this rule names without a path (Team Composition, Review Before PR, Specialist Consultations, Writers and Checkouts) lives in that file
