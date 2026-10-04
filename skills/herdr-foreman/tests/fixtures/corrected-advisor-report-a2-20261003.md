# Advisor report — operator-intervention audit (reset recovery), contract-corrected

- Task: `coding-policy-operator-interventions-20261003`
- Role: advisor, specialty `operator-intervention-audit`
- This file: `/Users/jbaruch/.hr/a2.md` (report-contract correction only)
- Immutable original: `/Users/jbaruch/.hr/a.md` (27k, 2026-10-03 16:52; not edited)
- Source base (original brief): `5dff21e2a216c4637afba84e45880ba6df7c7b31`
- Installed owner (original audit): coding-policy `0.3.360`
- Shared checkout: `/Users/jbaruch/Projects/nanoclaw` (read-only; no git)

## Assigned question

Complete the existing operator-intervention audit with a corrected report contract. Preserve the original report’s substantive findings. Revalidate the three acceptance criteria from the supplied prior evidence. Remove the misleading reserved-prefix prose that caused owner assessment refusal. Do not implement. Do not inspect source again.

## Answer

The original audit’s answer still holds.

The operator-only reset recovery rule is a reversible operational chore for the failure class that actually happened (`scheduled`, never claimed, nothing typed). Treat that class as owner-owned automatic recovery. Keep operator look-first only when typing already began (`interrupted`) or the pane may already hold a resumed foreman.

The silent failure was not “the operator forgot a documented step.” `foreman-reset` exited 0 with `next: End this turn now; the deliverer clears the pane once it is idle`. The deliverer then skipped claim on a launcher-vs-runtime argv digest mismatch and returned **exit 0** `skipped: not the scheduled owner of this reset`, without `fail_unclaimed`. The row stayed `scheduled` until a later `foreman-reset-reconcile --outcome failed`. Catch-up can list that class; the scheduling path advertised automatic resumption. That is the uninformed intervention.

**Initial repair (later developer brief):** (1) keep the investigator’s launch-identity handshake so claim sees the stable runtime; (2) never skip claim as success; (3) auto-finalize and auto-deliver a never-typed failed reset under the same session/idle/composer/process checks the deliverer already uses; (4) make failure and recovery visible; (5) leave genuine authority gates alone.

This round changes none of that design. It only republishes the audit so owner assessment can read a single terminal design declaration.

## Contract correction (this round)

Owner assessment of `/Users/jbaruch/.hr/a.md` refused. Supplied refusal evidence:

- `/Users/jbaruch/.local/state/foreman/rounds/nanoclaw-20261003/intervention-assessment.json` is a 0-byte file (written 2026-10-03 16:53).
- Input named that report: `intervention-assessment-input.json` (`id` `intervention-audit-assessment`, `report` `/Users/jbaruch/.hr/a.md`).
- This brief: owner refused only the prose line that began with the reserved prefix and declared design in running text.

The refused line in `a.md` was:

`CONTRIBUTION declared below as design.`

That line is omitted here. Design authorship remains the terminal contract line at the end of this file. No other `a.md` line started with `ACCEPTANCE` or `CONTRIBUTION` except the three `ACCEPTANCE k/3` lines and the terminal `CONTRIBUTION: design` line.

## Revalidation (no new source inspection)

Re-read in this seat: `a.md` (full), prior `brief-advisor.92505af785890ade.md`, prior `COMMON.47aca3938c60d720.md`, `reset-recovery-diagnosis.md` (full), `intervention-assessment.json` (empty), `intervention-assessment-input.json`, `intervention-labels.json`, this round’s COMMON and brief.

The three criteria remain met on that evidence. Line citations below are the original audit’s citations, not a fresh pass over coding-policy source.

---

## Facts (evidence)

### Incident

