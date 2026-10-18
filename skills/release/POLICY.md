# General Coding Policy Inputs

## Installed Policy

- This file locates the installed general rules; it grants no release or team authority.
- Tessl installs use the project `.tessl/RULES.md` or the selected global Tessl rule index.
- Follow the index's rule links and their declared scopes.

## ACR Native Policy

- Resolve these paths from the consumer project root, not from this installed skill directory.
- Codex reads `AGENTS.md` and `AGENTS.override.md` when present, including their instruction imports.
- Claude Code reads `.claude/CLAUDE.md` and `CLAUDE.md` when present, including their instruction imports.
- Cursor reads `.cursor/rules/acr__jbaruch__coding-policy__*.mdc`, respecting always-on and path scopes.
- Shared native skill installations use the selected agent's same instruction surface.
- Preserve operator-owned instructions alongside the managed package rules.
- Missing managed coding-policy rules require installation repair; an unrelated instruction file never substitutes for the package policy.
