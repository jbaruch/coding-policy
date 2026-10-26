# Combined installed-context acceptance (#642)

This is an acceptance record for the coordinated coding-policy, nanoclaw-host
and nanoclaw-admin task-contract extraction. It adds no session-start accounting
phase. Live NanoClaw registrations, runtime files, owner state and cadences were
not changed.

## Representative installation

Use an owned disposable directory and real Tessl installs for Claude Code.
Include coding-policy, nanoclaw-host, nanoclaw-admin, herdr-policy and
good-oss-citizen together. Keep the same project instruction entry point and
normal user instruction imports in both fixtures. Disable hooks for the probe;
never copy credentials, private transcripts or operational state.

The matched baseline used coding-policy 0.5.8, nanoclaw-host 0.1.89,
nanoclaw-admin 0.1.570, herdr-policy 0.16.2 and good-oss-citizen 1.1.12.
The candidate fixture installed the changed first three plugins from their
isolated source checkouts with `tessl install --agent claude-code --yes file:<path>`.
The other plugin trees and generated root instruction index stayed present.
The final refresh includes the two host authority rules added during review. Candidate manifest versions alone are not publication evidence.

## Native results

Observed with Claude Code 2.1.310, Haiku 5.5, low effort:

| Observation | Matched baseline | Candidate |
| --- | --- | --- |
| Startup warning | 90 files, 258.9k characters, over 150.0k | No warning |
| Native `/memory` imported-file inventory | 90 instruction files | Same 90 paths; detail references absent from startup |
| Native `/context` memory estimate | 97.2k tokens | 47.5k tokens |
| One-turn READY probe input + cache tokens | 107,456 | 57,478 |
| READY probe output | 4 tokens, success | 4 tokens, success |

The candidate's raw decoded import-graph total is 134,976 characters, including
frontmatter. This is a conservative upper bound with 15,024 characters of
headroom. Claude's native warning strips rule metadata; its count differs from
raw file sizes. The bound was checked against the actual native inventory,
not coding-policy's source rule directory alone.

The one-turn probes used the same prompt and flags, normal instruction discovery,
`--no-session-persistence`, disabled hooks, no browser, no MCP services and no
built-in tools. Each returned READY on the configured subscription. The reported
input sum includes input, cache creation and cache read counters. Cache placement,
cost and timing differ between calls; this record makes no remaining-quota,
latency or instruction-omission claim.

## Required policy loading

A separate read-only native probe selected a synthetic task and stopped before
provider access. Its tool trace read the applicable installed rule router, then
that router's full co-shipped reference. It loaded no unrelated detail reference.
The run succeeded without a provider operation or a write tool.

All 47 extracted original rule bodies were compared byte-for-byte with their
binding references: 13 coding-policy, 19 host and 15 admin. The compact startup
rules preserve the applicable role, ownership, credential, approval and failure
boundaries. Missing references stop the affected action. References load by
explicit reads; `@` imports would restore the startup load and are forbidden.

## Repeating acceptance

Rebuild fresh disposable installations from the exact candidate artifacts.
Observe native startup, `/memory` and `/context` before any task. Run a
no-action READY request to verify the provider path independently. Run a separate
read-only task-selection case and inspect its tool trace for the required full
reference read. Confirm both distribution manifests still package each target.
Retain aggregate results only in public evidence; keep private source content
and raw local probe logs out of public reports. Stop each disposable session and
remove its owned fixture when the release verification is complete.
