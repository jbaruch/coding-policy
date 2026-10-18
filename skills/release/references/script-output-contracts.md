# Script Lookup and Output Contracts

These preconditions bind the exceptions named in `rules/script-delegation.md`.

## Scoped Plugin Contracts

Applies to another plugin's scoped skill lookup or stdout contract, for an
installed plugin or one authored in its own verified repository.

Preconditions (all required):

1. The plugin's named rule enumerates its authored scope, co-shipped targets and input/output contract.
2. Regression coverage verifies the declared lookup and output behavior.
3. The exception grants no runtime authority and waives no check after lookup.
4. During source authoring, the review proves plugin identity from the trusted base manifest matching the reviewed repository.
5. During source authoring, the review reads the head's declared regular rule blobs and checks their self-consistency.

Every other command block follows Scripts Are Real Files and Script Requirements unchanged.

## Version Stdout

Applies to `skills/release/registry-baseline.sh` and
`skills/release/confirm-tessl-landed.sh` when the wrapper's whole output is the
version or verified `unpublished` baseline its caller passes to the next gate command.

Preconditions (all required):

1. Success emits only that version or verified baseline sentinel and a newline.
2. Failure emits no version, exits non-zero, and writes an actionable diagnostic to stderr.
3. Each distinct verdict of the helper it wraps reaches the caller as a distinct exit code.
4. `unpublished` requires verified registry absence or a supported empty versions response; authentication failures and indeterminate reads never prove absence.

Every other release script retains the JSON-producing requirement.

## Bounded Runner Stdout

Applies to `skills/release/lib/bounded-run.sh` when a caller runs another script
under a wall-clock budget.

Preconditions (all required):

1. The runner's stdout is exactly the wrapped command's stdout.
2. The runner adds nothing to stdout on success.
3. On a spent budget the runner adds nothing to stdout, writes a diagnostic to stderr, and exits 124.
4. The runner's own failures exit non-zero and write only to stderr.
5. The wrapped command itself satisfies Script Requirements.
6. A caller discards the wrapped command's partial stdout on exit 124 or 125.

Every other skill script retains the JSON-producing requirement.
