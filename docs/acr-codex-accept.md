# Central ACR Codex acceptance

`.github/workflows/acr-codex-accept.yml` is a manual, separate acceptance lane
in `jbaruch/coding-policy`. It uses that repository's existing
`CODEX_AUTH_JSON` subscription credential. It does not change fleet review.
The Python standard-library helper is `.github/codex-accept/contract.py`;
the workflow uses JSON syntax, a YAML subset, so its complete job/step structure
can be tested without adding a YAML dependency. Existing Renovate GitHub
Actions discovery renews the action version pins.

**ACR integration and live acceptance are pending.** Registration of this
workflow does not establish runtime compatibility, successful conversion,
publication, or consumption. An ACR candidate without the exact proof tests
fails before real authentication. The central tests use synthetic repositories
and receipt fixtures; they prove central validation behavior only.

## Dispatch and trust

A central maintainer supplies a reviewed exact ACR commit with completed code
gates. SHA syntax establishes identity, not that an executable is trustworthy.
The only executable source is `jbaruch/agentic-context-registry`; central and
candidate checkouts use their exact SHAs with credential persistence disabled.
There are exactly seven inputs:

| Input | Contract |
| --- | --- |
| `phase` | Required `convert` or `consume`; no implicit fallback. |
| `acr-sha` | Required lowercase 40-hex reviewed ACR commit. |
| `codex-version` | Convert requires a release in the candidate installer digest table; default `0.154.0`. Consume ignores it and installs no Codex. |
| `producer-run-id` | Consume requires the exact successful central run; empty for convert. |
| `producer-run-attempt` | Consume requires the exact positive attempt; empty for convert. |
| `goc-source` | Consume: `github:jbaruch/acr-156-goc-validation@v1.1.11`; empty for convert. |
| `ffa-source` | Consume: `github:jbaruch/acr-156-ffa-validation@v0.9.38`; empty for convert. |

Preflight checks inputs before candidate execution and checks the candidate
installer table before any auth is seeded. Shell scripts receive inputs through
quoted environment variables, never expressions inserted into shell code.
Concurrency is per phase/candidate with cancellation disabled. Convert has a
150-minute job limit and 140-minute live-test limit; consume has 30 and 25 minutes.

The two full upstream repositories are fixed:

| Fixture | Untouched commit | Package argument | Publication target/version |
| --- | --- | --- | --- |
| `tesslio/good-oss-citizen` | `f21fda887815af815979a4fea43a66eb5174ee3e` | `plugins/good-oss-citizen` | `jbaruch/acr-156-goc-validation`, `1.1.11` |
| `jbaruch/frequent-flyer-advocate` | `142babbb1e2bebc798eb42128ac2466f21b5131d` | `.` | `jbaruch/acr-156-ffa-validation`, `0.9.38` |

Fixtures are full clones in fresh `$RUNNER_TEMP/acr-accept/fixtures/{goc,ffa}`.
Pins are acceptance fixtures, not moving dependencies; a fixture update needs a
reviewed contract change. Codex archive pins come from the exact candidate's
`.github/scripts/install-codex.sh`, renewed monthly with that runtime matrix.
The actual archive, ELF binary digest and native version are recorded. The
upstream FFA gate runs with Python 3.12 and its own hash-locked Pyright 1.1.411;
central diagnostics retain their separate 1.1.414 pin.

## Credential ownership and pre-auth proof

The contract identifier is `acr-credential-boundary/v1`. Central owns the seed
and original-suite token. ACR owns the initial and observed refreshed values
inside each migration, including their union across scopes and repairs. Those
refreshed values never travel to central, in a file, digest, length, environment,
CLI argument or cross-process channel. Central does not claim to scan for them.
Coverage excludes unseen intermediate rotations and arbitrary encodings.

Before seeding real auth, `proof-run` verifies the clean exact candidate, makes
fresh private HOME/CODEX_HOME/state/TMPDIR, removes live/auth/token variables,
and runs this command uncached from the candidate checkout:

```sh
go test -race -count=1 -json -timeout 10m -run '^TestCodexCredentialBoundary(Runtime|CLI)$' ./internal/producerconvert ./cmd/acr
```

