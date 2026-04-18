# Copilot Review — GraphQL Reference

## Request Review

Must use GraphQL, not REST — REST silently drops bot reviewers.

```bash
# Get PR node ID
PR_NODE_ID=$(gh api graphql -f query='
  query {
    repository(owner: "<owner>", name: "<repo>") {
      pullRequest(number: <N>) { id }
    }
  }
' --jq '.data.repository.pullRequest.id')

# Request Copilot review
gh api graphql -f query='
  mutation {
    requestReviews(input: {
      pullRequestId: "'"$PR_NODE_ID"'",
      botIds: ["BOT_kgDOCnlnWA"]
    }) {
      clientMutationId
    }
  }
'
```

## Fallback — Retrieve Bot ID

If the bot ID `BOT_kgDOCnlnWA` goes stale, retrieve it from a past Copilot review:

```bash
gh api graphql -f query='
  query {
    repository(owner: "<owner>", name: "<repo>") {
      pullRequests(last: 20) {
        nodes {
          reviews(first: 10) {
            nodes {
              author { ... on Bot { id login } }
            }
          }
        }
      }
    }
  }
'
```

## Verify

Confirm the request was accepted:

```bash
gh api repos/<owner>/<repo>/pulls/<N> --jq '.requested_reviewers[].login'
```
