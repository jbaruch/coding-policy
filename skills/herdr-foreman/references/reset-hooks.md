# Native Reset Input Gate

`hooks/herdr-reset-input.sh` adapts native `UserPromptSubmit` JSON to
`foreman/reset_input_hook.py`. Claude Code and Codex declare the same quoted
command in the plugin manifest. Grok loads the Claude-compatible declaration;
it has no separate verifier. Ordinary prompts produce no output or owner writes.
An invalid planned reset produces `decision: block` before model execution.
Claude's complete matching native long-paste frame is removed before exact
input comparison; mismatched frames and extra inner content remain refused.

The input envelope is a locator, not authority. The claimed deliverer first
saves its original foreground pins in the reset owner's schema-3 row. The
hook verifies that row, its live child, the unchanged stow and complete prompt,
and the native payload's new session. It consumes the old supervision binding
under its owner lock, preserves members/events/holds, and saves acceptance on
the reset row. Replay cannot consume the original binding twice. The deliverer
requires that acceptance and the matching new binding before reporting success.
Only a verified input receives native `additionalContext` authenticating the
planned continuation. It preserves the saved user scope, questions and holds;
it grants no new task authority. This distinguishes a proved machine handoff
from arbitrary pasted instructions without weakening the model's trust boundary.
The receipt proves landing when a runtime clips its transcript. A started turn
is still required; receipt acceptance alone never establishes healthy execution.

Codex's integration can retain its old session hint until the first real prompt.
Its foreman reset uses `/clear` to remain in the same checkout; a legacy `/new`
setting is translated on the reset's copied mechanics, not written to config.
No checkout picker or arbitrary modal response is automated.
The deliverer permits that hint only for the planned continuation, under the
unchanged foreground pins. The pre-prompt hook still refuses an old native
session. Claude/Grok keep their eager-session check before input and use the
same final hook gate. Unknown sends remain interrupted and are never repeated.
After clearing, all runtimes must establish stable idle under the original
process pins. That bounded read-only wait handles startup hooks after a stale
done observation; it never waits for an identity event that requires a prompt.

## Native validation

Use only an owned named Herdr test server and test owner files; never a focused
session or production owner state. Read the installed `herdr --skill` first.
Pass the adapter through the runtime's supported hook configuration. The Grok
configuration inspection must show its Claude-compatible `user_prompt_submit`
command as enabled. Preserve the plugin's quoted command strings.

For each supported runtime:

- Submit a prompt ending with `Herdr reset input receipt: {}` on its own line.
  The native UI must show the reset verifier's blocking reason and perform no
  model/tool work. A lifecycle wait alone does not prove this; read the pane.
- For a healthy round boundary, bind the fixture's native session, save a stow
  with unchanged required files, and run the normal reset owner command from
  that fixture's actual tool turn. Observe the loaded child's durable claim,
  the clear, one real continuation, a new native binding, the row's acceptance
  and delivered result, and the resumed foreman's saved-memory read.
- Lose one preclaim child in the existing deterministic subprocess fixture.
  The owner must retain its loss and recover within its startup allowance;
  only the claimed child may deliver. Claimed/uncertain input never retries.
- Replay the exact continuation: the hook blocks it and preserves the current
  binding. Verify changed stows and replacement process identities refuse too.
- Close only the owned fixture surfaces and stop only its named test server.

Unit and subprocess coverage lives in `tests/test_reset_input_hook.py` and
`tests/test_foreman_reset.py`; the full repository runner discovers both.
Native validation supplements those deterministic tests, never replaces them.