- Stow `nanoclaw-fleet-ready-20261003`, pane `w1:p4`, scheduled `2026-10-03T21:07:56.976127+00:00`, PID 68956, digest `2df5a2f021aa7eb933a791856534ce8df6062bd0e295826a35c4945f5dd78e47`.
- Log (`state.json.foreman-reset.log`): skip, no claim, no pane client.
- Record now `failed`, reconciled `2026-10-03T21:40:36.927033+00:00`, message “deliverer stopped without an outcome.” Native session still `01a10297-f65b-7383-944e-4da049cecef8`.
- Investigator diagnosis (`reset-recovery-diagnosis.md`): 12/12 macOS launcher→framework argv transitions; reconstruction binds the stored digest to `/usr/local/bin/python3 -m foreman foreman-reset-deliver ...`. Claim requires exact stored-versus-live identity (`foreman_reset.py` `claim`, 616–632).
- Operator authorization for this audit (`intervention-task.json`): “this is some BS. I think for some reason coding-policy has tons of operator intervensions, most of which are unneeded and uninformed. We both had no idea I neeeded to do something.”
- Prior idle-pane restart authorization (`restart-lesson.json`, `readiness.md`): “If that solves it - just do that next time.” Scope: idle Herdr pane, preserve native session, no interruption of active work.

### Skip-as-success contract (this is the silent gate)

`skills/herdr-foreman/foreman/cli.py` 1964–1972:

- Claim exception → `fail_unclaimed` then `reset_ended`.
- `if not claimed: return {..., "skipped": "not the scheduled owner of this reset"}, None` → **exit 0**, row unchanged.

Pinned by `tests/test_foreman_reset.py` `test_a_deliverer_that_does_not_own_the_reset_sends_nothing` (1205–1209): expects `code == 0` and the skip string.

`schedule` (`foreman_reset.py` 413–420) probes identity immediately after `Popen.pid`. `process_identity` (`supervision_runtime.py` 60–73) hashes `ps -p PID -o lstart= -o command=`. Interpreter argv change after spawn changes the digest with PID and start time unchanged.

`foreman-reset` success text (`cli.py` 1893–1895) tells the caller the deliverer will clear once idle. SKILL.md Step 17 exit 0 (906–912) says the same.

`outstanding()` (`foreman_reset.py` 504–507) already knows a dead `scheduled` row typed nothing: “the foreman in pane {} still holds its old context.” That mechanical fact is then handed to `foreman-reset-reconcile` and operator paste (`OPERATOR_RECOVERY`, 119–122).

### Operator-only recovery rule (disputed)

| Location | What it requires |
| --- | --- |
| `references/team-operation.md` Working Memory 494–501 | Failed/interrupted reset: operator, never the foreman, confirms the pane is not already resumed, clears, pastes saved prompt |
| `SKILL.md` Step 17 913–922 | `reset_ended`: never re-run `foreman-reset` for this stow; record attention blocker; operator recovers |
| `foreman_reset.py` 27–33, 119–126, 458–516, 637–641 | One attempt; skip/fail authorizes operator paste; `ResetEnded` “only the operator recovers it” |
| `state-schema.md` Foreman Reset Record 1046–1056 | Dead `scheduled` finalized failed; then `reset_ended` for operator recovery |
| CHANGELOG ~0.3.271 | “A failed or interrupted reset is recovered by the operator, never the foreman” |

`references/working-memory.md` does **not** restate that carve-out; it points at `foreman_reset.py`. The three live copies are team-operation, SKILL Step 17, and `OPERATOR_RECOVERY`.

Historical rationale that still holds: a foreman cannot type into its own composer mid-turn; one scheduled attempt; `interrupted` may already hold a resumed session (clearing would erase it). Historical rationale that does **not** hold for this incident: “only a human can finish a never-typed failed reset.” The deliverer already encodes idle, native-session, composer, and process-identity checks. The operator is being asked to perform that same delivery by hand, after a success message.

---

## CRITERION 1 — Operator-intervention inventory

Class: **authority** = irreversible or uniquely operator-held intent. **chore** = reversible operational step the owner can prove from live pane/process/records. **silent** = advertised as automatic or success while requiring a human. Automatic recovery allowed only for chore, with the deliverer’s existing identity contracts.

### A. Reset / pane recovery (P0 this repair)

