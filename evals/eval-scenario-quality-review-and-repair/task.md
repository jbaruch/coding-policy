# Eval Scenario Quality Audit

## Problem/Feature Description

A batch of automatically generated evaluation scenarios has been flagged for review before it can go into production. Automated generators sometimes produce scenarios that compromise the signal of the eval suite; your job is to find out which scenarios have issues, decide what to do about each, and document what you found.

You have been handed three scenario folders. Read each `task.md` and `criteria.json`, identify any problems against this tile's expectations for eval authoring, fix what can be fixed, and delete any scenario that cannot be salvaged. Produce a written audit report so the team can review your decisions.

## Output Specification

- For each scenario you fix, update the files in place (edit `task.md` and/or `criteria.json` as needed).
- For any scenario that cannot be repaired, delete its directory.
- Produce a file named `audit-report.md` summarising: what you found in each scenario, what you did (fix vs delete), and your reasoning — so a reviewer can judge your calls.

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

An operations team needs to send Slack notifications when deployments complete.

## Output Specification

Do something with Slack.
