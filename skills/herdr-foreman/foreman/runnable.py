"""The runnable form of this package's commands (#532).

No installed plugin puts a `foreman` executable on `PATH`: the package runs
through the skill's `foreman.sh` launcher, and a Tessl install strips its
execute bit. A hint that names a bare `foreman measure`, or a bare
`supervision-bind`, does not run as written. Every error, help and resume hint
that tells the reader to run a subcommand renders it through `command()`.

Contract: `launcher()` returns the absolute path of the `foreman.sh` beside
this package; `command(tail)` returns `bash <quoted launcher> <tail>`. The
tail is inserted verbatim, so a caller quotes any value it interpolates and
may keep placeholders such as `FILE` or `<path>` for the reader to fill in.
"""

import shlex
from pathlib import Path


def launcher():
    """The installed launcher that runs this package's commands."""
    return str(Path(__file__).resolve().parents[1] / "foreman.sh")


def command(tail):
    """The runnable command line for one subcommand and its arguments."""
    return "bash {} {}".format(shlex.quote(launcher()), tail)
