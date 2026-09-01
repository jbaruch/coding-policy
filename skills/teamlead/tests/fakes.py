"""Test doubles shared by the transport, measure, and CLI tests.

`FakeRunner` is a scripted stand-in for `subprocess.run`: it matches a call by
the first few argv tokens, records every invocation, and returns a canned
result. Nothing here starts a process.
"""

import shlex


class FakeCompleted:
    """The subset of subprocess.CompletedProcess the transport reads."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """A scripted runner. Responses are keyed by an argv prefix.

    Keys are shell-quoted argv prefixes with the binary dropped, e.g.
    ``"agent get claude"``. The longest matching prefix wins, so a specific
    ``agent read claude --source visible`` beats a general ``agent read``.
    """

    def __init__(self, responses=None, raises=None):
        self.responses = dict(responses or {})
        self.raises = raises or {}
        self.calls = []

    def set(self, prefix, stdout="", returncode=0, stderr=""):
        self.responses[prefix] = FakeCompleted(returncode, stdout, stderr)
        return self

    def __call__(self, argv):
        self.calls.append(list(argv))
        joined = shlex.join(argv[1:])
        for prefix in sorted(self.raises, key=len, reverse=True):
            if joined.startswith(prefix):
                raise self.raises[prefix]
        best = None
        for prefix in self.responses:
            if joined.startswith(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            raise AssertionError(
                "FakeRunner has no scripted response for: {}\nscripted: {}".format(
                    joined, sorted(self.responses)
                )
            )
        response = self.responses[best]
        if isinstance(response, ScriptedReads):
            return response.next_completed()
        return response

    def commands(self):
        """Every call as a shell string, binary dropped."""
        return [shlex.join(argv[1:]) for argv in self.calls]

    #: Every argv prefix that puts bytes into somebody's terminal.
    WRITE_PREFIXES = (
        "agent prompt ",
        "agent send-keys ",
        "pane send-text ",
        "pane send-keys ",
        "pane run ",
    )

    def writes(self):
        """Only the calls that write to an agent's terminal."""
        return [
            command
            for command in self.commands()
            if command.startswith(self.WRITE_PREFIXES)
        ]

    def pasted_prompts(self):
        """Text delivered through `agent prompt`, which pastes it.

        A slash command must never appear here: pasted into a TUI with
        bracketed paste on, it lands as a chat message rather than a command.
        """
        return [
            command[len("agent prompt ") :]
            for command in self.commands()
            if command.startswith("agent prompt ")
        ]


class ScriptedReads:
    """A response that yields a different stdout on each successive call.

    Stands in for a pane that takes a few reads to finish painting. The last
    entry repeats once the script runs out, so an over-long poll is stable.
    The runner materializes one FakeCompleted per call, so reading `.stdout`
    twice within a call (tracing does) never advances the script.
    """

    def __init__(self, texts):
        self._texts = list(texts)
        self._index = 0

    def next_completed(self):
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        return FakeCompleted(0, text, "")


def agent_json(name, status, pane_id):
    """A minimal `herdr agent get` response body."""
    return (
        '{"id":"cli:agent:get","result":{"type":"agent_info","agent":'
        '{"agent":"%s","name":"%s","agent_status":"%s","pane_id":"%s",'
        '"workspace_id":"w1","tab_id":"w1:t1"}}}' % (name, name, status, pane_id)
    )


def ok_json(kind="ok"):
    """A generic successful control response."""
    return '{"id":"cli:test","result":{"type":"%s"}}' % kind
