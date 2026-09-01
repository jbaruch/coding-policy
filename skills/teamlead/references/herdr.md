# Herdr Reference for the Team Lead

Everything the `teamlead` skill needs to know about driving [Herdr](https://herdr.dev),
a terminal multiplexer that recognizes coding agents running in its panes.
Gathered from a real three-agent round on 2026-09-01 that built
`github.com/jbaruch/agentic-context-registry` with workers `claude` (Claude
Code), `codex` (OpenAI Codex CLI), and `grok` (Grok CLI).

`herdr --skill` prints the authoritative control skill shipped with the
installed binary. `herdr agent`, `herdr pane`, `herdr workspace` (each group
with no subcommand) print the current command surface. When this file and the
binary disagree, the binary wins.

## Am I Inside Herdr

```bash
test "${HERDR_ENV:-}" = 1
```

Herdr injects the caller's own identifiers into every managed pane:
`$HERDR_WORKSPACE_ID` (`w1`), `$HERDR_TAB_ID` (`w1:t1`), `$HERDR_PANE_ID`
(`w1:p1`). Outside Herdr, say so and stop — do not drive a session you are not
part of.

Control commands print JSON on stdout. A server error is JSON on stderr with
exit 1; a syntax error exits 2.

## Roster and Names

```bash
herdr agent list
herdr agent rename <pane-id> <name>
```

`agent list` returns `{"result":{"agents":[...]}}`. Each element carries
`agent` (the kind: `claude`, `codex`, `grok`), `agent_status`
(`idle|working|blocked|done|unknown`), `pane_id`, `cwd`, and an optional
`name`. Names match `[a-z][a-z0-9_-]{0,31}`, are unique among live agents, and
follow the pane occupant — a name is cleared when that agent exits or is
replaced. Name each worker once; every later command addresses it by name.

`skills/teamlead/roster.sh` is the skill's read of this surface — see its
header for the contract.

## Driving a Worker

```bash
herdr agent prompt <name> "<text>"          # text + Enter, bracketed paste honored
herdr agent prompt <name> "<text>" --wait   # wait for the first settled idle/done/blocked
herdr agent wait <name> [--until <state>] [--timeout <ms>]
herdr agent send-keys <name> esc            # also ctrl+c and other logical keys
herdr agent get <name>
herdr agent read <name> --source visible|recent|recent-unwrapped --lines <n>
```

`agent prompt` refuses with `agent_blocked` when the agent sits at an approval
or question dialog, before sending any input. A prompt sent from a non-working
state must produce an observed lifecycle change within 5 seconds, otherwise
Herdr returns `agent_prompt_stalled` rather than waiting forever.

## Why Reports Are Files

Claude Code and Grok render on the terminal's **alternate screen**. Rows that
leave it never enter Herdr's host scrollback, so a bigger `--lines` cannot
recover them. `herdr agent read` additionally fails with `agent_not_idle` while
the agent is working.

Every substantive worker output therefore travels through a REPORT FILE. Pane
reads are for lifecycle state and the last few lines — never for the work
product.

## State Flicker Is Real

Observed in the 2026-09-01 round:

- `herdr agent wait claude` returned `done` several times while Claude Code was
  mid-task, between tool calls and while streaming.
- Grok reported `working` while sitting idle at startup.

A single `idle` or `done` observation is therefore never completion. Completion
is the conjunction: the report file exists on disk AND the worker's final
message ends with the literal last line `REPORT: <path>`. The reliable wait
primitive is

```bash
herdr pane wait-output <pane-id> --regex 'REPORT: ' --source visible --lines 40 --timeout <ms>
```

followed by verifying the file. `skills/teamlead/wait-report.sh` does both;
its header carries the contract, its constants the poll interval and budget.

`pane wait-output` searches the existing snapshot before it polls, so a marker
left in the scrollback by an earlier round can satisfy the wait immediately.
Clear the worker's context between rounds and verify the file's content belongs
to the current round.

### A Stale `working` Is Not a Busy Worker

Herdr derives the lifecycle state from the agent's window title, and that title
goes stale. Grok keeps reporting `working` after a `/usage` dialog has been
opened and dismissed while it sits at an empty prompt; taken at face value that
refuses the agent forever.

The utility therefore confirms a `working` verdict against the pane before
acting on it. It reads
`herdr agent read <name> --source visible --lines 40` and decides from the
footer. Two invariants shape the probe:

- `blocked` is never probed. Herdr recognizing an approval dialog is a positive
  signal, not a stale one.
- The bias runs toward refusing. A false `working` costs a skipped round; a
  false `idle` types over somebody's work. Anything the probe cannot read
  confidently stays inconclusive, and inconclusive keeps the refusal.

Every measured record says which signal decided: `state_source` is `herdr` or
`probe`, and `herdr_state` carries what herdr claimed. A probe that overturns
herdr warns on stderr and the run continues.

The footer signatures live in the config, never in code — a CLI that restyles
its footer is a config edit. Two optional per-agent keys drive it:

| Key | Meaning |
| --- | ------- |
| `idle_markers` | Literal footer text meaning "at the prompt". Empty (the default) means never probe, and herdr's verdict stands |
| `working_markers` | Literal footer text meaning "mid-turn". Checked before `idle_markers` |

Both are literal substrings matched against the last non-empty footer rows.
Shipped defaults are in `skills/teamlead/config.example.json`; the precedence
order and the row count are in `skills/teamlead/teamlead/probe.py`.

## Reading Subscription Headroom

Each worker reports its own remaining budget through its native slash command,
sent as a prompt. Never send one to an agent that is `working` or `blocked` —
it would queue as a prompt and land in the middle of that agent's work.

| Kind | Command | Rendering | What it prints |
| ---- | ------- | --------- | -------------- |
| Claude Code | `/usage` | Full-screen dialog on the alternate screen; read with `--source visible`, close with `send-keys esc` | `Current session N% used`, `Current week (all models) N% used`, plus per-model weeks |
| Codex | `/status` | Box in the transcript | `Weekly limit: [...] N% left (resets ...)`, `5h limit: ... N% left`; a line like `GPT-5.3-Codex-Spark limit:` starts a secondary-model section, and the lines after it belong to that model |
| Grok | `/usage` | Two renderings — see below | `Weekly limit: N%` (used), plus `Credits: $X` |

**Grok has two renderings, and a restart can switch them.** On a fresh session
`/usage` prints inline text in the transcript (`Weekly limit: 0%`,
`Next reset:`, `Credits:`). After a restart it opens a boxed modal
(`Weekly limit (X Premium+)`, a bar row carrying the percentage, `Resets:`,
`Credits:`) that must be closed with Esc (`close_keys: ["esc"]`,
`usage_read_source: visible`). Both parse to the same `Weekly limit` window, so
nothing downstream cares which one appeared; the modal also reports a plan name,
carried as the informational `plan` field. An unclosed dialog leaves the pane
holding a modal, which flips the agent to `working` and swallows the next
prompt — the close keys go out even when the wait, the read, or the parse
failed.

Headroom is the **minimum** remaining percentage across an agent's windows. A
worker with 100% of its week and 2% of its five-hour window has 2% of headroom;
handing it the developer role stalls the round in ten minutes. Grok's credit
balance is informational and never feeds headroom — a dollar balance and a
percentage are not the same currency.

## Clearing Context

| Kind | Command |
| ---- | ------- |
| Claude Code | `/clear` |
| Codex | `/new` |
| Grok | `/new` |

Each is sent as a prompt. Wait for the agent to settle before sending the
brief, so the brief does not land in the clearing dialog.

## Worker Runtime Quirks

- **Grok does not follow includes.** It reads `AGENTS.md` and Claude's settings
  but does NOT follow the `@.tessl/RULES.md` include, so a Grok worker's brief
  must tell it to read `.tessl/RULES.md` and every rule file linked from it.
- **Same plugin, every worker.** All workers run from the shared checkout with
  the same tessl plugin installed, so the same hooks and skills load at session
  start and the same policy governs every role.
- **One GitHub account.** The workers usually share the operator's GitHub
  account, and GitHub refuses `APPROVE` / `REQUEST_CHANGES` on that account's
  own PR. Internal reviews are therefore COMMENT reviews, and the lead enforces
  the blocking findings itself.

## Parsing Note

Every number above comes from text an agent chose to draw on a terminal. A CLI
update that renames a window label or reorders a status box breaks the
corresponding parser. The utility fails loudly on an unreadable pane
(`ParseError`, the agent listed in `failed_agents`, exit 1) rather than
reporting a silently wrong number — treat a parse failure as "measure that one
by hand", never as "it has no headroom".
