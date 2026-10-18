## Step 2 — Create PR

- Once Step 1's readiness checks pass, create the PR automatically — the green readiness checks are the gate. Do not pause to ask a human whether to open it.
- Push the branch: `git push -u origin <branch>`
- Resolve origin's repository, so the PR opens where the branch was pushed and never in `gh`'s own default (`gh repo set-default`):

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  python3 "$CP/skills/release/origin-repo.py" .
  ```

  Contract: see `skills/release/origin-repo.py` — top-of-file docstring. Emits `{ "repo", "owner", "name" }`. A non-zero exit stops the release with its stderr diagnostic
- Create the PR with `gh pr create --repo <repo>`, `<repo>` from that output:
  - **Title**: `<type>(<scope>): <imperative summary>`
  - **Body**:
    ```
    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps>

    Closes #<n>
    ```
  - **Issue lines**, one or more of, each on a line of its own:
    - `Closes #<n>`, one line per issue the PR fully resolves
    - `Part of #<n>` for an issue it only partly resolves
    - `Part of https://github.com/<owner>/<repo>/issues/<n>` for partial work tracked in another repository
    - `No issue` for work with no tracking issue
  - `Refs #<n>`, `Related #<n>` and a bare `#<n>` close nothing
- Check the link GitHub resolved from the body:

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  python3 "$CP/skills/release/check-closing-issues.py" <owner> <repo> <pr-number>
  ```

- Exit 0 passes
- Exit 1 (`unlinked`): the body names no issue GitHub will close and declares no alternative. Edit the body and re-run
- Exit 2 is a usage, environment or `gh` error
- Output fields and verdicts are the script's contract — see `skills/release/check-closing-issues.py` docstring

When this step is wrapped in a reusable script (e.g., `release.sh` that other devs run unattended), see the script-wrapping gates at:

```text
skills/release/references/SCRIPTING.md
```

Proceed immediately to Step 3.

