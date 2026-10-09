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
in `skills/herdr-foreman/references/team-operation.md`. A consultation uses the `advisor`,
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

Read only the profiles needed for this task. Most are prompts for selecting
questions and evidence, not checklists that every change must complete. Five
are triggered: the condition fires, the profile is consulted, and its
deliverable lands before implementation proceeds. Four of them allow a
recorded staffing decision instead, with its reason, the way a shortfall of
eligible workers already does. The exhaustion trigger does not: the judge's
diagnosis rules on that assessment, and `foreman diagnose` refuses without
it. Legacy review below requires staffing every fired trigger; decisions cannot
answer its triggers.

| Trigger | Profile | Deliverable before implementation |
| --- | --- | --- |
| A new or substantially changed package above the size the repo states | Architect | The intended boundaries, the options and consequences, and the verification each boundary needs |
| A new or changed trust boundary — anything deciding whether foreign input, generated content or a proposed change is safe | Security | A bounded threat assessment against that boundary |
| A new user-facing command, flag or refusal path | UX and product | The flow, the alternatives considered, and acceptance criteria |
| A new user-facing document | Documentation | A draft verified against the shipped behavior, never against intent |
| Fix rounds reaching the allowance without converging | Investigator | A reproduction, a causal assessment and a discriminating experiment. Required; no staffing decision substitutes for it |

The exhaustion trigger is enforced where the judge is dispatched. The other
four are detected from the round's diff.

## Declare this repo's trigger surfaces

`foreman detect-triggers` classifies a diff against `.herdr/triggers.json` in
the consuming repo. Every field is required; an absent or incomplete
declaration is refused rather than read as "nothing fired", so the architect
trigger no longer fires never or always depending on who reads it.

```json
{
  "schema_version": 1,
  "package_roots": ["skills/*", "scripts"],
  "package_change_lines": 400,
  "trust_boundary_paths": [".github/workflows/*", "hooks/*"],
  "cli_spec_paths": ["skills/herdr-foreman/foreman/*.py"],
  "cli_surface_markers": ["add_parser(", "add_argument("],
  "user_doc_paths": ["README.md", "docs/*"]
}
```

`package_roots` name the directories this repo treats as packages, and the
nearest matching ancestor owns a changed file. A package root is a directory
rather than a subtree, so `*` matches within one path segment there: `skills/*`
is every skill, never a directory nested inside one. `package_change_lines` is
the size a changed package must exceed to trigger the architect.
`trust_boundary_paths`, `cli_spec_paths` and `user_doc_paths` are subtree
globs, where `*` does span path separators, so `docs/*` covers everything under
`docs`. `cli_surface_markers` are literal substrings that an
added line inside a CLI spec path must carry to count as a new command or flag.
State `[]` for a surface this repo does not have — an empty list is a statement,
an omitted field is not. Classification rules are in
`skills/herdr-foreman/foreman/triggers.py`, in its module docstring and the
`detect` and `cli_surface` docstrings.

Run it before `plan`, with the roles and requirements that round intends. This
is a synopsis; the runnable command, through `bash` and the resolved plugin
root, is `skills/herdr-foreman/SKILL.md` Step 5:

```text
foreman detect-triggers --repo <dir> --base <ref> [--head <ref>] \
  --roles <role[,role...]> [--requirements <file>] [--planned <file>] \
  [--decisions <file>] [--bootstrap-declaration <reviewed-file>]
```

An unchanged historical PR uses the separate legacy-review synopsis below.

## Declare a pre-implementation round's surfaces

The triggers gate work before implementation, and a task's first round has
nothing committed to classify. `--planned` supplies the surfaces the work will
touch, classified against the same declaration. A round that classifies neither
a diff nor a plan is refused.

```json
{
  "schema_version": 1,
  "added": ["skills/new-thing/mod.py", "docs/new-guide.md"],
  "changed": ["skills/herdr-foreman/foreman/recovery.py"],
  "package_lines": {"skills/herdr-foreman": 800},
  "cli_surface": ["skills/herdr-foreman/foreman/cli.py"]
}
```