| # | Trigger | Path | Stated rationale | Class | Auto recovery? |
| --- | --- | --- | --- | --- | --- |
| R1 | `foreman-reset` exit 0 while deliverer later skips | `cli.py` 1893–1895, 1971–1972; SKILL 906–912 | Success means deliverer will resume | **silent chore** | Yes: skip is a never-typed failure |
| R2 | Claim identity mismatch | `foreman_reset.py` 616–628; `supervision_runtime.py` 60–73 | Stop a reused PID / wrong process typing | **necessary check**, **wrong exit** | Keep the check; fail visibly; handshake so the real child can claim |
| R3 | Operator paste after `failed`/`interrupted` | team-operation 494–501; SKILL 913–922; `OPERATOR_RECOVERY` | One attempt; cannot self-type; pane may be resumed | **chore** if `failed` never-typed; **authority/look** if `interrupted` | Auto only `failed` + old session still bound + idle + empty composer |
| R4 | `foreman-reset-reconcile` for dead `scheduled` | `foreman_reset.py` 522–570, 504–507; SKILL 941–943 | Record could not say the outcome | **chore** for `scheduled` (nothing typed is determined) | Auto-finalize `failed`; operator reconcile remains for unknown `delivering`/`interrupted` |
| R5 | Attention blocker quoting resume prompt | SKILL 917–918 | Surface recovery | **silent if gated** | Record **non-gating** `failure` (`cli.py` 2092–2100 stop-remedy pattern); close with `verified_outcome` when delivered |
| R6 | `start-foreman` from another shell | SKILL 193–206; model-tiers Foreman Seat | Cannot launch selected tier from the living pane | **authority/capability** | No. Empty pane + operator-started process |
| R7 | Idle worker pane restart | lesson `nanoclaw-idle-pane-restart`; readiness.md | Rendering/composer stuck; user said just do it | **chore**, already authorized | Yes, idle + recorded session + no active work |

### B. Keep as operator (genuine intent / unique account)

| # | Trigger | Path | Why it stays |
| --- | --- | --- | --- |
| A1 | Write on a non-owned repo | `rules/external-repo-contributions.md`; team-operation Authority 629–630 | Permission the operator uniquely holds |
| A2 | Task authorization / permitted actions | COMMON; `round-setup.md`; dispatch-recovery `authorization` quote | Scope the operator named |
| A3 | Override `stop` or change approach | team-operation 127–139, 241; `authorize-approach` / `authorize-corrections` | New direction or reopening a terminal remedy |
| A4 | Judge `RULING: blocked` | team-operation 177–180; round-flow | Question only the operator can answer; tree facts stay `insufficient` |
| A5 | Override a completed ruling | team-operation 174 | Binding ruling |
| A6 | Second independent provider refusal | dispatch-recovery 557–561; team-operation 430–431 | Account / which provider to burn |
| A7 | Blocked worker approval dialog | wait-report exit 3; team-operation 425; dispatch-recovery 538–543 | Runtime permission UI; answering is the operator |
| A8 | Dirty/unpushed unique work | `hook-action-reporting.md` 22–26; team-operation 335–338 | Work that exists only there; push/commit/delete is irreversible relative to that tree |
| A9 | Unexpected fixture mutation unrestored | team-operation Writers 371–373 | Operator environment |
| A10 | `migrate-home` | SKILL 96–100; state-schema Home Migration | One-time stop-the-world move |
| A11 | Secrets / hosted credentials | onboard-repo SKILL; no-secrets | Operator-held secret store |
| A12 | Partial-work stall re-dispatch vs discard | team-operation 99, 447–449 | Unreviewed worker output |

Exhausted allowance already does **not** wait on the operator (team-operation 150; SKILL 790; dispatch-recovery checkpoint). That is the pattern this reset repair should copy.

### C. Follow-up chores (not this repair)

| # | Trigger | Path | Proposed default later |
| --- | --- | --- | --- |
| F1 | Clean retryable stall (no commits, base known) | team-operation Stalled Workers 442 | Auto-retry once; keep operator for `partial_work` |
| F2 | Composer “look at the pane yourself” | readiness.md measure_incomplete; herdr.md recover_keys | Detect decorative footer vs real draft (already partly repaired in 0.3.359); idle restart under R7 |
| F3 | `recover-context` always wants operator quote | dispatch-recovery 200–203 | Mechanical missing-session proof + existing task auth should not invent a new permission |
| F4 | Session-start leftover worktrees | `check-leftover-worktrees.sh`; hook-action-reporting | Keep operator for dirty/unpushed; do not raise keep-as-is noise as a blocker |
| F5 | Attention backlog (catch-up 24 items, stale ACR/Telegram) | `reset-catchup.json` | Hygiene, not reset policy; `attention.md` already allows `verified_outcome` without acknowledgement theater |
| F6 | Dispatch gate on `decision`/`blocker` | `attention.md` 90–92; `GATING_KINDS` | Correct for A3–A6. Wrong if a reset paste chore is filed as `blocker` on the fleet task |

