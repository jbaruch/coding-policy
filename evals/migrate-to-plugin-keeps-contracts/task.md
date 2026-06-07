# Migrate a Legacy Plugin off tile.json

## Problem/Feature Description

The repo `acme/widgets` is an older Tessl plugin still shipped in the legacy `tile.json` form — there is no `.tessl-plugin/plugin.json` yet. The maintainer wants it brought fully onto the current plugin manifest form and the repo's wording brought in line, so a reader isn't left with a half-renamed project.

The relevant current contents:

`tile.json` (the legacy manifest, present at the repo root) and `.tileignore` (a build-artifact ignore file) both exist; there is no `.tessl-plugin/plugin.json`.

`README.md`:

```
# acme/widgets

Widgets tile for ACME's agents. 12 steering rules covering commits, testing, and release.
Install with `tessl install acme/widgets`.
```

`skills/release/check-publish.sh`:

```bash
# Poll the registry for the published version's moderation state.
#   tessl api v1/tiles/<workspace>/<name>/versions/<version>
latest=$(tessl tile info "${workspace}/${name}" | grep "Latest Version" | awk '{print $NF}')
```

`skills/release/registry.ts`:

```ts
const tileRegistry = new Registry(workspace);
export function fetchTile(name: string) { return tileRegistry.get(name); }
```

`CHANGELOG.md` (top entry):

```
- Migrated the build from a hand-written tile.json to the generated manifest.
```

## Output Specification

Produce a migration plan in a file named `migration-plan.md`. Walk through, in order, the concrete commands you would run to convert the manifest and sanitize the layout, then list every wording edit you would make to the files above — for each, give the before and after, or state explicitly that the occurrence stays unchanged and why.