`added` and `changed` are repo-relative paths the round will create or edit.
`package_lines` states the lines the round will change in a package, for the
architect trigger's size. `cli_surface` names the declared CLI spec paths the
round will add a command, flag or refusal to; a path outside
`cli_spec_paths` is refused. State `[]` or `{}` for what this round has none
of. A later round classifies its diff, which is evidence rather than intent.

### Bootstrap the first declaration

When the recorded base has no `.herdr/triggers.json`, an accepted consultation
may deliver the first declaration as an external report artifact. Keep those
reviewed bytes outside the repo until the developer round. The first consultation
uses the ordinary no-write plan below: no declaration is required for that
read-only round. Its report supplies one structured evidence line:

```text
TRIGGER_DECLARATION: {"repo":"/absolute/target/repo","base_revision":"<exact recorded task commit>","path":"/absolute/external/triggers.json","sha256":"<SHA-256 of reviewed bytes>"}
```

Only a standalone line at column 0 is evidence. A copy in a fenced block, a
blockquote, an indented block or inline code is documentation, not assessment
authority (`skills/herdr-foreman/foreman/report_contract.py` docstring), so a report may quote the
schema above and still carry its one live line.

The consultation worker records the artifact it actually reviewed; the foreman
never writes approval evidence on its behalf. Normal `assess-specialist`
validates the artifact and digest while recording the delivered report's
existing contract and receipts. Every criterion must be met and the verdict
must not be blocking. `detect-triggers` automatically consumes those existing
owner assessments from its normal `--state`; no separate acceptance receipt or
approval flag exists. Changed report bytes, artifact, repository or task base
establish no authority. Missing historical reports establish no authority and
require no unrelated maintenance. Add the declaration
path and its exact digest to the planned surfaces:

```json
{
  "schema_version": 1,
  "added": [".herdr/triggers.json"],
  "changed": [],
  "package_lines": {},
  "cli_surface": [],
  "bootstrap_declaration_sha256": "<lowercase SHA-256 of the reviewed bytes>"
}
```

Pass that artifact with `--bootstrap-declaration`. The detector validates it,
confirms the recorded base lacks the declaration, verifies the plan's digest and
the accepted consultation's unchanged report binding,
and classifies the planned task with those surfaces. The developer installs
those bytes unchanged. Re-run the same command with `--head` after the first
push; it refuses a missing or non-identical committed declaration. A changed
artifact or plan returns to consultation acceptance and trigger classification.

When the recorded task base already contains `.herdr/triggers.json`, its own
committed declaration is the sole authority. A first declaration at the head or
in the worktree still requires the bootstrap proof; omitting the flag refuses. Omit `--bootstrap-declaration` on
later rounds. A bootstrap artifact never overrides existing repo content.

### Ordinary no-write rounds

An ordinary round that writes no repository content at all — an investigation, an
architecture consultation, an advisory question — has no surface to declare and
would otherwise be refused as classifying nothing. It says so explicitly:

```json
{
  "schema_version": 1,
  "added": [], "changed": [], "package_lines": {}, "cli_surface": [],
  "writes_repository": false
}
```

Every trigger is quiet on such a round by construction: each one classifies a
repository surface, and this round touches none. The claim is checked rather
than taken — `--roles` must name only read-only responsibilities
(`READ_ONLY_ROLES` in `skills/herdr-foreman/foreman/triggers.py`), every other
planned field must be empty, and a
tracked diff against the base refuses it, since evidence outranks intent.
Untracked files are never listed or read on such a round: scratch in the
shared checkout is no surface of it, and an unreadable scratch file does not
refuse it. This explicit no-write plan needs no repo trigger declaration:
there is no surface for one to classify. If the declaration exists it must
still be valid; writing rounds still require the complete declaration.
Omitting `writes_repository` reads as `true`, so a plan written before the
field keeps its meaning.

### Classify an unchanged legacy PR

Use this mode only for read-only verification of an existing immutable PR head
whose base and head both predate `.herdr/triggers.json`. Keep its source and
head unchanged. A declaration at the base uses committed repository authority;
a first declaration at the head uses the writing bootstrap above instead.

Obtain a bounded consultation for the exact task and subject. Its report carries
one operative five-key line, assessed through the normal owner path above:

```text
TRIGGER_DECLARATION: {"repo":"/absolute/target/repo","base_revision":"<full base commit OID>","head_revision":"<full head commit OID>","path":"/absolute/external/triggers.json","sha256":"<SHA-256 of reviewed bytes>"}
```

