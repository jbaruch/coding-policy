# Code Review Response Guide

## Problem/Feature Description

A junior engineer on your team just had their first PR reviewed by an automated code reviewer and doesn't know the team's conventions for responding. The reviewer left three types of feedback: one CI failure (a failing unit test), one suggestion that is clearly correct and should be accepted (simplifying an overly nested conditional), and one suggestion that over-engineers the solution by adding an abstraction layer that isn't needed for the current scope.

The engineer needs a concrete, step-by-step guide explaining exactly how to handle each type of feedback, including how to respond to review threads so nothing is left unresolved. They also want to know: if they make code changes to address the feedback, should those changes go on a new branch or the existing one?

The team cares about a consistent reply style in review threads — accepted suggestions and declined suggestions each have a specific format the team uses.

## Output Specification

Produce a document named `review-response-guide.md` that walks through how to handle the three feedback types described above. For each type, include:

- What action to take on the code (if any)
- The exact text to post as a reply in the review thread
- Where to push any code changes

Make the guide concrete: use placeholder values like `<sha>` or `<reason>` where the actual values would depend on the situation, but show the exact reply format the team expects.
