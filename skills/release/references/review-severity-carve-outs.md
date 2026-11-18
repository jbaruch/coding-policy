# Review Severity Carve-Outs

The full preconditions of each `rules/review-severity.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Each section carries only the moved detail; the trigger, applies-when and reset lines stay in the rule.

## Judge-Weighed Finding Carve-Out


- A weighing ruling comes from the operator, `AUTHORITY: operator`, or a software-factory judge, `AUTHORITY: judge`
- The ruling file follows the format the `skills/release/dismiss-ruled-review.sh` header names
- An operator weighing asks whether fixing a finding is worth its cost, never whether the finding is real
- A judge rules only `decline` or `defer`
- A judge `decline` demonstrates that the finding misreads the code or the governing policy
- A correctly applied binding rule the judge considers defective requires operator authority
- The judge cannot waive a binding rule
- A judge `defer` requires all of:
  - evidence that the finding is pre-existing or outside the PR's goal
  - the named follow-up issue
  - an explanation of why fixing now costs more than the failure it prevents
- A real defect the change introduced stays the operator's risk acceptance, including one described as outside scope
- The agent nominates a finding only when it sits on lines the previous fix push added, or with a cited reachability claim marking it marginal
- A gate with any nomination no judge ruling covers goes wholly to the operator
- The agent asks the operator one decision question per gate, carrying every nomination and its claim
- The question never blocks the round
- The agent keeps fixing until the answer arrives
- No answer means `fix`
- An operator ruling file is the operator's reply to that question, copied verbatim under `AUTHORITY: operator`
- A judge ruling file is the judge's collected report, emitted whole in the ruling format under `AUTHORITY: judge`
- The release agent cites the judge's report by absolute path and sha256 digest and never rewrites it
- Each judge FINDING line states its ground
- Preconditions (all required):
  1. The ruling rules the finding `defer` or `decline`
  2. The ruling's authority covers the verdict:
     - `AUTHORITY: operator` quotes the operator's reply verbatim
     - `AUTHORITY: judge` is a judge's report whose sha256 matches the cited digest, and the verdict is a judge `decline` or `defer` stating its ground
  3. The ruling names the finding at its `HEAD:`, and the finding's file is unchanged from that commit to the head
  4. The finding is under no floor
  5. The finding is entered in the task's follow-up issue citing the ruling, a `decline` labelled won't-fix
  6. Every other release gate holds
- Floors:
  - a failing test, lint, diagnostic or required check
  - a security or data-loss defect reachable by normal inputs
  - a `no-secrets` or `ci-safety` finding
  - a carve-out's unmet precondition
  - an unmet acceptance criterion the operator stated
- Either authority rules `fix` on every floor
- `skills/release/dismiss-ruled-review.sh` refuses the rule-id floors and a failing check
- A ruling never skips, disables or removes a test
- A gating policy review the ruling covers is dismissed per `rules/ci-safety.md` Judge-Ruled-Review Dismissal Carve-Out
