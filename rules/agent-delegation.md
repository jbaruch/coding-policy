---
alwaysApply: true
description: Direct standalone work and the permitted delegation boundary
---

# Agent Delegation

## Direct Work

- A standalone agent does the task directly.
- Never simulate team roles, briefs or reports in standalone work.
- Task size or caution does not establish a team session.

## Delegated Work

- Authorized delegation uses configured Herdr-managed agents through their owner scripts.
- Native subagent, spawn, task and team mechanisms are forbidden delegation routes, including tools, skills and CLI wrappers.
- The restriction covers nested delegation, research, review and read-only work.
- If the route or an eligible worker is unavailable, retain task ownership and use the authorized recovery or escalation route.
- An unavailable route never authorizes native fallback.
- Explicit role restrictions and independence requirements remain binding.

## Optional Team Policy

- Team execution requires the separately installed `jbaruch/herdr-policy` contract and verified session authority.
- Installing that package alone does not create a team session or authorize dispatch.
- Ordinary direct work requires no team runtime, ledger, report or hook.