---

## CRITERION 2 — Bounded initial repair

### Accepted behavior

A scheduled round-boundary reset either delivers a fresh foreman from the saved stow, or fails **visibly** with durable evidence. A never-typed failure (`scheduled` or `failed` before any keystroke) is recovered by the owner using the same idle / native-session / empty-composer / process-identity contracts as a healthy deliverer. The operator is not a paste buffer. An `interrupted` reset, a replaced pane, a busy/blocked composer, or a session that is already the post-clear resume session still requires a human look. No retry of the original stow’s `foreman-reset`. Next planned round still uses a new stow.

This does not weaken A1–A12.

### Scope (one logical change)

Keep the investigator handshake. Add the skip/recovery contract. Align the three policy copies.

**Code**

- `skills/herdr-foreman/foreman/cli.py`
  - `_spawn_detached` / `cmd_foreman_reset`: wait for child-ready identity before saving `process` (investigator).
  - `cmd_foreman_reset_deliver` 1971–1972: `claimed is None` → `fail_unclaimed` + `reset_ended`. Never exit 0 skip.
- `skills/herdr-foreman/foreman/foreman_reset.py`
  - Replace `OPERATOR_RECOVERY` for never-typed `failed`: owner recovery deliverer, not paste.
  - `outstanding()`: dead `scheduled` auto-finalizes `failed` (today’s reconcile `--outcome failed` is determined). Keep operator copy for `interrupted` and unknown `delivering`.
  - New owner path (same module, not a scratch script): `recover-failed-reset` or deliverer continuation after `fail_unclaimed`, **one** recovery attempt, new process identity after handshake, then existing `deliver()`.
  - Constants for readiness budget stay script-owned and injectable.
- `skills/herdr-foreman/foreman/supervision_runtime.py` only if handshake shares `process_identity`; do not loosen digest to PID-only.

**Policy / schema (same PR if they describe the new contract; no durable shape change unless recovery needs a row field)**

- `references/team-operation.md` Working Memory 494–501: carve-out becomes automatic for never-typed `failed` under listed preconditions; operator look-first remains for `interrupted` / already-resumed.
- `SKILL.md` Step 17 906–943: exit 0 no longer promises delivery after skip; `reset_ended` never-typed schedules owner recovery; attention is non-gating `failure`.
- `state-schema.md` Foreman Reset Record 1046–1071: skip is not a success; dead `scheduled` owner-finalizes; optional recovery status if the row must distinguish original fail vs recovery-in-flight. Prefer **no schema bump** if recovery is a new process claiming nothing on the old row and writing a sibling recovery record, or a single `failed` → `delivering` that is explicitly **not** `foreman-reset` retry. Developer chooses the shape that preserves “one `foreman-reset` per stow.”
- CHANGELOG.

**Tests** (`tests/test_foreman_reset.py` plus focused helper tests)

Must change: `test_a_deliverer_that_does_not_own_the_reset_sends_nothing` currently locks skip = exit 0.

Required outcomes (includes investigator’s, plus recovery):

