# Lift Analysis Reference

Reference material for Step 9 of the `eval-authoring` skill. Pulled out of the SKILL.md body so the main workflow stays scannable.

## What Lift Means

`lift = with_context_score - baseline_score`

Lift is the number that matters. Aggregate attainment on its own is a vanity metric — a tile scoring 99% with-context and 73% baseline is contributing 26 points of real value, not 99. A 100/100 scenario with the tile loaded but 100/100 baseline is delivering zero tile value, no matter how good the score looks.

## Measure on the Floor Model

- Lift is model-dependent and often diverges — sometimes inverts — across solver strength: a scenario near-zero on a strong solver can deliver large lift on a weak one
- Measure lift on the **floor model** (the weakest agent in the consumer spectrum), not the strongest — a strong solver's baseline competence masks context the floor model actually needs
- coding-policy's floor is `glm-5.1` (the CI publish-run solver); a curation decision reached on a stronger solver such as `claude-opus-4-7` must be re-checked against the floor before any retire
- Retire only when lift is near-zero on the floor model too

## Thresholds (Positive Cases)

| Lift band | Verdict | Action |
|---|---|---|
| **≥ 40** | Healthy signal | The tile is doing real work — usually checks specific tile-prescribed choices (particular bot-ID discovery, particular reply template, particular CLI sequence). Keep these scenarios; do NOT soften criteria toward "testing reasoning" baseline already does |
| **10–39** | Weak | Audit for the three causes below — the scenario likely tests baseline competence rather than tile value |
| **< 10** | No signal | Apply the three-cause diagnosis below before retiring; many "no lift" scenarios are actually "wrong target" |

### Three causes for weak / no lift

1. **Coincidence with universal competence** — the tile's prescription *itself* is what baseline already produces; no more-specific tile behaviour is being missed, so no replacement criterion can be salvaged. Retire the scenario as a null test.
2. **Task leaked the technique** — baseline pattern-matched the answer from the task text. Fix the task (strip the leaked literal); keep the criterion.
3. **Criteria grade universal competence** — the criteria test things baseline always does (basic git safety, obvious engineering judgement, general engineering-101) while the tile prescribes a *more specific* behaviour the criteria failed to check. Rewrite the criteria to check that specific prescription (the exact reply template, the chosen flag, the named CLI sequence).

**Cause #1 vs #3 discriminator** — both present as "baseline already does it," and conflating them sends the action the wrong way. Ask: does the tile prescribe a more specific behaviour than the criteria currently grade, one baseline would not produce by default?

- No salvageable tile-specific replacement (the tile's prescription *is* the universal behaviour — e.g., imperative-mood commit subjects) → cause #1 → `retire`
- A more-specific prescription exists and the criteria merely glossed it (e.g., a canary 10% / 15-min-bake sequence the criteria phrased as "mention deployment") → cause #3 → `rewrite-criteria`

The salvageable-replacement test decides — there is no default action.

## Negative Cases (Refusal-Based Scenarios)

Near-zero lift on a negative case is acceptable **only** when the baseline refusal is driven by universal knowledge (obvious error cases — e.g., "do not push secrets," "do not merge red CI"). Tile-specific refusal reasoning (e.g., "refuses to overwrite an existing reviewer workflow because the tile's overwrite-guard says so") must still show lift; if it doesn't, apply the three-cause diagnosis above.

## Diagnosing Where to Fix

For each scenario with non-zero lift but with-context below 100%, identify the failing criteria and decide where the problem lives:

- **The skill** — unclear instruction. The agent didn't apply the rule because the SKILL.md doesn't make the prescription explicit enough. Fix in `skills/<name>/SKILL.md`.
- **The task** — doesn't ask for what the criteria test. The criteria check for X but the task never gives the agent a reason to do X. Fix in `evals/<scenario>/task.md`.
- **The criteria** — tests the wrong thing. The criteria phrase the requirement as something baseline can satisfy without the tile, or as something the tile doesn't actually prescribe. Fix in `evals/<scenario>/criteria.json`.

The skill / task / criteria triage is the most common reason a fix lands in the wrong place — re-asking "what artifact owns this concern?" before editing anything saves a round trip.
