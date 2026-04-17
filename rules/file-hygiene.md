---
alwaysApply: true
---

# File Hygiene

## .gitignore

- Maintain a proper `.gitignore` for the project's language and tooling
- Cover: build artifacts, cache directories, IDE/editor files, OS files, dependency directories
- Use templates from github/gitignore as a starting point

## Generated Files

- Never commit generated files (compiled output, bundled assets, rendered docs)
- If a file can be reproduced from source, it doesn't belong in the repo

## Standalone Scripts

- Scripts must have entry-point guards (e.g., `if __name__ == "__main__"` in Python, `if (require.main === module)` in Node)
- This makes scripts both executable and importable for testing

## I/O Conventions

- stdout for program output, stderr for errors and diagnostics
- Exit 0 on success, non-zero on failure
- Use meaningful exit codes when the platform supports them

## Idempotency

- Scripts should be safe to run multiple times
- Don't fail if a directory already exists, a file was already processed, or a resource was already created