Repository and artifact paths are canonical absolute paths. The existing
four-key writing-bootstrap line does not authorize legacy review. Changing the
task, subject, artifact path/bytes or assessed report requires a new bounded
consultation and ordinary assessment; re-running detection alone supplies none.
Quoted examples remain inert under the report-line contract above.

Pass the ordinary empty no-write plan, the intended read-only responsibilities
and their requirements, and the same owner state that holds that assessment.

Consultation responsibilities retain the normal explicit engagement, specialty
and capability requirements. Seat syntax must be valid for the normal planner.

```text
foreman detect-triggers --repo <dir> --base <full-base-OID> --head <full-head-OID> \
  --task <exact-task> --legacy-review-declaration <external-file> \
  --planned <empty-no-write-plan> --roles <role[,role...]> \
  [--requirements <file>] [--state <owner-state>]
```

The mode's input validation and subject binding are owned by
`skills/herdr-foreman/foreman/triggers.py` `load_legacy_review_declaration`;
its permitted responsibilities are `LEGACY_REVIEW_ROLES` there. Do not pass
`--bootstrap-declaration` or `--decisions`. Staff every fired trigger through its
required role or requirements specialty. Ordinary no-write tracked-diff refusal
and byte-identical writing bootstrap remain unchanged outside this mode.

Success emits schema-3 classification with `task` and
`declaration_authority.kind: legacy_review`, the exact subject/artifact tuple,
assessment id and unchanged report receipt. An authority failure emits no
classification. Unstaffed triggers emit classification on stdout and an
`unaddressed_trigger` error on stderr, exit 1; update staffing and rerun.
Detection writes no repository or state and contacts no worker. Exit 0 proves
composition only: it grants no writing, merge, release or future-task authority.
Independent current-head review/test reports, contribution exclusions and
normal external CI/review gates still apply.

Exit 0 means every fired trigger is staffed or answered. Exit 1 with an
`unaddressed_trigger` error names the triggers that are neither; re-run it
after each change, since the failed invocation read none of them. A trigger is
answered by planning its role or by a requirements assignment carrying its
specialty; which role and which specialty answer each trigger are the
`TRIGGER_ROLES` and `TRIGGER_SPECIALTIES` constants in
`skills/herdr-foreman/foreman/triggers.py`, and the detection payload names
the one it accepted. An advisor staffed for a fired `security` trigger is
planned with round context `{"advisor": {"security_trigger": true}}`, which
selects its judgment round (`references/model-tiers.md`).

Outside legacy review, a staffing decision answers a fired trigger instead, and
the detector reads it:

```json
{
  "schema_version": 1,
  "decisions": {
    "documentation": "the added file is an internal reference, not a reader-facing document"
  }
}
```

An empty reason is refused. Silence is never that decision. A decision for a
trigger that did not fire is reported under `unused_decisions` and changes
nothing.

| Profile | Bring it in for | Useful output |
| --- | --- | --- |
| [UX and product](specialists/ux-product.md) | A new flow, confusing behavior or unresolved interaction choice | Concrete flow, alternatives and acceptance criteria |
| [Accessibility](specialists/accessibility.md) | An affected user path needs keyboard or assistive technology evidence | Reproducible findings with coverage and manual-check gaps |
| [Investigator](specialists/investigator.md) | Unclear causality or repeated unsuccessful fixes, and every exhausted allowance | Reproduction, causal assessment and discriminating experiment |
| [Architect](specialists/architect.md) | Cross-component choices or lasting contracts | Decision note with options, consequences and verification needs |
| [Security](specialists/security.md) | A changed trust boundary or concrete security question | Bounded threat assessment and actionable findings |
| [Performance and reliability](specialists/performance-reliability.md) | Latency, concurrency, resource or recovery uncertainty | Measured explanation and reproducible failure or improvement check |
| [Documentation](specialists/documentation.md) | Readers must understand or operate changed behavior | Verified draft or findings against the actual workflow |

Combine compatible expertise in one bounded assignment when a worker can cover
it. For example, a UX worker with the required tools may assess accessibility
on the same flow. Split the work when the evidence, tools or independence
requirements differ. The bench is extensible: describe another specialty when
the task needs one, with its capability evidence and concrete deliverable.

