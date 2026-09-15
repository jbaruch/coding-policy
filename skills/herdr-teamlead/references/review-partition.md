# Review Partition

The document `validate-partition` checks. One reviewer per slice, so a change
reaches a state that means "reviewed": a slice is saturated when its reviewer
reports clean at the current tip, and the change is reviewed when every slice
is saturated at one tip.

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
- `role` — optional, `reviewer` when absent. The role the slices seat. It never
  contains `#`.
- `slices` — at least two. Each is `{name, paths}` and carries nothing else.
  - `name` — non-empty, unique within the document. It becomes the seat name
    `<role>#<name>` in the plan, and names the slice in that seat's brief and
    report.
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
slice, keyed `<role>#<slice>` in the plan's `assignments`. Each seat inherits
that role's cost, exclusions, round type and requirements, so capability,
contribution-exclusion and headroom ordering apply unchanged and each slice
gets a distinct worker.

`apply` takes those seat names directly: pass each seat its own brief
(`--brief reviewer#api=<path>`). A seat takes its ROLE's brief template, and
the ledger records the role, so the per-role history does not fragment across
seats. The dispatch record keeps the seat, which is what a slice's verdict is
read back through.

Each slice's brief names its own slice and forbids roaming. An observation
outside the slice belongs in a separate section of that report and forms no
part of that slice's verdict.