`proof-run` captures output privately, retains actual exit status, rechecks
checkout identity/cleanliness, then invokes the same validator exposed by:

```sh
python3 .github/codex-accept/contract.py prove-runtime --acr-root "$ACR_ROOT" --events "$PRIVATE_EVENTS" --exit-code "$GO_EXIT" --output "$PROOF"
```

The wrapper creates the event path exclusively; the proof output must also be
fresh. The parser requires package start/pass, test run/pass for both roots and
all intermediate/leaf names, and no failure, skip, duplicate completion, foreign
package, unexpected leaf, missing terminal package result or zero-test success.
Timing is irrelevant. Arbitrary Go `Output` text is never copied into proof.

The required runtime package is
`github.com/jbaruch/agentic-context-registry/internal/producerconvert`.
`TestCodexCredentialBoundaryRuntime` must execute each suffix under both
`darwin` and `linux`: `seed_proposal`, `rotate_proposal`, `rotate_patch`,
`rotate_stream_success`, `rotate_stream_failure`, `auth_uninspectable`,
`cancel_cleanup`, `raw_integrity`, `clean_rotation`. There are 18 runtime leaves.
The CLI package is `github.com/jbaruch/agentic-context-registry/cmd/acr`;
`TestCodexCredentialBoundaryCLI` has `rotated_refusal` and `rotated_clean`.
Variants within a leaf use loops rather than additional named subtests.

These are ACR integration obligations, not tests the central helper supplies.
They must exercise actual runtime/planning/application behavior and the built
CLI with a deterministic rotating native fixture on the real Linux boundary.
They must cover seed/rotated proposals, patch assembly and cross-scope unions,
sanitation of successful diagnostics and failed streams, uninspectable auth,
synchronized cancellation, raw protocol integrity, and clean-rotation success.
The CLI positive control must inspect the generated Git closure and evidence
with its in-memory synthetic oracle after private homes have been removed.
Credential-bearing proposals refuse before mutation and are not repair feedback;
clean proposals with sanitized diagnostic echoes may succeed. No always-refuse
implementation satisfies this proof.

`evidence/credential-boundary.json` is central-owned. All keys are exclusive:
`schema_version:1`, `contract`, `result:"passed"`, `acr_sha`, `central_sha`,
`run_id`, `run_attempt`, `host:"linux-amd64"`, `command` (the argv above),
`exit_code:0`, `events_sha256`, `tests` (the complete unique leaf set as
`{package,name,result:"passed"}`), and `coverage`:

```json
{"runtime":["initial","observed_refreshed"],"central_scanner":["initial","suite"],"refreshed_verification":"runtime-composition"}
```

The trusted context variables are `ACR_ACCEPT_ACR_SHA`,
`ACR_ACCEPT_CENTRAL_SHA`, `ACR_ACCEPT_RUN_ID`, `ACR_ACCEPT_RUN_ATTEMPT`,
`ACR_ACCEPT_HOST`. They are orchestration metadata, not dispatch inputs.
Seal binds to this convert run. Verify binds to the authenticated **producer**
run, never to a later consume run. The private-event digest is an identifier,
not an independent attestation of an arbitrary executable.

Only after proof passes does `seed` write valid subscription JSON under the
private run root, with directory/file modes 0700/0600, and invoke the existing
central masking helper. There is no API-key fallback. `CODEX_AUTH_JSON` is scoped
to that single step. The suite token is scoped to conversion and sealing.
The future ACR harness must capture it before journey setup, pass it solely to
original-test children, clear `GITHUB_TOKEN` there, and exclude both tokens from
Codex children. Missing or `journey-fixture-token` values refuse centrally.

## ACR producer evidence interface

The candidate's `TestCodexLiveUpstreamConversion` must run both `GOC` and `FFA`
subtests. The workflow sets `ACR_CODEX_LIVE=1`, `ACR_CODEX_LIVE_REQUIRED=1`,
`ACR_CODEX_LIVE_EVIDENCE`, and both `ACR_CODEX_LIVE_<KEY>`, `_SHA`, `_REPOSITORY`
values. Missing/skipped fixtures fail; no synthetic substitute is permitted.
Temporary runtime state belongs under the supplied TMPDIR/state root.