## Record assignment requirements

Worker configuration schema v3 carries a `capabilities` list for each worker.
Keep these declarations aligned with the tools, skills and evidence inspected
above. A capability label is a staffing input, not a credential or authorization.
A `capabilities` change never follows one provider refusal or one unavailable
model identifier: one stopped session read repeatedly is one refusal. Remove a capability only on independent
refusals of the same class across sessions, each recorded through
`record-refusal`, and cite those dispatches in the change.
Follow `skills/herdr-foreman/state-schema.md` for configuration and persisted
assignment shapes.

Pass a requirements file to `plan --requirements <absolute-file>`. The file
names the expertise needed for each specialized assignment and the stable
engagement that a later consultation may continue. For example:

```json
{
  "schema_version": 1,
  "assignments": {
    "advisor": {
      "specialty": "ux-product",
      "required_capabilities": ["ux"],
      "independent": false,
      "engagement": "onboarding-ux"
    }
  }
}
```

Use the planned role as the assignment key. `required_capabilities` names the
worker capabilities the deliverable needs. `independent` states whether the
assignment requires an independent worker; the reviewer and tester gates retain
independence. `engagement` identifies the bounded consultation across its
follow-ups, not a new task or correction budget. Give each distinct engagement
its own identity and preserve the parent task identity.

The input parser in `skills/herdr-foreman/foreman/composition.py` owns the accepted keys and names.
Reviewer and tester requirements use `independent: true`. New advisor and
investigator assignments require a requirements file; supply it for every new
architect consultation too. Legacy architect records remain readable but establish
no specialist continuity. No extra model flags or invented role aliases belong
in a requirements file.

Include requirements for specialist consultations and for a developer, reviewer
or tester whose assignment needs that expertise. The planner emits the
requirements with its assignment; dispatch rechecks them from that saved plan.
Use that plan through the normal apply contract. On a refusal, resolve the
reported capability, independence or evidence gap before replanning. Do not
hand-edit the plan to substitute a worker or bypass its requirements.
Live specialized dispatch requires the foreman's existing supervision binding and
one enrolled report path per responsibility. Use the same state selection across
plan, apply, assessment, observation and follow-up.

## Compose a bounded consultation

State the question the specialist must settle, why its answer matters now, the
accepted behavior, scope, relevant prior decisions, available inputs, permitted
actions and stopping condition. Number the criteria its report answers. Supply the source paths and revisions, useful project lessons, and
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
planned role. `ACCEPTANCE_CRITERIA` is one `CRITERION <k>: <text>` line per
criterion, numbered from 1; the template renders it as the brief's single
`## Acceptance Criteria` section. `apply` refuses a consultation brief without
that contiguous block, and its count is the `N` the report answers. Put applicable profile questions and evidence in the actual
brief values; a bare profile name is insufficient. For a developer, reviewer or
tester with specialist requirements, supply the same bounded expertise context
in `SPECIALIST_CONTEXT` within its normal role brief.

Consultations read evidence and write only their assigned report or draft
artifacts. Follow the normal composition, authority classification,
YOLO verification, retrospective, ledger and fleet-supervision steps for every
dispatch. A specialty adds no independent permission, correction attempt or
release waiver. Stop a consultation once its assigned deliverable is ready or
its genuine block is recorded; idle bench membership creates no monitoring job.

## Assess specialist work

Confirm delivery through the normal report checkpoint and save its successful
JSON output. Read the actual report in full. Its contract lines decide its
outcome, never your reading of it: one `ACCEPTANCE <k>/<N>:` line per criterion,
one `VERDICT:` line for a `security`, `ux-product` or `documentation`
specialty, and at most one optional `CONTRIBUTION:` line. Line formats and
refusal classes are in `skills/herdr-foreman/foreman/report_contract.py`'s
module docstring.

Run the installed `skills/herdr-foreman/foreman.sh` with explicit `bash` and
the plugin root resolved by the skill. This synopsis names its owner command:

```text
assess-specialist --record /durable/team/assessment.json [--state <state-file>] [--now <ISO-time>]
```

The input supplies the actual per-assignment dispatch id from apply output or
the supervision member, the worker's report path, and the saved delivery path:

