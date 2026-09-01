"""Agent configuration: which agents exist and how each reports its usage.

The config is operator-owned. teamlead never writes it -- the operator copies
`config.example.json` into place, so a machine that has not been set up fails
with an actionable message instead of silently inventing agents.
"""

import json
import os
from pathlib import Path

from .errors import ConfigError
from .herdr import SLASH_DELIVERIES, SLASH_DELIVERY_PASTE

CONFIG_SCHEMA_VERSION = 1

REQUIRED_AGENT_FIELDS = ("name", "kind", "usage_prompt", "usage_marker", "usage_read_source", "clear_prompt")

VALID_READ_SOURCES = frozenset({"visible", "recent", "recent-unwrapped", "detection"})

#: Optional per-agent fields that default to an empty list of strings.
OPTIONAL_LIST_FIELDS = (
    "close_keys",
    "idle_markers",
    "working_markers",
    "dialog_next_tab_keys",
    "recover_keys",
)

#: Default when an agent does not name one. `paste` is the older path and the
#: one that works for claude and codex; grok needs `type`.
DEFAULT_SLASH_DELIVERY = SLASH_DELIVERY_PASTE


class Agent:
    """One configured agent. A plain value object, never mutated after load."""

    __slots__ = REQUIRED_AGENT_FIELDS + OPTIONAL_LIST_FIELDS + (
        "slash_delivery",
        "composer_glyph",
    )

    def __init__(self, name, kind, usage_prompt, usage_marker, usage_read_source, clear_prompt, close_keys=(), idle_markers=(), working_markers=(), dialog_next_tab_keys=(), recover_keys=(), slash_delivery=DEFAULT_SLASH_DELIVERY, composer_glyph=""):
        self.name = name
        self.kind = kind
        self.usage_prompt = usage_prompt
        self.usage_marker = usage_marker
        self.usage_read_source = usage_read_source
        self.clear_prompt = clear_prompt
        self.close_keys = tuple(close_keys)
        # Footer signatures the idle probe matches when herdr's title-derived
        # state says `working`. See teamlead/probe.py.
        self.idle_markers = tuple(idle_markers)
        self.working_markers = tuple(working_markers)
        # Keys that move a usage dialog to its next tab, when the report is
        # behind one teamlead did not land on.
        self.dialog_next_tab_keys = tuple(dialog_next_tab_keys)
        # Keys that clear a stuck composer. Sent EXACTLY once, never in a
        # loop: for Codex this is ctrl+c, and a second ctrl+c exits Codex.
        self.recover_keys = tuple(recover_keys)
        # "paste" (agent prompt) or "type" (pane send-text plus Enter).
        self.slash_delivery = slash_delivery
        # The prompt glyph that marks the composer row, so teamlead can see
        # whether a slash command was consumed. Empty disables the check.
        self.composer_glyph = composer_glyph

    def __repr__(self):
        return "Agent(name={!r}, kind={!r})".format(self.name, self.kind)

    def __eq__(self, other):
        if not isinstance(other, Agent):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def as_dict(self):
        record = {field: getattr(self, field) for field in REQUIRED_AGENT_FIELDS}
        for field in OPTIONAL_LIST_FIELDS:
            record[field] = list(getattr(self, field))
        record["slash_delivery"] = self.slash_delivery
        record["composer_glyph"] = self.composer_glyph
        return record


