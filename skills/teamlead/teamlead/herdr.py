"""Transport for the `herdr` CLI.

Two halves, deliberately split:

* **argv builders** are pure functions from arguments to an argv list. They are
  what `apply --dry-run` prints, so the commands shown are the same lists the
  live path would execute -- there is no second, drifting copy of the command
  shapes.
* **executions** run one of those argv lists through an injected `runner`.
  Every caller in this package receives a client rather than importing one, so
  tests hand in a fake runner and never touch the real binary.

Herdr's I/O contract, from `herdr --skill`:

* control commands return JSON on stdout
* `agent read` returns raw pane text on stdout, not JSON
* server errors are JSON on stderr with exit status 1
* CLI syntax errors exit with status 2

Pass a `trace` sink to record every invocation -- argv, exit status, and raw
stdout and stderr -- so a live run that goes wrong is diagnosable without
guessing at what teamlead sent. `--trace` or `TEAMLEAD_TRACE=1` turns it on.
"""

import json
import os
import shlex
import subprocess

from .errors import HerdrError

#: Default per-call wall-clock ceiling for the subprocess itself. Herdr's own
#: --timeout governs how long *it* waits; this guards against a wedged binary.
SUBPROCESS_TIMEOUT_SEC = 300

#: How long to wait for a usage report's marker to appear after sending the
#: slash command. Bounded on purpose: a missing marker fails loudly.
DEFAULT_MARKER_TIMEOUT_MS = 20000

#: How long to wait for an agent to settle back to idle after `/clear`.
DEFAULT_SETTLE_TIMEOUT_MS = 60000

#: Agent lifecycle states that mean "ready for input".
READY_STATES = frozenset({"idle", "done"})

#: Agent lifecycle states teamlead refuses to write to without --force.
BUSY_STATES = frozenset({"working", "blocked"})


#: Environment switch for tracing, equivalent to the `--trace` flag.
TRACE_ENV_VAR = "TEAMLEAD_TRACE"


def default_binary():
    """The herdr executable to invoke, overridable for an unusual install."""
    return os.environ.get("TEAMLEAD_HERDR_BIN", "herdr")


def trace_enabled_in_env(environ=None):
    """True when TEAMLEAD_TRACE is set to something other than 0 or empty."""
    value = (environ if environ is not None else os.environ).get(TRACE_ENV_VAR, "")
    return value.strip() not in ("", "0")


def subprocess_runner(argv):
    """Run `argv` and return the CompletedProcess. The only impure code here."""
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=SUBPROCESS_TIMEOUT_SEC,
        check=False,
        text=True,
    )


def format_argv(argv):
    """Render an argv list as a copy-pasteable shell command."""
    return shlex.join(argv)


