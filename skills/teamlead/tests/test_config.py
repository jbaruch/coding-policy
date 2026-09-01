"""Tests for teamlead.config."""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from teamlead.config import (
    Agent,
    default_config_path,
    load_config,
    parse_config,
    select_agents,
)
from teamlead.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID = {
    "schema_version": 1,
    "agents": [
        {
            "name": "claude",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "close_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "clear_prompt": "/new",
        },
    ],
}


class ParseConfigTest(unittest.TestCase):
    def test_parses_a_valid_config(self):
        agents = parse_config(VALID)
        self.assertEqual([agent.name for agent in agents], ["claude", "codex"])
        self.assertEqual(agents[0].close_keys, ("esc",))
        self.assertEqual(agents[1].close_keys, ())

    def test_shipped_example_config_is_valid(self):
        payload = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))
        agents = parse_config(payload, source="config.example.json")
        self.assertEqual([agent.name for agent in agents], ["claude", "codex", "grok"])
        self.assertEqual([agent.kind for agent in agents], ["claude", "codex", "grok"])

    def test_wrong_schema_version_is_rejected(self):
        payload = dict(VALID, schema_version=2)
        with self.assertRaises(ConfigError) as caught:
            parse_config(payload)
        self.assertIn("schema_version", str(caught.exception))

    def test_missing_agents_array_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config({"schema_version": 1})

    def test_empty_agents_array_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config({"schema_version": 1, "agents": []})

    def test_missing_field_names_the_field(self):
        broken = json.loads(json.dumps(VALID))
        del broken["agents"][0]["clear_prompt"]
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        self.assertIn("clear_prompt", str(caught.exception))

    def test_bad_read_source_is_rejected(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["usage_read_source"] = "screenshot"
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        self.assertIn("recent-unwrapped", str(caught.exception))

    def test_duplicate_names_are_rejected(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][1]["name"] = "claude"
        with self.assertRaises(ConfigError):
            parse_config(broken)

    def test_close_keys_must_be_an_array(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["close_keys"] = "esc"
        with self.assertRaises(ConfigError):
            parse_config(broken)

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config([VALID])


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-config-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_loads_from_disk(self):
        path = Path(self.tmp) / "config.json"
        path.write_text(json.dumps(VALID), encoding="utf-8")
        self.assertEqual([agent.name for agent in load_config(path)], ["claude", "codex"])

    def test_missing_file_error_tells_the_operator_what_to_run(self):
        path = Path(self.tmp) / "nope" / "config.json"
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        message = str(caught.exception)
        self.assertIn("config.example.json", message)
        self.assertIn(str(path), message)

    def test_malformed_json_error_names_the_validator(self):
        path = Path(self.tmp) / "config.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        self.assertIn("json.tool", str(caught.exception))

    def test_directory_instead_of_file_is_rejected(self):
        path = Path(self.tmp) / "adir"
        path.mkdir()
        with self.assertRaises(ConfigError):
            load_config(path)


class DefaultPathTest(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("XDG_CONFIG_HOME")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.original is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.original

    def test_honours_xdg_config_home(self):
        os.environ["XDG_CONFIG_HOME"] = "/somewhere/cfg"
        self.assertEqual(default_config_path(), Path("/somewhere/cfg/teamlead/config.json"))

    def test_falls_back_to_dot_config(self):
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.assertEqual(
            default_config_path(), Path.home() / ".config" / "teamlead" / "config.json"
        )


class SelectAgentsTest(unittest.TestCase):
    def setUp(self):
        self.agents = parse_config(VALID)

    def test_no_names_selects_everything_in_config_order(self):
        self.assertEqual([a.name for a in select_agents(self.agents, None)], ["claude", "codex"])
        self.assertEqual([a.name for a in select_agents(self.agents, [])], ["claude", "codex"])

    def test_named_subset_keeps_config_order(self):
        self.assertEqual([a.name for a in select_agents(self.agents, ["codex"])], ["codex"])
        selected = select_agents(self.agents, ["codex", "claude"])
        self.assertEqual([a.name for a in selected], ["claude", "codex"])

    def test_unknown_name_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ConfigError) as caught:
            select_agents(self.agents, ["grok"])
        self.assertIn("grok", str(caught.exception))


class AgentValueObjectTest(unittest.TestCase):
    def test_as_dict_round_trips_through_parse_config(self):
        agent = parse_config(VALID)[0]
        rebuilt = parse_config({"schema_version": 1, "agents": [agent.as_dict()]})[0]
        self.assertEqual(agent, rebuilt)

    def test_equality_is_by_value(self):
        self.assertNotEqual(parse_config(VALID)[0], parse_config(VALID)[1])
        self.assertEqual(
            Agent("a", "claude", "/usage", "m", "visible", "/clear"),
            Agent("a", "claude", "/usage", "m", "visible", "/clear"),
        )


if __name__ == "__main__":
    unittest.main()
