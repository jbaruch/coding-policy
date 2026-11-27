---
alwaysApply: true
description: Read original review feedback before disposition or merge.
---

# Reviewer Feedback Reading

## Independent Review Context

- An independent read-only review may use a bounded historical index to locate contracts and prior source identities.
- An index never disposes of feedback or establishes that an item needs no action.
- A fresh current-head review assesses current source independently.
- Its verdict does not certify that anyone read or handled earlier feedback.

## Read Before Disposition

- Before disposing of feedback or merging, read every original review body and inline comment in full across all authors, states and pages. Use `skills/release/poll-pr-reviews.sh` and GitHub's original feedback.
- States, inline-comment counts and non-blocking labels are gate or loop signals, not substitutes for the text.
- Act under `rules/review-severity.md` after reading.

## Report the Substance

- Name each reviewer whose feedback you read.
- Summarize each review's substance separately, not just its state.
