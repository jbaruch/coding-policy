# Review Severity Carve-Outs

The full preconditions of each `rules/review-severity.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Text is unchanged from the rule.

## Judge-Accepted Defect Carve-Out

- Narrow exception for shipping with a blocking finding still open
- Applies when a fix loop did not converge and the pinned judge's diagnosis answers it with `REMEDY: stop`
- Preconditions (all required):
  1. The task's current exhaustion carries a recorded `stop` diagnosis under `skills/herdr-foreman/references/team-operation.md` Judge Seat
  2. The remaining blocking finding is recorded as a tracked accepted defect with its issue reference
  3. The shipped scope excludes that defect's work, and what ships carries no other blocking finding
  4. Every other release gate holds: CI green, the external reviews, and independent reviewer and tester passes on the shipped tip
- The operator overrides a `stop` like any ruling, by authorizing a plan over it
- Every other blocking finding is fixed before merge

## Judge-Weighed Finding Carve-Out

- Narrow exception for merging with a blocking finding a weighing ruled `defer` or `decline`
- Applies when fixing the finding costs more than the failure it prevents
- A weighing asks whether fixing a finding is worth its cost, never whether the finding is real
- The ruling file follows the format the `skills/release/dismiss-ruled-review.sh` header names
- In a Herdr team round the pinned judge weighs under `skills/herdr-foreman/references/team-operation.md` Judge Seat
- In standalone mode the operator is the judge
- The standalone agent nominates a finding only when it sits on lines the previous fix push added, or with a cited reachability claim marking it marginal
- The standalone agent asks the operator one decision question per gate, carrying every nomination and its claim
- The question never blocks the round
- The agent keeps fixing until the answer arrives
- No answer means `fix`
- The agent records the answer verbatim as the ruling file
- Preconditions (all required):
  1. The ruling rules the finding `defer` or `decline`
  2. In standalone mode, the ruling file carries `AUTHORITY: operator` and quotes the operator's answer verbatim
  3. In a team round, the ruling file carries `AUTHORITY: judge` and `foreman verify-ruling` binds it to the report supervision enrolled for the pinned judge's weighing
  4. The ruling names the finding at its `HEAD:`, and the finding's file is unchanged from that commit to the head
  5. The finding is under no floor
  6. The finding is entered in the task's follow-up issue citing the ruling, a `decline` labelled won't-fix
  7. Every other release gate holds
- Floors:
  - a failing test, lint, diagnostic or required check
  - a security or data-loss defect reachable by normal inputs
  - a `no-secrets` or `ci-safety` finding
  - a carve-out's unmet precondition
  - an unmet acceptance criterion the operator stated
- The judge rules `fix` on every floor
- `skills/release/dismiss-ruled-review.sh` refuses the rule-id floors and a failing check
- A ruling never skips, disables or removes a test
- A gating policy review the ruling covers is dismissed per `rules/ci-safety.md` Judge-Ruled-Review Dismissal Carve-Out
- Every other blocking finding is fixed before merge
