# Lift Analysis Reference

Reference material for Step 9 of the `eval-authoring` skill. Pulled out of the SKILL.md body so the main workflow stays scannable.

## What Lift Means

`lift = with_context_score - baseline_score`

Lift is the number that matters. Aggregate attainment on its own is a vanity metric — a tile scoring 99% with-context and 73% baseline is contributing 26 points of real value, not 99. A 100/100 scenario with the tile loaded but 100/100 baseline is delivering zero tile value, no matter how good the score looks.

## Thresholds (Positive Cases)

| Lift band | Verdict | Action |
|---|---|---|
| **≥ 40** | Healthy signal | The tile is doing real work — usually checks specific tile-prescribed choices (particular bot-ID discovery, particular reply template, particular CLI sequence). Keep these scenarios; do NOT soften criteria toward "testing reasoning" baseline already does |
| **10–39** | Weak | Audit for the three causes below — the scenario likely tests baseline competence rather than tile value |
| **< 10** | No signal | Apply the three-cause diagnosis below before retiring; many "no lift" scenarios are actually "wrong target" |

### Three causes for weak / no lift

1. **Coincidence with universal competence** — the tile prescribes what baseline already does. The rule is documentation, not lift-producing. Accept the no-lift result, or retire the scenario as a null test.
2. **Task leaked the technique** — baseline pattern-matched the answer from the task text. Fix the task (strip the leaked literal); keep the criterion.
3. **Criteria grade universal competence** — the criteria test things baseline always does (basic git safety, obvious engineering judgement, general engineering-101) rather than the specific manner the tile prescribes. Rewrite the criteria to check the tile's specific prescription (e.g., the exact reply template, the chosen flag, the named CLI sequence), or retire the scenario if nothing tile-specific can be salvaged from it. The default action is `rewrite-criteria` (the task itself is fine; the bleed lives in the criteria); `retire` is the fallback when no tile-specific replacement criteria exist.

## Negative Cases (Refusal-Based Scenarios)

Near-zero lift on a negative case is acceptable **only** when the baseline refusal is driven by universal knowledge (obvious error cases — e.g., "do not push secrets," "do not merge red CI"). Tile-specific refusal reasoning (e.g., "refuses to overwrite an existing reviewer workflow because the tile's overwrite-guard says so") must still show lift; if it doesn't, apply the three-cause diagnosis above.

## Diagnosing Where to Fix

For each scenario with non-zero lift but with-context below 100%, identify the failing criteria and decide where the problem lives:

- **The skill** — unclear instruction. The agent didn't apply the rule because the SKILL.md doesn't make the prescription explicit enough. Fix in `skills/<name>/SKILL.md`.
- **The task** — doesn't ask for what the criteria test. The criteria check for X but the task never gives the agent a reason to do X. Fix in `evals/<scenario>/task.md`.
- **The criteria** — tests the wrong thing. The criteria phrase the requirement as something baseline can satisfy without the tile, or as something the tile doesn't actually prescribe. Fix in `evals/<scenario>/criteria.json`.

The skill / task / criteria triage is the most common reason a fix lands in the wrong place — re-asking "what artifact owns this concern?" before editing anything saves a round trip.
