# Hook Throttle-Stamp Schema

Schema for the cross-invocation throttle stamps the `SessionStart` hooks write,
per `rules/stateful-artifacts.md`.

## Artifacts

| Stamp path | Owner (writer + reader) | Purpose |
| ---------- | ----------------------- | ------- |
| `$FRESHNESS_STATE_DIR/last-check` (default `${TMPDIR:-/tmp}/coding-policy-freshness/last-check`) | `hooks/check-policy-freshness.sh` | Throttle the `tessl outdated` registry call |
| `$SYNC_STATE_DIR/sync-<repo-key>` (default `${TMPDIR:-/tmp}/coding-policy-sync/sync-<repo-key>`) | `hooks/check-git-sync.sh` | Throttle the `git fetch origin` call, per repo (`<repo-key>` = cksum of the repo toplevel path) |

Each stamp has exactly one owner hook that both writes and reads it. No other
skill or hook touches it.

## Record Format

One line, two space-separated fields:

```
<schema_version> <checked_at>
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `1`. Bumped on any shape change |
| `checked_at` | integer | Epoch seconds when the owner last ran its throttled call |

Example: `1 1734567890`

## Writer / Reader Contract

- **Writer** (owner hook): writes `1 <now>` before running the throttled call,
  so a slow or failed call still throttles the next session.
- **Reader** (owner hook): throttles only when the record's `schema_version` is
  `1` **and** `checked_at` is an integer within the throttle window. Any other
  record — a `schema_version` this hook does not accept, a non-integer
  `checked_at`, an absent stamp — is treated as **no usable prior state**: the
  hook runs its call (no throttle) and rewrites a current record.

## Migration

- Only the owner hook migrates. On reading a record it does not accept — an
  older bare-integer stamp (pre-versioning) or a future `schema_version` — it
  takes the no-usable-prior-state path above and rewrites a `1 <now>` record on
  its next throttled run.
- This fallback is safe and non-disruptive: the only effect of discarding a
  record is one extra network / registry call. It never escalates work.
- Bump `schema_version` for any shape change; do not repurpose `checked_at`
  silently.

## Hints, Not Authority

The stamp is a last-seen timestamp cache, never ground truth. A missing,
corrupt, or unreadable stamp degrades only to an extra call — never to a wrong
result — so the hooks tolerate its absence at every read.
