---
alwaysApply: true
description: Start from the fresh remote default; in Herdr rounds the foreman records it and workers consume their provisioned base
---

# Sync Before Work

## Sync Before Reading

- At the start of any task that will read or modify a repo, sync the local checkout with the remote default branch
- Run `git fetch origin` before the first read — not after the first failure
- The default branch you start from must match what the remote currently has, not whatever the local clone was left at

## Land on the Fresh Default

- On the default branch: fast-forward to `origin/<default>`
- Local default diverged or stale: rebase or reset onto `origin/<default>` before branching
- Cut the feature branch from the synced default, never from a stale local tip

## Staleness Poisons Conclusions

- Local default behind `origin/<default>` by more than a trivial amount: treat every "this file looks like X" conclusion as suspect until re-derived against the fresh tree
- An issue naming files, skills, or steps absent from the local tree is a staleness tell — fetch and re-derive before mapping the work onto what you see

## Herdr Team Rounds

- A worker consumes the foreman-provisioned task worktree under the narrow exception in `skills/herdr-foreman/references/team-operation.md` Writers and Checkouts.
- The provisioning owner must successfully fetch, resolve and record the exact fetched default commit before dispatch. A failed fetch stops provisioning.
- Brief composition consumes the original registered task base and validates the provisioned worktree against it automatically. It names the exact base; an explicitly authorized existing task base remains distinct from the latest fetched default.
- The worker reports drift through the normal report path and never repairs the shared checkout. Every other task follows Sync Before Reading and Land on the Fresh Default.

## Working Against a Pinned Ref

- Narrow exception for starting work from a specific older ref instead of the fresh default
- Preconditions (all required):
  1. The user explicitly names the older ref or commit to work against
  2. You state you are starting from that pinned ref rather than `origin/<default>`
- Every other task starts from the synced remote default