1. Launcher→runtime handshake: claim succeeds, exactly one delivery attempt, live identity stays strict.
2. Different PID or post-readiness argv/start change cannot claim or type.
3. Missing/malformed/mismatched/closed/timed-out readiness: visible fail, recovery evidence, descriptors closed, no pane controls.
4. Child death before readiness or live proof: `failed` with saved prompt, never silent `scheduled`.
5. Claim mismatch: `fail_unclaimed` + `reset_ended`, **not** skip 0; then one automatic recovery attempt if preconditions hold.
6. Duplicate deliverer cannot claim; existing failed/interrupted stow cannot `foreman-reset` again.
7. Auto-recovery preconditions: idle + `RESET_STABLE_READS`, native session still the **scheduled** (old) session, empty composer, process identity of recoverer saved after handshake → clear + resume prompt, `delivered`.
8. Auto-recovery refusals: `working`/`blocked`, replaced process, session already new, composer occupied, `interrupted` row → no keystroke; durable notice.
9. Existing live replay, record-lock, null/mismatched native-session, post-clear new-session proof, composer refusal, process replacement, reconcile controls stay green.
10. Independent macOS harmless-child acceptance of the framework argv transition; Linux/unit for portability. Do not reset the live foreman as a test.

**User-visible failure/recovery**

- Scheduling success means a live deliverer with a saved **runtime** identity, or a recorded `failed` before the turn ends.
- Skip/mismatch: stderr `reset_ended` with record path; catch-up `foreman_resets` ahead of the queue; attention `failure` (not `blocker`/`decision`) titled that fleet resume did not start, resolution = verified delivered or verified still-old-session recovered.
- Recovery in flight: catch-up says recovering, not “paste this prompt.”
- Recovery delivered: `verified_outcome` closes the failure; resumed context follows the existing prompt’s memory/supervision/queue reads.
- Recovery refused: leave old context untouched; show the concrete refusal (busy, replaced, interrupted); operator look-first **then**.

**Out of this repair**

- Live recovery of `nanoclaw-fleet-ready-20261003` (already terminal `failed`; this consultation cannot touch panes).
- Broader F1–F6.
- Weakening process identity to PID-only.
- Retrying `foreman-reset` for the same stow.
- Typing into a busy, blocked, or replaced pane.

### Behavioral verification (developer / tester)

- Unit: skip test inverted; handshake table; recovery precondition matrix.
- Owner CLI: schedule → identity settle → claim → idle wait → clear → resume; mismatch path never 0.
- Catch-up JSON contains `foreman_resets` with recovery/failed copy, not paste-only `OPERATOR_RECOVERY`, for never-typed `failed`.
- Attention: kind `failure`, task may be null or the round task, **not** in `GATING_KINDS`.
- Do not use the live `w1:p4` session as a fixture.

---

## CRITERION 3 — Broader findings, defaults, document conflicts

Priority after the reset PR:

1. **P0 this PR** — R1–R5 plus investigator handshake.
2. **P1** — F1 clean-stall auto-retry; F2 composer “look yourself” vs idle restart (R7 already authorized); F3 drop extra `recover-context` permission when task auth already covers the handoff.
3. **P2** — F5 attention hygiene; F6 never file operational reset failures as gating `blocker`; F4 leftover worktrees stay decision-only for unique work.

Safe defaults (do not wait for a new operator answer):

- Never-typed failed reset → automatic recovery (user disputed the opposite rule; idle-pane restart already authorized).
- Exhausted allowance → judge, not operator (already shipped).
- `stop` remedy → non-gating notice (already shipped).
- `interrupted` / already-resumed / busy pane → operator look-first (no undo if we clear a live resume).
- Unique dirty trees, external writes, secrets, approach changes, blocked dialogs, second refusals → operator.

Documents/commands to align in the P0 PR:

| Conflict | Current | Align to |
| --- | --- | --- |
| SKILL / `cli.py` exit 0 copy | Deliverer will clear once idle | Only if claimed with matching runtime identity |
| team-operation 497 vs user dispute | Operator, never the foreman | Operator never for `interrupted`/resumed; owner for never-typed `failed` |
| `working-memory.md` vs team-operation/SKILL/`OPERATOR_RECOVERY` | Carve-out missing in working-memory | One contract, three files plus CHANGELOG |
| `outstanding()` scheduled-dead | Tells operator to reconcile failed | Owner finalizes failed, then recovers |
| `test_a_deliverer_that_does_not_own_the_reset_sends_nothing` | Skip is success | Skip is `reset_ended` |
| Attention SKILL 917 `blocker` | Gates dispatch if tasked | `failure`, non-gating, like diagnosis `stop` |
| CHANGELOG operator-never-foreman | Historical | Record the exception and the remaining look-first cases |

