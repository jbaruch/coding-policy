# Optional Team Ruling Interface

## Applicability

- Standalone releases use operator rulings and require no team runtime.
- A verified team session may supply an enrolled ruling through the separately installed `jbaruch/herdr-policy` package.
- Its team contract owns nomination, source-change handoff and adjudicator staffing.
- Missing or failed team verification never falls back to an operator ruling.

## Owner Contract

- `dismiss-ruled-review.sh` resolves the compatible Herdr foreman skill beside the executing package or in the same ACR native skill host.
- It calls `foreman.sh verify-ruling --task <id> --ruling <file>` through Bash.
- Exit 0 returns one JSON object carrying the enrolled file's 64-character lowercase SHA256 in `sha256`.
- Exit 1 returns the foreman error object on stderr; this is a binding refusal.
- Any other exit or malformed output is a tool failure and blocks dismissal.
- The dismissal owner compares that digest with the bytes it parses; a changed file fails.
- This interface grants no dispatch, worker, merge or production authority.

## Verification

- `tests/release/tests/test_dismiss_ruled_review.sh` covers operator-only operation, missing owners, refusals, malformed results and digest mismatch.
- The team package owns independent enrollment, native session and report provenance checks.
