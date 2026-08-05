# resurface-response-clarity state schema

Per-session turn counter written and read by `hooks/resurface-response-clarity.sh` — the sole owner.

## Location

- `$RESURFACE_STATE_DIR/<session>.json` (default `${TMPDIR:-/tmp}/coding-policy-resurface/`)
- One file per hook session; `<session>` is the sanitized `session_id` from the hook payload
- Ephemeral — safe to delete; a missing file means "first turn"

## Fields

- `schema_version` (int) — currently `1`
- `session_id` (string) — sanitized session id (the filename stem)
- `turn_count` (int) — cumulative UserPromptSubmit count for this session

## Writer / reader contract

- The hook is the only writer and the only reader
- On an absent, unreadable, or corrupt file the hook treats `turn_count` as `0` (no prior state) — the next turn becomes turn 1 and fires
- Staleness is tolerable per `rules/stateful-artifacts.md` Hints, Not Authority — the counter has no external source to verify against, and a stale or reset count only shifts the reminder cadence (a reminder fires a turn early or late), never a correctness impact

## Migration

- Only the hook migrates. On a `schema_version` it does not recognize it treats the record as no usable prior state (count `0`) and rewrites at the current version on the next turn
