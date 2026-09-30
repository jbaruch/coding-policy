# Release Contract

Moved from `rules/ci-safety.md` (#642) to keep the always-loaded rules under the instruction budget. It binds as rule content: `rules/ci-safety.md` Always Watch CI and Publish Outcomes require reading it before watching a PR's reviews, confirming a release, or diagnosing a publish run. Its bullets are unchanged from the rule.

## Pre-Merge Watch Mechanics

- Reading an open PR's current gate evidence without merging runs `skills/release/poll-pr-reviews.sh` once, never the blocking watch
- The snapshot's `requested` is exactly one fact: a review for that login is still owed on a request
- Owed means a request still pending on the PR, or a Copilot run that consumed one with no review posted since — predicate in `skills/release/copilot-run.sh` `copilot_run_in_flight`
- It is false once the owed review has posted, and false for a push-triggered reviewer, which is never requested at all
- A same-head review posted before the request or run it answers does not clear it
- Resolve a reviewer's arrival by how it is triggered: a push-triggered review is owed by the push, a request-triggered one only once requested
- A request-triggered lane with no posted review and `requested` false is diagnosed and named, never waited out
- Never wait on a request-triggered review the waiting role has no scope to request
- The gating reviewer's bot login varies by repository — resolved inside `poll-pr-reviews.sh`
- A non-gating check that reds and surfaces as the watcher's `ci_failure` result is not a reason to hand-roll
- On a `ci_failure` result, read the returned snapshot and act on the check that actually failed
- The gating signal is not always a PR status check
- A run started out of band (a `workflow_dispatch` reseed, a manual job) may not surface in the PR's `statusCheckRollup`
- Identify the run that gates the outcome and bind the watch to its `conclusion`
- A failed PR check that no event re-triggers stays red until an explicit `gh run rerun --failed` once its cause is fixed

## Publication Confirmation

- For plugin/package releases, the duty extends past merge — confirm the resolved run's conclusion and the publication's own published-artifact evidence; no single signal is authoritative
- The duty is keyed on the publication, never on the package — a package that publishes through more than one channel owes it once per publication, each confirmed against the channel that carried it
- The release contract below is the Tessl form: its registry-baseline capture, its registry-advance and moderation conjuncts, its moderation wait and `skills/release/verify-moderation-cleared.sh` confirm a Tessl publication and nothing else
- Every Tessl publication keeps that contract whole, mixed distribution included — a tag, release or artifact on another channel never substitutes for the Tessl registry advance or the moderation clear
- A publication through another channel substitutes that channel's own published-artifact evidence for those Tessl mechanics
- That evidence is two facts, both required:
  - The immutable release or tag exists
  - The artifact is retrievable at the version the run attempted
- A GitHub tag/asset publication reads that evidence through `skills/release/verify-github-release.sh`
- A Tessl publish confirmed on the registry says nothing about another channel's release, which needs its own evidence
- Channel-independent, whatever publishes the package:
  - Resolve the run for that publication, bound to its workflow, its exact commit, the `push` event and the ref that fired it
  - Watch that resolved run to a terminal state
  - Require its `conclusion` to be `success`
  - Verify the version actually published on the channel that carried it
  - Never report a release confirmed while its publish is unconfirmed
- Two runs matching all four binding facts are an ambiguity to resolve, never a winner to pick — see `skills/release/resolve-publish-run.sh` header
- Release contract:
  1. Before merge: capture the registry's `Latest Version` as baseline
  2. After merge: resolve the publish run by merge-commit `headSha` + `push` event filter
  3. Watch the resolved run to terminal state
  4. Confirm the conjunction (all required, in this order):
     - The resolved run's `conclusion` is `success`
     - The registry's `Latest Version` advanced past the baseline
     - The published version's moderation state has cleared
  5. Wait for the moderation clear with exponential backoff before reporting the release confirmed
- See `skills/release/SKILL.md` Step 7 for the agent-executable form of the release contract
- Do not derive an expected version from the merge SHA's manifest
- Do not compare against a specific expected version
- Tessl moderation gates `tessl install` after publish — a freshly published version can be install-blocked until its moderation state reaches `pass`
- The moderation wait uses exponential backoff to a bounded budget — see `skills/release/verify-moderation-cleared.sh`
- A still-pending or blocked state at budget exhaustion is an unconfirmed release, surfaced as a failure, never reported as success
- A security finding is distinct from moderation
- A security advisory only suggests review
- A blocking security finding requires an override flag for `tessl install`
- If any conjunct fails, the publish is not confirmed — query the real moderation state, never invent one to hedge a failed publish
- Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (e.g., `tesslio/patch-version-publish`) and the run got past it
- Recover instead with a follow-up commit, which fires a fresh publish on merge

## Credits Never Block Publishing

- The tessl publish step never consumes credits
- The publish step never blocks on org credit state
- The artifact lands regardless of the credit balance
- Credits can still fail a review or eval step and turn the run red (see `rules/context-artifacts.md` Credit-Outage Review Carve-Out)
- A red run (credit-caused or not) is never proof that publishing was blocked
- Whether the artifact published is answered by the registry advance plus moderation `pass`, independent of the run's color
- A red run whose artifact landed means a step other than the publish failed, not a blocked publish
- Never blame a red publish run on credits without confirming the artifact landing and naming the failing step from the logs
- A publish that did not land is the agent's own diagnosis, never a deflection to `tessl credits`

## A Non-Zero Publish Exit Is Not Proof Nothing Published

- A publish command can exit non-zero after the artifact already landed
- Tolerate such an exit only for the terminal-failure classes the publisher's own allowlist names — see the signature constants at the top of `skills/release/smart-publish.sh`
- Every other non-zero exit stays red, whether or not the version appears afterwards
- Confirm a landing by the EXACT version the run attempted, never by a registry advance
- A version already present before the run proves nothing about that run
- The pre-publish absence of that version is required
- An indeterminate registry read is never a landing
- A rejected manifest bump-push after a landed publish stays red
- Deterministic form is `skills/release/registry-has-version.sh`, called from `skills/release/smart-publish.sh` — decision predicate in those headers, not restated here (`rules/script-as-black-box.md`)
