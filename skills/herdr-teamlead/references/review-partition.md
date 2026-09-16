# Review Partition

The document `validate-partition` checks. One seat per slice, so a change
reaches a state that means the seated responsibility has passed: a slice is
saturated when its seat reports clean at the current tip, and the
responsibility has passed when every slice is saturated at one tip. A tester
partition passes the tester gate, never the reviewer's.

`rules/agent-team-operation.md` Review Before PR carries the contract this
format serves. Write the document only for a round filling several seats of one
role; a single-seat round needs none.

## Format

```json
{
  "schema_version": 1,
  "role": "reviewer",
  "slices": [
    {"name": "api",  "paths": ["src/api/*", "docs/api/**"]},
    {"name": "core", "paths": ["src/core/*", "README.md"]}
  ]
}
```

- `schema_version` — `1`. Any other value is refused.
- `role` — optional, and `reviewer` when absent. The accepted set is the
  script's — see `skills/herdr-teamlead/teamlead/tiers.py`, the `SEATABLE_ROLES`
  constant. A role outside it is refused, at the document and at `--roles`.
- `slices` — at least two. Each is `{name, paths}` and carries nothing else.
  - `name` — unique within the document, and written with letters, digits,
    underscores, dots or hyphens, starting with a letter or digit. It becomes
    the seat name `<role>#<name>` in the plan, and a seat is a CLI key: the
    left side of `--brief SEAT=PATH` and `--report SEAT=PATH`. A name carrying
    `=`, `#`, a comma or whitespace does not read back, and is refused.
  - `paths` — a non-empty array of globs matched against the round's changed
    paths, `fnmatch`-style (`*` does not stop at `/`; `**` is ordinary text).

## Validate before planning

```bash
teamlead.sh validate-partition --repo <repo> --base <base> [--head <head>] \
  --partition <partition.json>
```

Exit 0 emits the ownership payload — every slice with the changed paths it
owns, plus the full changed set:

```json
{"schema_version": 1,
 "slices": [{"name": "api", "paths": ["src/api/routes.py"]},
            {"name": "core", "paths": ["README.md", "src/core/db.py"]}],
 "changed": ["README.md", "src/api/routes.py", "src/core/db.py"]}
```

Exit 1 names everything that cannot carry a verdict, in one run, with the paths
in `details`:

- `unowned` — a changed path no slice owns; a gap reads as a clean slice
- `overlaps` — a changed path more than one slice owns; two verdicts, no owner
- `empty` — a slice owning no changed path; a worker spent for no verdict

A slice party to an overlap is not also reported empty: that overlap is why it
owns nothing. Fix the document and re-run. Dispatch only once it exits 0.

## Seating

`plan --partition <partition.json>` replaces the named role with one seat per
slice, keyed `<role>#<slice>` in the plan's `assignments`. A seat's ROLE
decides everything the responsibility governs — its cost and rotation history,
exclusions, round type, requirements, tier qualification and the review-package
checks its brief owes — so capability, contribution-exclusion and headroom
ordering apply unchanged and each slice gets a distinct worker.

`plan --partition` takes the OUTPUT of `validate-partition`, not the document
it was built from. The result carries the slices with their resolved paths and
the `changed` set they were checked against, so a seated round is seated from a
partition proven disjoint and exhaustive over this round's change. `plan` reads
no repo, base or head and cannot check ownership itself; passing the document
is refused, naming the command to run.

`plan` emits `slice_paths`, a `{seat: [glob, ...]}` map, `slice_digest` over it,
and `seat_digests`, one digest per seat over that seat and the globs it owns.
Each seat's values carry its globs as `SLICE_PATHS` and its own `seat_digests`
entry as `SLICE_DIGEST`; `compose-briefs.sh` renders both into the brief without
recomputing either. `apply` re-derives each seat's digest from the plan and
checks three facts against that seat's brief: the digest, the slice name, and
every one of its globs. A boundary edited after validation — in the plan, in the
values, or in a brief written by hand — no longer matches, and the dispatch is
refused. Two seats' briefs exchanged are refused with it, which a round-level
digest alone would pass. `compose-briefs.sh`
renders the slice name and those paths into the brief's `SLICE_SCOPE` and
refuses a seat without them: a slice name alone leaves the worker no boundary
to resolve, and the composer never reads the partition document. `SLICE_SCOPE`
itself is composed, never supplied.

`apply` takes those seat names directly: pass each seat its own brief
(`--brief reviewer#api=<path>`), and `--task`, since the seat lives on the
dispatch. A seat takes its ROLE's brief template, and the ledger records the
role, so the per-role history does not fragment across seats. The dispatch
record keeps the seat, which is what a slice's verdict is read back through.

Each slice's brief names its own slice and forbids roaming. An observation
outside the slice belongs in a separate section of that report and forms no
part of that slice's verdict.
