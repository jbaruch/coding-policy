# Review Partition

The document `validate-partition` checks and `plan --partition` seats. One
reviewer per slice, so a change reaches a state that means "reviewed": a slice
is saturated when its reviewer reports clean at the current tip, and the change
is reviewed when every slice is saturated at one tip.

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
    `<role>#<name>` in the plan.
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

Exit 1 names what cannot carry a verdict, with the paths in `details`:

- a changed path no slice owns (`unowned`) — a gap reads as a clean slice
- a changed path more than one slice owns (`overlaps`) — two verdicts, no owner
- a slice owning no changed path (`empty`) — a seat spent for no verdict

Fix the document and re-run. Plan only once it exits 0.

## Seating

`plan` does not yet fill several seats from one partition. Dispatch resolves
briefs, requirements and round tiers by role name, so a seat name would reach
`apply` as an unknown role. Until it carries seats, a round runs its slices as
separate reviewer dispatches, one per slice, against the same tip.

Each slice's brief names its own slice and forbids roaming. An observation
outside the slice belongs in a separate section of that report and forms no
part of that slice's verdict.
