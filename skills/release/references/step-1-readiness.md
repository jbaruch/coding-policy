## Step 1 — Verify Readiness

Nothing below runs until this exits 0:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/check-leftovers.sh" --release
```

Exit 0 clears this readiness gate. Read `others` and `attention` even on exit 0.
Preserve unrelated work and record its path, evidence, consequence and required
owner decision in the existing task report or standalone conversation. Continue
this release without committing, stashing, ignoring or deleting that work.
Exit 1 blocks on `blocking`; resolve only the selected checkout's authorized work
and re-run. Exit 2 is a usage or tool-state error, never a verdict.
The scope and observation contract live in `skills/release/check-leftovers.sh`
(header). The repository-wide inspection mode is not a release prerequisite.

- Confirm you're on a feature branch (not `main`/`master`)
- Run the test suite — all tests must pass
- Run the linter — no warnings or errors
- Self-audit the diff against every governing rule or skill whose domain covers the touched paths (e.g., auto-loaded prose in `rules/` or `skills/` → `rules/context-writing-style.md`; new scripts → `rules/script-delegation.md` and `rules/testing-standards.md`)
- Grep the diff for the literal markers each governing rule or skill names:
  - banned connectives `because`, `therefore`, `since`, etc. per `rules/context-writing-style.md`
  - the `outer-boundary-process-contract` token per `rules/error-handling.md`
- Run any local check the rule or skill prescribes:
  - `bash -n <script>` must exit 0 on shell scripts
  - the script's own fixture test must pass
- The policy reviewer is a backstop, not the first read
- If anything fails, fix it before proceeding
