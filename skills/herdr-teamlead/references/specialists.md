# Specialist Bench

Compose the active team around the next task decision. A profile is reusable
expertise, not a permanent Herdr pane. Bring a specialist in when its answer or
artifact could change the work; keep unused profiles on the bench without
launching workers. Reassess composition when evidence, scope or the task phase
changes.

## Separate responsibility, specialty and worker

**Responsibility** states what the assignment must deliver: implementation,
investigation, advice, review, verification or release. **Specialty** states
which expertise the assignment needs. **Worker** names the actual execution
session, model, tools and measured capacity. Record all three in the plan and
task ledger. Renaming a worker cannot change what it contributed or grant new
authority.

The normal implementation, reviewer, tester, release and judge contracts remain
in `rules/agent-team-operation.md`. A consultation uses the `advisor`,
`investigator` or `architect` responsibility and produces its bounded deliverable
and report. A specialist who needs to implement receives the normal
developer assignment, with a provisioned writing worktree and the relevant
profile in its brief. A specialist report does not substitute for a required
reviewer or tester pass. A specialist may perform that gate only through its
normal verification responsibility with the required evidence and independence.

Separate requested expertise from proven capability. Before considering
headroom, assess whether a candidate has the tools, relevant available skills,
task context and demonstrated results needed for the assignment. Read its
evidence-linked prior work where available. A model name or profile title alone
is no evidence of competence. Record material capability gaps and their effect
on the requested deliverable. Use measured headroom among suitable candidates;
when none qualifies, record the gap instead of silently assigning an unsuitable
worker. The pinned judge remains outside ordinary staffing.

## Choose relevant profiles

Read only the profiles needed for this task. They are prompts for selecting
questions and evidence, not checklists that every change must complete.

| Profile | Bring it in for | Useful output |
| --- | --- | --- |
| [UX and product](specialists/ux-product.md) | A new flow, confusing behavior or unresolved interaction choice | Concrete flow, alternatives and acceptance criteria |
| [Accessibility](specialists/accessibility.md) | An affected user path needs keyboard or assistive technology evidence | Reproducible findings with coverage and manual-check gaps |
| [Investigator](specialists/investigator.md) | Unclear causality or repeated unsuccessful fixes | Reproduction, causal assessment and discriminating experiment |
| [Architect](specialists/architect.md) | Cross-component choices or lasting contracts | Decision note with options, consequences and verification needs |
| [Security](specialists/security.md) | A changed trust boundary or concrete security question | Bounded threat assessment and actionable findings |
| [Performance and reliability](specialists/performance-reliability.md) | Latency, concurrency, resource or recovery uncertainty | Measured explanation and reproducible failure or improvement check |
| [Documentation](specialists/documentation.md) | Readers must understand or operate changed behavior | Verified draft or findings against the actual workflow |

Combine compatible expertise in one bounded assignment when a worker can cover
it. For example, a UX worker with the required tools may assess accessibility
on the same flow. Split the work when the evidence, tools or independence
requirements differ. The bench is extensible: describe another specialty when
the task needs one, with its capability evidence and concrete deliverable.

## Compose a bounded consultation

State the question the specialist must settle, why its answer matters now, the
accepted behavior, scope, relevant prior decisions, available inputs, permitted
actions and stopping condition. Define what the lead will inspect to accept
the result. Supply the source paths and revisions, useful project lessons, and
the selected profile's applicable questions in the brief. The worker should
not need an earlier conversation to reconstruct its assignment.

Name actual available tools and skill invocations when they improve the work.
Check availability before claiming a capability. Missing tools are an evidence
gap to resolve or report; do not claim visual inspection, user research,
assistive technology coverage or measurements that the worker cannot perform.
Avoid activating adjacent specialist workflows solely from a changed filename.

Compose each consultation with `templates/brief-specialist.md` through the
composer; its canonical consultation roles select that shared template. Supply
`TASK`, `SPECIALTY`, `RESPONSIBILITY`, `OBJECTIVE`, `ACCEPTANCE_CRITERIA`, `INPUTS`,
`TOOLS_AND_SKILLS`, `SCOPE_LIMITS`, `CONTRIBUTION_HISTORY`, `KNOWLEDGE` and `REPORT`,
plus the common task, branch and authority values. Set `RESPONSIBILITY` to the
planned role. Put applicable profile questions and evidence in the actual
brief values; a bare profile name is insufficient. For a developer, reviewer or
tester with specialist requirements, supply the same bounded expertise context
in `SPECIALIST_CONTEXT` within its normal role brief.

Consultations read evidence and write only their assigned report or draft
artifacts. Follow the normal composition, authority classification,
YOLO verification, retrospective, ledger and fleet-supervision steps for every
dispatch. A specialty adds no independent permission, correction attempt or
release waiver. Stop a consultation once its assigned deliverable is ready or
its genuine block is recorded; idle bench membership creates no monitoring job.

## Preserve contribution history and knowledge

Track actual contributions across session clears, worker changes and model
changes. Record who originated or materially shaped the accepted design, wrote
the implementation, or authored the artifact under review. Consultation that
only inspects evidence may remain independent; a specialist that shaped the
solution must disclose that contribution. Decide independence against the
subject being verified, never against the worker's current title or a fresh
context. Obtain another qualified worker for independent assessment of a
contributor's work. Keep the ordinary reviewer and tester gates intact.

Keep a useful worker idle after its report when follow-up is likely and capacity
permits. An idle session is optional continuity, not durable memory or authority
to dispatch into it without checks. Use the verified specialist continuation
path for a follow-up within the same engagement; follow the dispatch recovery
contract for its inputs, refusal conditions and evidence. Every other next
assignment follows the normal clear or developer retained-fix path. Before
clearing, relaunching or changing a seat or tier, complete the required
retrospective and capture useful outgoing knowledge.

Use `skills/herdr-teamlead/references/working-memory.md` for project lessons and
lead handoffs. Keep a specialty label consistent, such as `specialty:ux-product`,
alongside the project and task labels. The memory owner's scope selection is a
union; inspect each returned lesson's scope before applying it to a different
project. Revalidate source evidence before including a lesson in a fresh brief.
Workers propose lessons in their reports; the lead curates them through the
existing owner. Keep task outcomes, user attention and immutable retrospective
notes in their existing owner artifacts.

## Evaluate the composition

At the required retrospective, assess which specialist contribution changed a
decision, prevented rework or exposed a missed requirement. Note expertise that
arrived too late and consultations that produced no useful change. Compare the
observed benefit with waiting time, repeated context setup and capacity use;
mark unavailable measurements as unknown. Propose a concrete composition change
with an owner and success criterion, then revisit it in the next applicable
retrospective. A new profile or durable lesson should follow evidence of useful
work, not a desire to fill every possible seat.