```json
{
  "id": "onboarding-ux-assessment-1",
  "dispatch": "onboarding-ux-dispatch-1",
  "report": "/durable/team/reports/ux-1.md",
  "delivery": "/durable/team/reports/ux-1-delivery.json"
}
```

`outcome`, `summary` and `contribution` are refused by name: the record reads
them from the report, never from you. The delivery file must be the successful
`wait-report.sh` JSON receipt for that worker and report, with `found: true`, or
the unchanged owner `recover-report` result for that exact dispatch and report.
For native delivery missed by the watcher, complete recovery under
`references/dispatch-recovery.md` first and save its actual output. The assessment
owner checks recovered output against the saved recovery record; an edited or
invented receipt does not establish delivery.
Use an actual dispatch identity and matching report path; never invent history
for a report that has not been reconciled with its assignment.

The command re-derives `N` from the frozen brief the dispatch sent, parses the
report against the dispatch's own role and specialty, and emits JSON. It
preserves an immutable record with brief, report and delivery receipts in the
existing owner state; `skills/herdr-foreman/state-schema.md` Specialist
assessment records holds its shape. A report missing a required line, or
carrying an extra, duplicate, mismatched or malformed one, records nothing
beyond a declared `design` or `implementation` contribution, and names the gap: record `needs_work` and re-dispatch the same responsibility with
that gap named. Keep the referenced evidence files for future verification.
An exact retry with the same assessment id returns its original record. A new
assessment uses a new id and preserves the old evidence. Run it on every
reviewer and tester report too, where the contract is one `VERDICT:` line;
`close-member` refuses `accepted` without it. Developer work remains on the
normal developer receipt and correction path.

Record the outcome the lines support in the task ledger, citing the report: a
consultation with every criterion `met` may be `accepted`, and an `unmet`
criterion is `needs_work`. Handle pending
supervision events and retire the preceding enrollment through
`skills/herdr-foreman/references/supervision.md`. Assessment, event
acknowledgement and enrollment retirement are distinct operations. None of them
accepts the whole task or replaces its verification and release gates.

Before requesting a retained specialist follow-up, save the assessment and
finish those observation obligations. Preserve the assessed report and delivery
bytes; changed or missing source evidence requires reconciliation. Follow the
dispatch recovery contract for the continuation command and its refusal
conditions.

## Preserve contribution history and knowledge

Track actual contributions across session clears, worker changes and model
changes. A contribution only ever adds an exclusion; nothing clears one. A
worker is a possible contributor when its dispatch was classified one before it
ran, or when its report declares `CONTRIBUTION: design` or `CONTRIBUTION:
implementation`. A plan's `--exclude` narrows that plan alone and is never
contribution history. A declared or legacy `none` changes nothing. Every consultation worker stays excluded from verifying its own task.
Decide independence against the subject being verified, never against the
worker's current title or a fresh context. Obtain another qualified worker for
independent assessment of a contributor's work. Keep the ordinary reviewer and
tester gates intact.

New reviewer assignments carry an explicit verification scope. Migrated reviewer
history retains unknown scope, which excludes, and architecture work remains a
possible contribution. The owner never infers independence from a newer schema
stamp. External authors and work without usable
task provenance still need the foreman's explicit exclusions. Follow the planning
contract in `references/round-setup.md` Step 5 rather than reclassifying history
from a worker's current label.

Keep a useful worker idle after its report when follow-up is likely and capacity
permits. An idle session is optional continuity, not durable memory or authority
to dispatch into it without checks. Use the verified specialist continuation
path for a follow-up within the same engagement; follow the dispatch recovery
contract for its inputs, refusal conditions and evidence. Every other next
assignment follows the normal clear or developer retained-fix path. Before
clearing, relaunching or changing a seat or tier, complete the required
retrospective and capture useful outgoing knowledge.

Use `skills/herdr-foreman/references/working-memory.md` for project lessons and
foreman handoffs. Keep a specialty label consistent, such as `specialty:ux-product`,
alongside the project and task labels. The memory owner's scope selection is a
union; inspect each returned lesson's scope before applying it to a different
project. Revalidate source evidence before including a lesson in a fresh brief.
Workers propose lessons in their reports; the foreman curates them through the
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