class HerdrClient:
    """A thin, injectable wrapper over the `herdr` CLI."""

    def __init__(self, binary=None, runner=None, trace=None):
        self.binary = binary or default_binary()
        self._runner = runner if runner is not None else subprocess_runner
        # A callable taking one string, or None for no tracing. Diagnostics go
        # to stderr so stdout stays machine-readable JSON.
        self._trace = trace

    # -- argv builders (pure) ------------------------------------------------

    def argv_agent_get(self, name):
        return [self.binary, "agent", "get", name]

    def argv_agent_list(self):
        return [self.binary, "agent", "list"]

    def argv_agent_read(self, name, source=None, lines=None):
        argv = [self.binary, "agent", "read", name]
        if source:
            argv += ["--source", source]
        if lines is not None:
            argv += ["--lines", str(lines)]
        return argv

    def argv_agent_prompt(self, name, text, wait=False, until=(), timeout_ms=None):
        argv = [self.binary, "agent", "prompt", name, text]
        if wait:
            argv.append("--wait")
        for state in until:
            argv += ["--until", state]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        return argv

    def argv_agent_send_keys(self, name, keys):
        return [self.binary, "agent", "send-keys", name] + list(keys)

    def argv_agent_wait(self, name, until=(), timeout_ms=None):
        argv = [self.binary, "agent", "wait", name]
        for state in until:
            argv += ["--until", state]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        return argv

    def argv_pane_wait_output(self, pane_id, regex=None, match=None, source=None, lines=None, timeout_ms=None):
        if (regex is None) == (match is None):
            raise HerdrError(
                "pane wait-output needs exactly one of --regex or --match - "
                "pass a marker pattern for the agent's usage report.",
                {"pane_id": pane_id},
            )
        argv = [self.binary, "pane", "wait-output"]
        if regex is not None:
            argv += ["--regex", regex]
        else:
            argv += ["--match", match]
        if source:
            argv += ["--source", source]
        if lines is not None:
            argv += ["--lines", str(lines)]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        argv.append(pane_id)
        return argv

    # -- execution -----------------------------------------------------------

    def _emit_trace(self, argv, completed=None, outcome=None):
        """Record one invocation on the trace sink, if there is one."""
        if self._trace is None:
            return
        if completed is None:
            self._trace("herdr> {}{}".format(format_argv(argv), outcome or ""))
            return
        self._trace(
            "herdr> {}\nherdr< exit={} stdout={!r} stderr={!r}".format(
                format_argv(argv),
                completed.returncode,
                completed.stdout or "",
                completed.stderr or "",
            )
        )

    def _run(self, argv):
        """Execute `argv`, raising HerdrError on anything but a clean exit."""
        try:
            completed = self._runner(argv)
        except FileNotFoundError:
            self._emit_trace(argv, outcome="\nherdr< executable not found")
            raise HerdrError(
                "herdr executable {!r} not found on PATH - install Herdr or set "
                "TEAMLEAD_HERDR_BIN to its absolute path.".format(self.binary),
                {"command": format_argv(argv)},
            ) from None
        except subprocess.TimeoutExpired:
            self._emit_trace(argv, outcome="\nherdr< subprocess timeout")
            raise HerdrError(
                "herdr did not return within {}s running `{}` - check the pane "
                "with `herdr agent list`.".format(SUBPROCESS_TIMEOUT_SEC, format_argv(argv)),
                {"command": format_argv(argv)},
            ) from None

        self._emit_trace(argv, completed)

        if completed.returncode != 0:
            raise HerdrError(
                self._failure_message(argv, completed),
                {
                    "command": format_argv(argv),
                    "exit_code": completed.returncode,
                    "stderr": (completed.stderr or "").strip(),
                },
            )
        return completed.stdout or ""

    @staticmethod
    def _failure_message(argv, completed):
        stderr = (completed.stderr or "").strip()
        detail = stderr
        # Herdr reports server errors as JSON on stderr; surface its own code
        # and message when it gave us one, and the raw text when it did not.
        if stderr.startswith("{"):
            try:
                payload = json.loads(stderr)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                error = payload["error"]
                detail = "{}: {}".format(
                    error.get("code", "unknown"), error.get("message", stderr)
                )
        if completed.returncode == 2:
            return (
                "herdr rejected the command `{}` as a syntax error ({}) - this "
                "build of teamlead may not match the installed herdr; check "
                "`herdr agent --help`.".format(format_argv(argv), detail or "no detail")
            )
        return "herdr failed running `{}`: {}".format(argv and format_argv(argv), detail or "no detail")

    def _run_json(self, argv):
        stdout = self._run(argv)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise HerdrError(
                "herdr returned non-JSON output for `{}` - expected a JSON "
                "control response. Got: {!r}".format(format_argv(argv), stdout[:200]),
                {"command": format_argv(argv)},
            ) from None
        if not isinstance(payload, dict) or "result" not in payload:
            raise HerdrError(
                "herdr returned JSON without a `result` field for `{}` - got "
                "keys {}.".format(
                    format_argv(argv),
                    sorted(payload) if isinstance(payload, dict) else type(payload).__name__,
                ),
                {"command": format_argv(argv)},
            )
        return payload["result"]

    # -- operations ----------------------------------------------------------

    def agent_get(self, name):
        """Return the agent record for `name` (pane_id, agent_status, ...)."""
        result = self._run_json(self.argv_agent_get(name))
        agent = result.get("agent")
        if not isinstance(agent, dict):
            raise HerdrError(
                "herdr agent get {} returned no agent record - confirm the name "
                "with `herdr agent list`.".format(name),
                {"agent": name},
            )
        return agent

    def agent_list(self):
        """Return every live agent record."""
        result = self._run_json(self.argv_agent_list())
        agents = result.get("agents")
        if not isinstance(agents, list):
            raise HerdrError(
                "herdr agent list returned no agents array - check that the "
                "Herdr server is running.",
                {},
            )
        return agents

    def agent_read(self, name, source=None, lines=None):
        """Return raw pane text. `agent read` is the one non-JSON command."""
        return self._run(self.argv_agent_read(name, source=source, lines=lines))

    def agent_prompt(self, name, text, wait=False, until=(), timeout_ms=None):
        """Submit a prompt. Writes to the agent -- callers gate on status first."""
        return self._run_json(
            self.argv_agent_prompt(name, text, wait=wait, until=until, timeout_ms=timeout_ms)
        )

    def agent_send_keys(self, name, keys):
        """Send logical key names (`esc`, `ctrl+c`) to the agent."""
        return self._run_json(self.argv_agent_send_keys(name, keys))

    def agent_wait(self, name, until=(), timeout_ms=None):
        """Block until the agent reaches one of `until` (default: settled)."""
        return self._run_json(self.argv_agent_wait(name, until=until, timeout_ms=timeout_ms))

    def pane_wait_output(self, pane_id, regex=None, match=None, source=None, lines=None, timeout_ms=None):
        """Block until the pane snapshot contains the marker, or time out."""
        return self._run_json(
            self.argv_pane_wait_output(
                pane_id, regex=regex, match=match, source=source, lines=lines, timeout_ms=timeout_ms
            )
        )
