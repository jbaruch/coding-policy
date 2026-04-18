# Eval Scenario Quality Audit

## Problem/Feature Description

A batch of automatically generated evaluation scenarios has been flagged for review before they can be used in production. Automated generators sometimes produce scenarios that leak implementation details into the task description (so a graded agent can pass just by reading the task carefully) or reference internal system names that a real agent would not know about. There are also cases where criteria don't match what the task actually asks for, or where failure messages are so vague ("mismatch") that a grader cannot tell what went wrong.

You have been handed three scenario folders that need to be reviewed and fixed before they go live. Your job is to read each `task.md` and `criteria.json`, identify the specific problems, fix what can be fixed, and delete any scenario that cannot be salvaged. Document your findings in a report so the team knows what was wrong and what was done.

## Output Specification

- For each scenario you fix, update the files in place (edit `task.md` and/or `criteria.json` as needed)
- For any scenario you delete, remove its directory
- Produce a file named `audit-report.md` summarizing: what problems were found in each scenario, what was done to fix each one, and why any scenario was deleted if applicable

## Input Files

The following scenario files are provided as inputs. Extract them before beginning.

=============== FILE: evals/scenario-a/task.md ===============
# Authenticate a User

## Problem Description

A web application needs to authenticate users. The agent should implement JWT authentication using the `jsonwebtoken` npm package. The token should be signed using the HS256 algorithm and expire after 3600 seconds.

## Output Specification

Produce a file named `auth.js` that exports a function `createToken(userId)` which returns a signed JWT.
=============== FILE: evals/scenario-a/criteria.json ===============
{
  "context": "Tests JWT authentication implementation.",
  "type": "weighted_checklist",
  "checklist": [
    {
      "name": "Uses jsonwebtoken",
      "description": "Uses the jsonwebtoken package",
      "max_score": 25
    },
    {
      "name": "HS256 algorithm",
      "description": "Signs using HS256 algorithm",
      "max_score": 25
    },
    {
      "name": "Expiry 3600",
      "description": "Token expires after 3600 seconds",
      "max_score": 25
    },
    {
      "name": "Uses skill-internal action createJwtToken",
      "description": "Calls the internal skill action `tile://auth-skill/createJwtToken` to produce the token",
      "max_score": 25
    }
  ]
}
=============== FILE: evals/scenario-b/task.md ===============
# Summarize a Document

## Problem Description

A content team needs a tool to summarize long documents. Write a Python script that reads a text file and prints a summary.

## Output Specification

Produce `summarize.py` that accepts a filename argument and prints a 3-sentence summary.
=============== FILE: evals/scenario-b/criteria.json ===============
{
  "context": "Tests document summarization.",
  "type": "weighted_checklist",
  "checklist": [
    {
      "name": "Reads file",
      "description": "mismatch",
      "max_score": 25
    },
    {
      "name": "3 sentences",
      "description": "mismatch",
      "max_score": 25
    },
    {
      "name": "Uses OpenAI API",
      "description": "The script calls the OpenAI completions API to generate the summary — does NOT use a local heuristic",
      "max_score": 25
    },
    {
      "name": "Correct output format",
      "description": "mismatch",
      "max_score": 25
    }
  ]
}
=============== FILE: evals/scenario-c/task.md ===============
# Send a Notification

## Problem Description

An operations team needs to send Slack notifications when deployments complete. The task is too vague.

## Output Specification

Do something with Slack.
