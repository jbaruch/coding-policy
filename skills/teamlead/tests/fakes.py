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
        return self.responses[best]

    def commands(self):
        """Every call as a shell string, binary dropped."""
        return [shlex.join(argv[1:]) for argv in self.calls]

    def writes(self):
        """Only the calls that write to an agent: prompt and send-keys."""
        return [
            command
            for command in self.commands()
            if command.startswith("agent prompt ") or command.startswith("agent send-keys ")
        ]


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
