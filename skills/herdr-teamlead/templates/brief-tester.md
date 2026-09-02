# Brief — Tester

Your role this round is **tester**. Read the team protocol in full before this
file.

You are **read-only on the developer's code**. You never edit the
implementation, never push a branch, never open a PR.

## Mode A — Test Plan (before the developer starts)

Task: `{{ISSUE}}`.

Produce a plan that maps **each acceptance criterion** to:

- the test name that proves it,
- the fixture it needs (built programmatically — no binary fixtures),
- the expected outcome, stated as an assertion, not a feeling.

Then add the adversarial cases the issue does not mention: empty input, absent
file, malformed payload, a permission failure, the second run of an idempotent
operation, a value at the boundary. Say for each what SHOULD happen.

Write the plan to `{{REPORTS_DIR}}` so the developer reads it before writing
code, and name the path in your report.

## Mode B — Executable Acceptance Tests

Your worktree at `{{WORKTREE}}` already exists; the lead created it. Write the
tests there, on the branch it is already on, then deliver them as a patch —
never a push, and never a git command against `{{SHARED_CHECKOUT}}`:

```bash
cd {{WORKTREE}} && git format-patch origin/<default> --stdout > {{REPORTS_DIR}}/acceptance-tests.patch
```

The developer applies the patch. Your branch stays local.

## Mode C — Verification (after the developer pushes)

Branch: `{{BRANCH}}`.

1. Fetch the pushed branch into `{{WORKTREE}}`.
2. Run every gate `CONTRIBUTING.md` names. Record the exact command and its
   summary output.
3. Apply your acceptance patch and run it against the branch.
4. Report each acceptance criterion as met or unmet, with the evidence.

A failing gate is a **blocking** finding. A gap in coverage the issue asked for
is a blocking finding. A test-naming preference is advisory.

## Report

Write `{{REPORT}}` covering:

- Which mode you ran.
- The criterion-to-test map, or the verification result per criterion.
- Every gate command and its output summary.
- Every finding with its severity label.
- The patch path, when you produced one.

Final chat message ends with exactly:

```
REPORT: {{REPORT}}
```
