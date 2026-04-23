# Code Review Response Guide

## Problem/Feature Description

A junior engineer on your team just had their first PR reviewed by an automated code reviewer and doesn't know the team's conventions for responding. The reviewer left three types of feedback: one CI failure (a failing unit test), one suggestion that is clearly correct and should be accepted (simplifying an overly nested conditional), and one suggestion that over-engineers the solution by adding an abstraction layer that isn't needed for the current scope.

The engineer needs a concrete, step-by-step guide explaining exactly how to handle each type of feedback, including how to respond to review threads so nothing is left unresolved. They also want to know: if they make code changes to address the feedback, should those changes go on a new branch or the existing one?

The team cares about substantive replies — accepted suggestions should point concretely at the fix (so a reader can find it), and declined suggestions should cite concrete evidence (so the reviewer can verify the reasoning).

## Output Specification

Produce a document named `review-response-guide.md` that walks through how to handle the three feedback types described above. For each type, include:

- What action to take on the code (if any)
- A sample reply the engineer would post in the review thread — concrete enough that the reviewer can either find the fix (for accepts) or verify the reasoning (for declines)
- Where to push any code changes

Use placeholder values like `<sha>` or `<file>:<line>` where the actual values would depend on the situation.
