---
alwaysApply: true
---

# CI Safety

## Required Read

- Read the named sections of `skills/coding-contracts/references/ci-safety.md` before acting.
- CI configuration changes → `Hands Off CI Config` and `Install, Don't Skip`.
- Pushes, PR review reads, review handling and merges → `Always Watch CI`.
- Diagnosing queued checks with no Actions run → `Checks Not Starting`.
- Diagnosing a publish run → `Publish Outcomes`.
- Looking up a release readback helper → `Canonical Release Readback`.
- Relying on a CI exception → its named carve-out.
- Before releasing a package or plugin, read `Publication Confirmation` in `skills/release/references/release-contract.md`.

## Branches and Commits

- Name branches `<type>/<description>` in lowercase with hyphens.
- Use `<type>-<issue-number>` only where existing branches follow that pattern.
- Flag branch naming only before a PR exists.
- Never add CI-skip markers such as `[skip ci]`.
- Use pull requests outside a binding direct-push carve-out.
- Never push directly to protected branches, including `main` and `master`, outside a binding carve-out.

## CI and Review

- Keep CI changes within authorized scope.
- Release helper lookup grants no authority.
- Never disable or skip failing tests.
- Never bypass a review gate outside a binding carve-out.

## Always Watch CI

- Watch every push to its required terminal gates.
- Confirm each required publication before reporting a release confirmed.