def default_config_path():
    """`$XDG_CONFIG_HOME/teamlead/config.json`, falling back to `~/.config`."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "teamlead" / "config.json"


def _missing_config_error(path):
    return ConfigError(
        "Agent config not found at {} - copy the shipped example into place "
        "with `mkdir -p {} && cp config.example.json {}`, then edit the agent "
        "names to match `herdr agent list`.".format(path, path.parent, path),
        {"path": str(path)},
    )


def parse_config(payload, source="<memory>"):
    """Validate an already-decoded config mapping and return a list of Agents.

    Pure apart from the `source` string used in error messages, so tests build
    configs inline instead of writing files.
    """
    if not isinstance(payload, dict):
        raise ConfigError(
            "Config at {} must be a JSON object with `schema_version` and "
            "`agents` - see config.example.json.".format(source),
            {"source": source},
        )

    version = payload.get("schema_version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            "Config at {} has schema_version {!r}; this build reads version {}. "
            "Update the file against config.example.json.".format(
                source, version, CONFIG_SCHEMA_VERSION
            ),
            {"source": source, "found": version, "expected": CONFIG_SCHEMA_VERSION},
        )

    raw_agents = payload.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise ConfigError(
            "Config at {} has no `agents` array - add at least one agent entry "
            "shaped like the ones in config.example.json.".format(source),
            {"source": source},
        )

    agents = []
    seen = set()
    for index, entry in enumerate(raw_agents):
        if not isinstance(entry, dict):
            raise ConfigError(
                "Config at {}: agents[{}] is not a JSON object - each agent is "
                "an object with the fields {}.".format(
                    source, index, ", ".join(REQUIRED_AGENT_FIELDS)
                ),
                {"source": source, "index": index},
            )
        missing = [field for field in REQUIRED_AGENT_FIELDS if not entry.get(field)]
        if missing:
            raise ConfigError(
                "Config at {}: agents[{}] is missing {} - add {} to that entry.".format(
                    source, index, ", ".join(missing), " and ".join(missing)
                ),
                {"source": source, "index": index, "missing": missing},
            )
        read_source = entry["usage_read_source"]
        if read_source not in VALID_READ_SOURCES:
            raise ConfigError(
                "Config at {}: agents[{}] has usage_read_source {!r}; herdr "
                "accepts {}.".format(
                    source, index, read_source, ", ".join(sorted(VALID_READ_SOURCES))
                ),
                {"source": source, "index": index, "usage_read_source": read_source},
            )
        name = entry["name"]
        if name in seen:
            raise ConfigError(
                "Config at {}: agent name {!r} appears twice - herdr agent "
                "names are unique among live agents.".format(source, name),
                {"source": source, "name": name},
            )
        seen.add(name)

        lists = {}
        for field in OPTIONAL_LIST_FIELDS:
            value = entry.get(field, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ConfigError(
                    "Config at {}: agents[{}].{} must be an array of strings - "
                    "close_keys holds herdr key names such as [\"esc\"], "
                    "idle_markers and working_markers hold literal footer text "
                    "the idle probe looks for.".format(source, index, field),
                    {"source": source, "index": index, "field": field},
                )
            lists[field] = value

        delivery = entry.get("slash_delivery", DEFAULT_SLASH_DELIVERY)
        if delivery not in SLASH_DELIVERIES:
            raise ConfigError(
                "Config at {}: agents[{}].slash_delivery is {!r}; use {}. "
                "\"paste\" sends the command with `herdr agent prompt`; "
                "\"type\" types it into the pane and presses Enter. Which one "
                "an agent needs depends on its TUI.".format(
                    source, index, delivery, " or ".join(repr(v) for v in SLASH_DELIVERIES)
                ),
                {"source": source, "index": index, "slash_delivery": delivery},
            )
        glyph = entry.get("composer_glyph", "")
        if not isinstance(glyph, str):
            raise ConfigError(
                "Config at {}: agents[{}].composer_glyph must be a string - the "
                "prompt glyph that starts the composer row, e.g. \"\u203a \" "
                "for codex. Omit it to skip the consumed-command check.".format(
                    source, index
                ),
                {"source": source, "index": index},
            )
        agents.append(
            Agent(
                name=name,
                kind=entry["kind"],
                usage_prompt=entry["usage_prompt"],
                usage_marker=entry["usage_marker"],
                usage_read_source=read_source,
                clear_prompt=entry["clear_prompt"],
                slash_delivery=delivery,
                composer_glyph=glyph,
                **lists
            )
        )
    return agents


def load_config(path):
    """Read and validate the config file at `path`."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _missing_config_error(path) from None
    except PermissionError as exc:
        raise ConfigError(
            "Agent config at {} is not readable: {} - fix its permissions "
            "with `chmod u+r {}`.".format(path, exc.strerror, path),
            {"path": str(path)},
        ) from None
    except IsADirectoryError:
        raise ConfigError(
            "Agent config path {} is a directory - point --config at the "
            "config.json file itself.".format(path),
            {"path": str(path)},
        ) from None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Agent config at {} is not valid JSON ({} at line {} column {}) - "
            "validate it with `python3 -m json.tool {}`.".format(
                path, exc.msg, exc.lineno, exc.colno, path
            ),
            {"path": str(path)},
        ) from None

    return parse_config(payload, source=str(path))


def select_agents(agents, names):
    """Return the subset of `agents` named in `names`, in config order.

    An empty/None `names` selects every agent. An unknown name is an error,
    never a silent no-op.
    """
    if not names:
        return list(agents)
    by_name = {agent.name: agent for agent in agents}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ConfigError(
            "No configured agent named {} - configured agents are {}. "
            "Check the name against `herdr agent list`.".format(
                ", ".join(repr(name) for name in unknown),
                ", ".join(sorted(by_name)),
            ),
            {"unknown": unknown, "configured": sorted(by_name)},
        )
    wanted = set(names)
    return [agent for agent in agents if agent.name in wanted]