The harness writes one successful fixture projection directly to
`$ACR_CODEX_LIVE_EVIDENCE/{goc,ffa}/` after all checks finish. This is the new
v1 handoff; the older `linux-amd64/upstream-*/attempt-*` logs and
`converted-root.txt` are not accepted as success or as repository pointers.
Extra raw diagnostic files in the runtime directory are never recursively
exported. The fixed export projection is:

- `fixture-result.json`, `deterministic-dry-run.json`, `dry-run.json`, `apply.json`,
  `validate.json`, `rerun.json`.
- `baseline-inventory.json`, `converted-inventory.json`.
- `baseline-test-N.log`, `converted-test-N.log`: N=1..3 for GOC, N=1 for FFA.

The fixture-result fields are exclusive: `schema_version:1`, `key` (`GOC` or
`FFA`), `result:"passed"`, `acr_sha`, `upstream_sha`, `producer_sha`, `tree_sha`,
`repository`, `version`, `source_root`, `operations`, `commands`, `inventories`, `checks`,
`credential_boundary`. Inventory files are sorted arrays of
`{path,mode,sha256}` over the entire Git tree. Modes use Git's six-digit strings;
hashes cover decoded blob bytes. `inventories` has `baseline` and `converted`,
each `{path:"evidence/<key>/<phase>-inventory.json",sha256}`. The helper compares
them to actual Git objects and preserves original tests, `.github/scripts/`
and `.github/requirements.txt` bytes/modes across conversion. FFA's root
`pyrightconfig.json` is also an original gate input: its bytes and mode must
remain unchanged. Publishing workflows remain conversion-owned.

`checks` has exactly `deterministic_refusal`, `source_preserved`,
`delta_validated`, `validate`, `rerun`, `clean`, all true. The harness sets them
only after actual deterministic exit-1 semantic refusal with unchanged source,
genuine dry-run preservation, genuine apply/delta validation, root
`acr validate --json`, provider-free inert rerun, and post-suite clean status.
Commit creation before a failing assertion does not produce this receipt.

`source_root` records the actual absolute orchestration-owned fixture root;
seal compares it to its fixed root instead of following a receipt pointer.
`operations` records deterministic-dry-run, dry-run, apply, validate and rerun in
that order. Each object is `{name,argv,exit_code,output,sha256}`. `argv` includes
the actual built executable's absolute path (basename `acr`), identical across
operations. Migrations use the package argument, `--acr-only --repository <target>
--json`, then `--dry-run`, `--agent codex --dry-run`, `--agent codex`, or
`--dry-run` respectively. Validation is `acr validate <source_root> --json`.
Only deterministic refusal exits 1; all others exit 0. Each fixed JSON evidence
file carries the successful stdout envelope or deterministic-refusal stderr
envelope. Its hash must match. The refusal code is
`unsupported_semantic_conversion`; rerun reports `current:true` without agent
runs. This preserves actual command/result evidence in addition to check flags.

`commands` is the ordered baseline commands followed by converted commands.
Each object is `{phase,argv,exit_code:0,output,sha256,counts}`; `output` names the
corresponding fixed `evidence/<key>/<phase>-test-N.log`. Original argv is:

```sh
python3 tests/test_contribution_declaration.py
python3 tests/test_install_gate_scaffold.py
python3 tests/test_github_sh_envelope.py --repo tesslio/good-oss-citizen --issue-number 13 --pr-number 12 --file-path README.md
bash .github/scripts/pre-publish-gate.sh
```

The first three are GOC. Their real summary formats must demonstrate at least
15 classification cases plus CLI checks, 16 scaffold checks, and 23 envelopes
plus the negative path. FFA must report its three `N/N passed` summaries (at least
19 lock, 114 tracker and 51 letter-fit tests; 184 combined), Pyright 1.1.411 with zero findings, and its final gate success.
`counts` contains the parsed summary total(s); converted totals cannot diminish.
Arbitrary PASS-line counts are not accepted. Original fixture API coordinates
stay upstream; publication-target coordinates do not replace them.

