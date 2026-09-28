# Assignment Reasoning

The foreman applies this reference at intake, when composing a bug-fix brief, and
when routing findings before a correction. The worker receives the relevant
questions in its self-contained brief. The reasoning is the workers': the
foreman runs on a cheap tier, records and routes, and never reasons through
task content (`rules/agent-team-operation.md` Foreman Seat). The dispatch
utility does not infer intent or causality from prose.

## Preserve the Accepted Behavior

Record the operator's actual request and later decisions verbatim, separately
from any worker's proposed implementation. The request's own words carry the
observable behavior, acceptance criteria and explicit limits into every brief;
the foreman writes no criterion of its own. A request whose criteria are
unclear goes to an `advisor` consultation, whose report proposes them, and the
proposal becomes an operator decision before implementation. Use the
existing task identity and authorization record throughout corrections.

Resolve ordinary implementation choices within that accepted behavior. A hard
implementation, an additional necessary file, or a required regression test does
not by itself create a new permission requirement. A diagnostic recommendation
alone does not authorize implementation when the request was investigation only.
State investigation-only intent explicitly in the role's task text and select
the developer template's investigation branch. Its deliverable is a report;
implementation, push, PR and release instructions do not apply to that branch.

Gate investigation reports against the requested knowledge and evidence, including
required independent checks. An independent `reviewer` assesses the
investigation report against the request and returns the verdict the foreman
gates on. The implementation phases in Review Before PR apply
only to authorized code changes. Preserve blocking-finding, correction-allowance,
and judge rules for the investigation. A completed judge ruling (`uphold A`,
`uphold B` or `amend`) returns the investigation to its knowledge-deliverable
gate; it never supplies code-release authority. An `insufficient` ruling goes
through the investigator and re-adjudication path in
`skills/herdr-foreman/references/round-flow.md` Ruling Outcomes first.

## Assess a Finding's Scope

The reviewer or tester that raised a blocking finding classifies its scope in
its report, citing the acceptance criterion and the behavior the fix would
commit the project to provide. The foreman records that label and routes on
it; it does not re-derive it. The labels:

- **Required correction:** restores or completes accepted behavior, including
  necessary downstream tests and documentation. Continue within existing task
  authority and the remaining correction allowance.
- **Contract expansion:** introduces behavior or an obligation not required by
  accepted intent, such as a new guarantee, subsystem, supported environment, or
  continuing monitoring duty. Record it as a proposal for the operator.
- **Unresolved interpretation:** the evidence does not settle the requested
  behavior or whether the proposed fix is necessary. Record it as an operator
  decision naming that uncertainty.

A blocking finding with no scope label goes back to its responsibility with the
missing label named. A developer or foreman disputing a label is a contested
verdict for the judge.

A reviewer's label or confidence does not amend the accepted contract. Scope
classification also does not dismiss a blocking finding: a contested verdict,
foreman override, or bot disagreement follows the existing judge path in
`rules/agent-team-operation.md`. The foreman cannot waive a finding by calling it
an expansion. An agreed correction still obeys the fix allowance and release
gates; this reference adds no attempts or substitute judge trigger.

When an operator decision is needed, save an attention item before presenting it.
Quote the original requirement, the proposed expansion, and the smallest
compliant option, consequences and recommendation the reporting worker gave;
the foreman adds no recommendation of its own. Reuse a prior decision when its
scope still covers the action. Continue independent authorized work while the
affected task waits.

When a report names findings recurring on the same underlying design, the
foreman does not weigh the causes itself: it consults the `investigator`, whose
report compares the causes and the actual progress and says whether the changes
close distinct defects or keep compensating for an unresolved assumption. Put
that report in the next brief, existing judge dispute, or operator proposal as
applicable. Preserve the cumulative attempt count.

## Diagnose the User's Failure

For a bug task, ask the developer and tester for a reproduction matching the
actual user path. Record expected and observed behavior, setup, inputs, and
repeatability. If a faithful reproduction is unavailable, state the limitation
and what the substitute does and does not establish.

Separate the initiating trigger, any condition that hides or exposes the fault,
and the symptom the user sees. Compare the failing path with a demonstrated
working path and locate the earliest relevant difference. Inspect history when
it can explain that difference; proximity to a recent commit is not causal proof.

Name the leading explanation and an observation that would disprove it. Run the
smallest feasible counterfactual, changing one relevant condition at a time,
and retain contradictory results. Explain how the proposed cause accounts for
both paths. Label facts, hypotheses, and unresolved uncertainties separately.

## Use the Evidence

A diagnosis is accepted on independent evidence, never on the foreman's reading:
the tester verifies the reproduction fails before the fix and passes after it,
and the reviewer checks that the stated cause explains the reproduction and
comparison evidence. A missing experiment is an explicit gap, not an invented
pass. A report naming a gap that could change what should be fixed routes to a
focused `investigator` consultation. Keep bounded research inside an already authorized bug
fix when it does not require a separate deliverable or decision.

When implementation is authorized, turn the reproduction into an appropriate
regression test. The tester verifies the failing behavior before the fix and the
expected behavior after it where feasible, and records any limitation. Retain
the evidence in the normal report so the retrospective can examine failed
assumptions and successful diagnostic methods.