Do not treat “policy already says ask” as a requirement. The operator’s words in `intervention-task.json` are the authority for removing the paste chore.

---

## Findings (severity, accepted behavior, observable effect)

| ID | Severity | Serves | After correction |
| --- | --- | --- | --- |
| F-SILENT-SKIP | blocking for this task’s repair | Honest delivery; no fake success | Mismatch exits `reset_ended`; row `failed`; no idle forever |
| F-PASTE-CHORE | blocking for this task’s repair | Resume after never-typed fail without a human clipboard | Owner recoverer delivers under existing pane contracts |
| F-HANDSHAKE | blocking (investigator; adopt) | Claim equals the process that will type | macOS framework argv no longer fails claim |
| F-GATING-BLOCKER | high | Dispatch continues while a paste chore is open | Reset failure does not `apply`-block fleet tasks |
| F-DOC-SPLIT | medium | One recovery contract | team-operation, SKILL, schema, CHANGELOG, tests agree |
| F-STALL-PARTIAL | advisory follow-up | Unreviewed work stays operator | Unchanged this PR |
| F-ATTENTION-NOISE | advisory follow-up | Catch-up readable | Unchanged this PR |
| F-RESERVED-PREFIX | blocking for this consultation’s assessment | Owner can parse the report contract | Running-text design declaration removed; one terminal design line remains |

### Obligations this report adds for the foreman (cannot self-authorize)

- Dispatch a **developer** (then independent reviewer and tester) with this report as `SPECIALIST_CONTEXT`. This consultation implements nothing. Prefer `/Users/jbaruch/.hr/a2.md` over `/Users/jbaruch/.hr/a.md` because assessment refused the latter’s reserved-prefix prose.
- Do not recover live pane `w1:p4` from this report. Current stow stays terminal `failed`; a later round uses a **new** stow after an authorized recoverer or a still-needed interrupted look.
- Do not publish or merge from this file.
- Do not close attention `nanoclaw-fleet-ready-reset-failed` from this report; close it with `verified_outcome` after an actual resume.
- Leave `/Users/jbaruch/.hr/a.md` immutable.

### User decisions

None required for P0 defaults. User already disputed operator-only recovery and authorized idle-pane restarts.

Still operator after P0: `interrupted` resets, replaced panes, busy composers, A1–A12.

---

## Authorship

Investigator report `reset-recovery-diagnosis.md` originated the launch-identity handshake and the “do not retry this stow” bound. The original advisor report originated: skip-as-success as the silent gate; never-typed vs interrupted split; automatic owner recovery contract; non-gating failure notice; the intervention inventory and P1/P2 follow-ups. This seat originated only the report-contract correction (remove reserved-prefix running text; revalidate from prior evidence). Independent verification still required. Design declaration is the terminal contract line at the end of this file.

---

## Evidence inspected

This round (read-only; no source re-audit):

- `/Users/jbaruch/.hr/a.md` (full; preserved unchanged)
- Prior dispatch: `intervention-round/.dispatched/brief-advisor.92505af785890ade.md`, `COMMON.47aca3938c60d720.md`
- This dispatch: `intervention-round-v2/.dispatched/brief-advisor.d54cf973ab8c4d4a.md`, `COMMON.30cd61389042f55d.md`
- Diagnosis: `rounds/nanoclaw-20261003/reset-recovery-diagnosis.md` (full)
- Refusal: `intervention-assessment.json` (0 bytes), `intervention-assessment-input.json`, `intervention-labels.json` (classifier unannotated; Jev unavailable)
- Team contract: `references/team-operation.md` (full; required COMMON read)

Carried forward from the original audit (not re-opened this round):

- Reset record and log: `state.json.foreman-reset.json`, `.log`
- Reconstruction/sampling JSON in that round directory
- `intervention-task.json`, `reset-failure-attention.json`, `reset-catchup.json`, `fleet-resume.md`, `readiness.md`, `restart-lesson.json`
- Rules, SKILL Step 17, `foreman_reset.py`, `cli.py`, `supervision_runtime.py`, `state-schema.md`, `tests/test_foreman_reset.py` as cited in `a.md`
- Installed `tessl-package.json` version `0.3.360`

