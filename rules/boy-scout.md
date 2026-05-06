---
alwaysApply: true
---

# Boy Scout Rule

## The Principle

- Leave the codebase in better shape than you found it. If you see something wrong while doing something else, fix it
- "Pre-existing" is not a valid concept. The state you observed is the state you own, regardless of who or what put it there
- Applies to anything you'd flag if a colleague had just written it: failing tests, broken docs, dead code, stale comments, lying type signatures, leaked secrets, missing newlines, unbumped versions

## Why "Pre-existing" Is Cope

- Most bypass attempts and gate-evasion patterns ("not my regression", "pre-existing", "out of scope for this PR") share this framing. Patching the bypass paths individually plays whack-a-mole; ruling out the framing closes the source. The bypass paths themselves are forbidden separately by `rules/ci-safety.md`'s "Never Skip Tests" and `rules/context-artifacts.md`'s "Disagreeing With the Reviewer"
- "I didn't break it" is true and irrelevant — the question is whether you'll leave it broken when you walk away. The repo doesn't care who broke it; it cares whether it's broken when the next person reads it
- Quality gates measure the merged state, not the delta. Treating gates as "your delta only" rots them on the same timescale as bypassing them outright

## How to Apply

- **In-scope cleanups** (typo, missing newline, broken doc link, small drift): roll into the current PR. The bundle is fine when the PR's stated scope still reads coherently
- **Out-of-scope discoveries** (untested module, contradicting rules, unrelated security gap): open a follow-up PR — or an issue if the fix needs design — and reference it from the current one. Walking away with no record is the failure mode this rule prevents
- When unsure whether to bundle or split, prefer **bundle small + cite** or **split large + file**. The wrong answer is "leave it"

## Reconciliation With `commit-conventions`

- `rules/commit-conventions.md`'s "Keep PRs focused" and this rule appear to conflict. They don't. Focus governs the SHAPE of the bundle (one logical change per commit / PR); boy-scout governs whether you walk away from problems you noticed (no, you don't)
- A focused PR can include adjacent cleanup commits when the scope reads as one cohesive change. A focused PR cannot include unrelated rewrites — but it CAN include a follow-up reference to where those are tracked