`dry-run.json`/`apply.json` are successful CLI JSON envelopes. The result must
have `wrote:false`/`true`, complete nonempty successful `agentRuns` with Codex
version/isolation/argv/request digest and started/completed turn evidence, and
`credentialBoundary:{contract,planChecked:true,applicationChecked:false|true,
reportSanitized:true}`. Each AgentRun boundary has exactly `contract`,
`authInspected:true`, `proposalChecked:true`, `reportSanitized:true`,
`isolatedHomeRemoved:true`, and boolean `refreshObserved`. Either observation
value is valid; rotation alone is not failure.

The fixture's `credential_boundary` is exactly `{contract,plan_checked:true,
runs:[...]}`. Each run is `{phase:"dry-run"|"apply",index:<zero-based>,boundary}`.
It must match every referenced CLI AgentRun in phase/index order, including
repairs and scopes. False/missing flags, omitted runs and defaulted booleans fail.
Only the ACR production guard/harness may author those observations.

## Sealing and portable producer identity

`seal --run-root ... --evidence ... --output ...` verifies both final receipts,
proof, original suites and inventories, clean producer HEAD/tree and upstream
ancestry. It bundles each complete reachable Git closure under the single ref
`refs/heads/acr-accept-{goc,ffa}`, then imports into fresh bare repositories and
verifies refs, closure membership, commits, trees and ancestry again. Same tree
with a different producer commit fails. No regeneration or tar-only handoff.

The artifact name is `acr-accept-convert-<acr-sha>-<run-id>-<attempt>`, retained
14 days. Its exact members are `manifest.json`, `goc.bundle`, `ffa.bundle`, the
fixed fixture projection above, and `evidence/credential-boundary.json`.
Manifest v1 contains `schema_version`, `phase:"convert"`, `result:"passed"`,
central/candidate/run/attempt IDs, `run_url`, `platform`,
`codex:{version,archive_sha256,binary_sha256}`, both fixture identities with
`bundle`, `bundle_ref`, `bundle_sha256`, `receipt`, a sorted exact
`files:[{path,size,sha256}]` inventory excluding manifest itself, and
`credential_boundary:{contract,proof:"evidence/credential-boundary.json"}`.

Bounds are 1,000 files, 8 MiB per text member, 64 MiB total text, 128 MiB per
bundle, 100,000 Git objects, 512 MiB combined decoded objects. Duplicate JSON
keys, unknown/missing schema fields, unexpected archive members, unsafe paths,
symlinks, extra refs/objects and hash/size differences refuse. Bounds never
truncate evidence into success. A legitimate oversized fixture needs a focused
contract update after its measured size is reported.

The scanner requires regular readable valid seed auth with nonempty initial
values and the actual suite token. It literal-matches those values against all
decoded Git objects and export text. Compressed bundle scanning alone is
insufficient. Git objects are never redacted. Existing central text scanning is
also required, with file-presence checks. Scanner failure produces no export.

An always-run cleanup removes seed, private proof/live output and runtime
state. Upload requires successful sealing **and** successful cleanup and overall
job success. Failure artifacts are not uploaded. Hard runner termination relies
on hosted-runner disposal; shell cleanup is not claimed to survive it. Immutable
artifact ID and upload digest are written to the workflow summary.

## Producer provenance, publication and consumption

`download` and `verify` authenticate the exact producer run/attempt through the
central GitHub API using existing Actions-read access. They require this workflow
ID/path, dispatch event, completed successful run and convert job, central head
SHA, a unique unexpired exact-named artifact, its run/head linkage and digest.
Downloads validate the digest and all ZIP members before extraction; redirected
artifact storage receives no GitHub token. Both `verify` and standalone `consume`
fetch that authenticated archive again and compare every supplied file's exact
bytes, including `manifest.json`, against the verified download. No local hash
file or saved success receipt substitutes for the API/archive check. Consumption
passes the private authenticated snapshot to the candidate, retaining it until
the consumer exits; later changes to the caller's directory cannot change that
input. Verification imports and checks the authenticated producer bundles before
any install. Operator-entered expected SHAs alone are not provenance.

For local publication, use existing Actions-read authentication in `GH_TOKEN`:

```sh
python3 .github/codex-accept/contract.py download --acr-sha "$ACR_SHA" --run-id "$PRODUCER_RUN" --run-attempt "$PRODUCER_ATTEMPT" --artifact "$DOWNLOAD"
python3 .github/codex-accept/contract.py verify --acr-sha "$ACR_SHA" --run-id "$PRODUCER_RUN" --run-attempt "$PRODUCER_ATTEMPT" --artifact "$DOWNLOAD"
```

`$DOWNLOAD` must be fresh for download. Verification requires network access and
an unexpired remote artifact; there is no offline local-directory certification.
It leaves the directory unchanged and returns success only for byte-identical
content. Import/publish from that verified directory without modifying it. A
self-consistent new commit or replacement bundle requires its own producer run;
rewriting all local hashes cannot attach it to a previous successful run. The
internal `verify_artifact` function checks consistency only; callers must use the
authenticated CLI interface for publication. Hosted consume retains its explicit
download step and deliberately repeats authentication before candidate execution.

A later authorized publisher uses `download`/`verify`, imports those exact
producer objects, and publishes each once under the publication runbook. For
manual publishing, disable automatic Actions on the fresh validation repositories
before pushing their generated workflow-bearing commits/tags. Otherwise observe
the generated publisher and do not race it with a manual publish. Preserve the
producer commit and version tag; dry-run publish precedes the one real publish.
Record independently retrieved release metadata/archive/checksums. Repository
creation/settings/push/release are not actions this workflow performs.

The consume job installs no Codex and has no subscription credential. It verifies
the producer before invoking `TestCodexLivePublishedConsumption` with
`ACR_CODEX_CONSUME_REQUIRED=1`, `_MANIFEST`, `_EVIDENCE`, `_GOC_SOURCE`,
`_FFA_SOURCE`. The test must execute `GOC` and `FFA` subtests without skips,
using the built candidate and actual public GitHub endpoints, not mocks or local
fallbacks. Each fixture uses fresh roots/state, initializes claude-code/codex/cursor
with freshness none, and installs, realizes and checks. Its actual lock must
contain exactly the expected release dependency, producer commit, live release
ID/tag and metadata contentHash. A separate fresh SHA-pinned install must resolve
the same commit/content. Native file hashes/modes must match adapter declarations.

The harness writes `<evidence>/consumer-result.json` only after those assertions.
Its exclusive fields are `schema_version:1`, `result:"passed"`, `acr_sha`,
`central_sha` (producer's), `producer_run_id`, `producer_run_attempt`,
`host:"linux-amd64"`, and two `fixtures` in GOC/FFA order. Each fixture has:

- `key`, exact published `source`, `release:{id,tag,commit,contentHash}` from live
  metadata and peeled remote tag. `contentHash` keeps ACR's `sha256:<64hex>` form.
- `consumers`, in claude-code/codex/cursor order, each `{adapter,kind:"release",
  commit,release_id,tag,contentHash,install:true,realize:true,check:true,
  declared_inventory,native_inventory}`. Inventories are nonempty arrays of
  `{path,mode,sha256}` using normalized adapter destinations; they must agree.
- `sha_install:{source:"github:<repository>@<producer_sha>",commit,contentHash,
  install:true}` from the independent fresh SHA install.

This normalized receipt supplements the ACR test's actual LoadState/release/
archive/declaration assertions; it cannot certify a malicious trusted candidate.
Central rejects mismatched commit/kind/release/content/inventory even if a check
status is true. The same real lane must later run locally and hosted. Synthetic
central parser tests do not establish that real consumers passed.

## Local development and rollout

Run focused tests first, then the unchanged central gates:

```sh
python3 .github/codex-accept/tests/test_contract.py
bash scripts/run-diagnostics.sh
tessl plugin lint
bash scripts/run-tests.sh
```

Both new Python files are explicit root Pyright includes and the existing runner
discovers the nested test suite. Tests create real synthetic Git objects/bundles,
exercise proof/parser mutations, credential leakage, scanner/cleanup failures,
ZIP safety, provenance and job gating. No credentials/model calls are required.
Runtime proof and live receipt expectations above remain future ACR work.

After independent central review and normal merge/publication gates register
this lane, dispatch only a reviewed integrated candidate. The existing
publish-on-main policy remains in force: registering infrastructure can publish
a coding-policy version and still requires its ordinary release verification.
This source change does not satisfy #156's real macOS/Linux conversion, multiple
real Codex releases, publication/same-commit consumer, minor distribution or #62
obligations.
