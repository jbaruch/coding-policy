"""Tests for teamlead.config."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

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
            "idle_markers": ["? for shortcuts"],
            "working_markers": ["Churned for"],
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

    def test_probe_markers_are_read_into_the_agent(self):
        agents = parse_config(VALID)
        self.assertEqual(agents[0].idle_markers, ("? for shortcuts",))
        self.assertEqual(agents[0].working_markers, ("Churned for",))

    def test_probe_markers_default_to_empty(self):
        # An agent with no markers is simply never probed; herdr's state stands.
        agents = parse_config(VALID)
        self.assertEqual(agents[1].idle_markers, ())
        self.assertEqual(agents[1].working_markers, ())

    def test_slash_delivery_defaults_to_paste(self):
        # The older path, and the one claude and codex need.
        self.assertEqual(parse_config(VALID)[0].slash_delivery, "paste")

    def test_slash_delivery_is_read_when_given(self):
        entry = json.loads(json.dumps(VALID))
        entry["agents"][0]["slash_delivery"] = "type"
        self.assertEqual(parse_config(entry)[0].slash_delivery, "type")

    def test_an_unknown_slash_delivery_is_a_config_error(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["slash_delivery"] = "sendkeys"
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        message = str(caught.exception)
        self.assertIn("slash_delivery", message)
        self.assertIn("'paste'", message)
        self.assertIn("'type'", message)

    def test_dialog_next_tab_keys_default_to_empty(self):
        self.assertEqual(parse_config(VALID)[0].dialog_next_tab_keys, ())

    def test_dialog_next_tab_keys_are_read_when_given(self):
        entry = json.loads(json.dumps(VALID))
        entry["agents"][0]["dialog_next_tab_keys"] = ["tab"]
        self.assertEqual(parse_config(entry)[0].dialog_next_tab_keys, ("tab",))

    def test_dialog_next_tab_keys_must_be_an_array_of_strings(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["dialog_next_tab_keys"] = "tab"
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        self.assertIn("dialog_next_tab_keys", str(caught.exception))

    def test_idle_markers_must_be_an_array_of_strings(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["idle_markers"] = "? for shortcuts"
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        self.assertIn("idle_markers", str(caught.exception))

    def test_working_markers_reject_non_string_items(self):
        broken = json.loads(json.dumps(VALID))
        broken["agents"][0]["working_markers"] = [42]
        with self.assertRaises(ConfigError) as caught:
            parse_config(broken)
        self.assertIn("working_markers", str(caught.exception))

    def test_shipped_example_config_is_valid(self):
        payload = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))
        agents = parse_config(payload, source="config.example.json")
        self.assertEqual([agent.name for agent in agents], ["claude", "codex", "grok"])
        self.assertEqual([agent.kind for agent in agents], ["claude", "codex", "grok"])
        by_name = {agent.name: agent for agent in agents}
        # Grok renders /usage as a modal after a restart, so it reads the
        # viewport and dismisses the dialog just like claude does.
        self.assertEqual(by_name["grok"].usage_read_source, "visible")
        self.assertEqual(by_name["grok"].close_keys, ("esc",))
        # Every shipped agent carries an idle signature for the probe.
        self.assertTrue(all(agent.idle_markers for agent in agents))
        # Delivery is per-agent because the TUIs disagree: grok reads a pasted
        # slash command as a chat message, codex swallows a typed one in its
        # autocomplete popup.
        self.assertEqual(
            {agent.name: agent.slash_delivery for agent in agents},
            {"claude": "paste", "codex": "type", "grok": "type"},
        )
        # Every agent can be checked for a stuck composer.
        self.assertTrue(all(agent.composer_glyph for agent in agents))
        self.assertEqual(by_name["codex"].composer_glyph, "\u203a ")
        # Codex ships with NO recovery keys: the key that clears its composer
        # is ctrl+c, and ctrl+c on an idle Codex exits the process. That is
        # how a live agent was killed.
        self.assertEqual(by_name["codex"].recover_keys, ())
        # And its empty-composer placeholder is declared, so the hint text is
        # never read as somebody's typing.
        self.assertEqual(
            by_name["codex"].composer_placeholders, ("Ask Codex to do anything",)
        )
        # Dim composer text is never typing, on every kind.
        self.assertTrue(all(agent.composer_ignore_dim for agent in agents))
        self.assertEqual(by_name["grok"].dialog_next_tab_keys, ("tab",))

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
