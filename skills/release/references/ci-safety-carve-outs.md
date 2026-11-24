# CI Safety Carve-Outs

The full preconditions of each `rules/ci-safety.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Each section carries only the moved detail; the trigger, applies-when and reset lines stay in the rule.

## Superseded-Bot-Review Dismissal Carve-Out

- Narrow exception for dismissing superseded legacy bot review gates.
- coding-policy and consumer policy Apps approve natively; their ordinary change requests are never dismissed by this carve-out.
- Preconditions (all required):
  1. Run `skills/release/dismiss-stale-reviews.sh` with its existing owner, repository and PR arguments. No hand dismissal is authorized.
  2. The request belongs to a legacy bot on the script's `GATING_BOTS` allowlist, never a human or an approving App. On `jbaruch/coding-policy`, only the legacy workflow identity is eligible for ordinary dismissal; external callers retain the existing same-bot contract.
  3. The same bot posted a later all-clear under the script's decision contract, or the coding-policy identity migration below applies. A dismissal or pending review is never an all-clear.
- Identity migration applies only to an OPEN PR in `jbaruch/coding-policy` whose latest `github-actions[bot]` review requests changes. The latest `coding-policy-fleet-reviewer[bot]` review must be APPROVED on the current PR head and submitted later than that legacy review. Only the legacy identity's requests may be dismissed; an App comment, dismissal, older-head approval or quality approval cannot authorize migration.
- The helper preserves its argument, JSON output, exit-code and API-failure contracts for declared external callers. Decision predicates and identity lists live in its header.
- After migration, use native App approvals for ordinary re-reviews. Retain the helper for open legacy gates and external callers.
- Every other change request still gates until resolved through review.

## Publish-Pipeline Loop-Prevention Carve-Out

- Applies when that commit would otherwise re-trigger the same publish workflow (infinite publish loop)
- Preconditions (all required):
  1. Commit is authored by the CI bot inside the publish workflow — never a human- or agent-authored PR commit
  2. Sole purpose is the workflow's own release bookkeeping — manifest version bump, CHANGELOG version stamp — carrying no source or test changes that need CI validation
  3. No CI-skip marker is added
  4. Prevent recursion through the publish trigger or the push credential's native event suppression
  5. Never suppress failing tests
  6. Never bypass a merge gate
  7. Limit a publish-only paths filter to the bookkeeping paths
  8. Retain `workflow_dispatch` for a deliberate release changing only the filtered paths
  9. Keep other workflows eligible on a path-filtered bookkeeping push
- In this repository, `.github/workflows/publish.yml` owns the ignored path set. Auto-bump, the deploy-key push, the exact-main-ref guard and source-bound publication confirmation remain required
- Every other protected-branch write follows Protected Branches

## Content-Only Direct-Push Carve-Out

- Applies when the edited paths are prose / data artifacts a human audience reads directly — not code, not context artifacts an agent loads (rules, skills, scripts, manifests, workflow files, configuration)
- The push may go directly to `main` or `master` without a PR review cycle
- Preconditions (each consuming repo, all required):
  1. Repo documents an authority-of-record rule in its own plugin naming the carve-out — the exact path globs, why those paths qualify as content not code/context, and what policy review the direct-push does NOT carry
  2. Carve-out scopes to one or more named path globs — never a broad wildcard like `**/*.md`. Globs that would match `rules/**`, `skills/**`, workflow files (`*.yml` or `*.yaml`), `.tessl-plugin/plugin.json`, `package.json`, or any executable/loaded artifact (regardless of extension) are mis-scoped
  3. Push-time enforcement keeps any out-of-glob change from landing on the protected branch via direct push (allowlist semantics), satisfied by form A or form B. Post-push CI checks satisfy neither
     - Form A — server-side gate: a GitHub push ruleset with path restriction, a pre-receive hook, or an equivalent server-side gate rejects the ref update
     - Form B — client-side content-only diff gate: permitted only where the platform cannot express server-side allowlist enforcement (e.g., github.com personal repos)
       - The publishing tool runs the gate as a deterministic script (per `rules/script-delegation.md`), never agent judgment and never a bounded classification
       - The gate enumerates the paths the push would change on the protected branch and direct-pushes only when every one matches a declared content glob
       - Any out-of-glob path forces an automatic branch + PR fallback — never an operator-say-so override
       - The authority-of-record rule (precondition 1) names the gate script
