---
alwaysApply: true
---

# Ship on Green

## Green Gate Is the Approval

- Required checks green and none of the exits below apply ⇒ merge
- Do not pause to ask a human whether to merge
- Flagging, confirming, "say go and I'll", "worth one confirmation" and "want me to?" count as asking
- Governs every merge, with or without `skills/release`

## Stakes Raise Care, Not Permission

- A wide-reaching, always-on, ships-to-prod, deleting or otherwise high-impact change gets harder verification: more tests, tighter review, a pristine-checkout lint
- High impact is never itself an exit

## The Three Exits Are Exhaustive

- Merge is blocked only by one of these three exits, each judged by its literal test:
- **Red** — a required check is failing or pending
- **No undo** — `git revert` cannot restore it: force-push over protected history, an immutable published artifact, deleted data, sent external comms
- A supersedable version bump or shipping to prod does NOT qualify as No undo
- **Murky** — the task's own source states 2+ conflicting options AND names no default
- Source names a default ⇒ take it, never Murky
- The watch and verify steps of `rules/ci-safety.md` feed the Red test, never a fourth exit
- An unaddressed advisory finding never makes a gate Red, per `rules/review-severity.md`
