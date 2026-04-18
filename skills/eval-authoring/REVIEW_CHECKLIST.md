# Eval Scenario Review Checklist

## Bleeding

Does the task hand the agent the answer?

If the task says "use library X with algorithm Y" and the criteria check "uses library X" and "uses algorithm Y", that's bleeding — the eval tests reading comprehension, not problem-solving. The task should describe the problem; the criteria should check the solution.

**Check**: for each criterion, search the task text for the criterion's expected value. If found verbatim, it's bleeding.

## Leaking

Does the task or criteria reference tile internals?

- File paths, action names, internal terms that only exist in the skill
- Criteria **may** test for skill-prescribed approaches when those use public tools/APIs

**Check**: for each criterion, ask "would someone outside this tile's team understand this term?" If not, it's leaking.

## Quality

- Every criterion `description` must explain what went wrong on failure — not just "mismatch"
- Criteria must be specific and weighted sensibly
- Weights should reflect importance to the task, not equal distribution

## Consistency

- Every criterion must test something the task's output specification asks for
- If the task doesn't mention it, the criteria shouldn't check for it