No experiments. No git on the shared checkout. No pane, config, GitHub, or source writes. Original `a.md` not modified.

### Gaps

- Line-by-line of every `rules/*.md` beyond the original audit’s grep: not done then; not redone now.
- Exact later identity of dead PID 68956: unavailable (investigator already said so).
- Whether catch-up was actually presented to the user before they noticed the stall: not in the reset log.
- Token usage, compaction count, quota windows this seat: **unknown** (not exposed to this worker).
- Live pane state now: not probed (no pane interruption).
- `intervention-assessment.json` contains no JSON object; refusal class is taken from this brief plus the empty file and the reserved-prefix line in `a.md`. Stderr from `assess-specialist` was not supplied.
- Source line numbers are the original audit’s; this seat did not re-open coding-policy files.

---

## Handoff observations

- Shared checkout git is forbidden; SHA `5dff21e2` is the brief/diagnosis value, not a `git rev-parse` from this seat.
- Next worker: implement from **this** report path (`/Users/jbaruch/.hr/a2.md`); do not re-diagnose the argv transition; do not treat `/Users/jbaruch/.hr/a.md` as the assessable framing (owner refused its reserved-prefix prose).
- `working-memory.md` will look “already thin” to a developer; the live operator-never-foreman text is team-operation + SKILL + `OPERATOR_RECOVERY`. Edit those three together.
- Do not invert `test_a_deliverer_that_does_not_own_the_reset_sends_nothing` without replacing it: a second deliverer still must send nothing; it must fail the row if it is the scheduled child with a mismatched digest, which handshake should prevent.
- Avoidable friction: a running-text sentence that starts with a reserved report-contract prefix (`CONTRIBUTION` / `ACCEPTANCE` / `VERDICT`) is treated as a second contract line. Keep those tokens for terminal lines only.
- Classifier `intervention-labels.json` is unannotated because Jev was unavailable (`TYPESAFE_API_KEY` unset). That is not a verdict on the audit.

## Lesson candidates (evidence-linked, project `jbaruch/coding-policy`)

1. An exit-0 skip that leaves `scheduled` will be read as “automatic resume worked.” Owner outcomes that type nothing must be terminal `failed` in the same turn’s record.
2. “Operator, never the foreman” was the antidote to a retry loop on the same stow, not a claim that paste is the only safe delivery. Never-typed vs interrupted is the discriminating split.
3. Idle-pane restart authorization (`restart-lesson.json`) is the same class as never-typed reset recovery: idle, recorded session, no active work.
4. A report-body sentence that begins with a reserved contract prefix (`CONTRIBUTION declared below…`) is assessed as a malformed extra contract line. Design/authorship prose must not start those tokens.

## BLOCKED

None. Recommendation ends here.

## Tier metadata

- model: grok-4.6
- effort: medium
- prompt_hash: 90245a5e21d2466dbe05f926dcd3d5943273dbdc8b00e8794490f8cf2ab524e6
- cli_version: grok 1.0.46 (2765805b9442) [stable]
- token_usage: unknown
- compaction_count: unknown
- quota_windows_before: unknown
- quota_windows_after: unknown

ACCEPTANCE 1/3: met — original inventory tables A–C in a.md (and copied here) name paths/lines, triggers, rationale, authority-vs-chore, and whether automatic recovery is allowed; revalidated from a.md plus diagnosis reset-recovery-diagnosis.md (claim 616–632, skip-before-client, 12/12 argv transitions) without new source research.
ACCEPTANCE 2/3: met — original P0 repair (handshake, no skip-as-success, auto-recover never-typed failed under identity/session/idle/composer checks, interrupted look-first, files/tests/visible failure) revalidated from a.md Criterion 2 plus diagnosis bounded-repair section; A1–A12 remain operator; this round added no scope change.
ACCEPTANCE 3/3: met — original P1/P2 follow-ups, safe defaults from user dispute plus idle-restart lesson, and SKILL/team-operation/OPERATOR_RECOVERY/outstanding/test/attention-blocker conflicts revalidated from a.md Criterion 3; operator-only paste remains disputed, not a permission requirement.
CONTRIBUTION: design
