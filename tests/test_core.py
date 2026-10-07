"""Core helpers: registries, config, matching, transcript parsing."""
import json
import os
import unittest
from pathlib import Path

from _util import EnvPatch, make_env

from grounding_gates import core


class TestRegistries(unittest.TestCase):
    def setUp(self):
        env, self.tmp = make_env({})
        self.patch = EnvPatch(env).__enter__()

    def tearDown(self):
        self.patch.__exit__()

    def test_registry_roundtrip(self):
        core.save_registry("tool_routing", {"routes": [{"job": "x"}]})
        self.assertEqual(core.load_registry("tool_routing")["routes"][0]["job"], "x")

    def test_missing_registry_is_empty(self):
        self.assertEqual(core.load_registry("nope-not-here"), {})

    def test_corrupt_registry_is_empty(self):
        (self.tmp / "answers.json").write_text("{not json")
        self.assertEqual(core.load_registry("answers"), {})

    def test_jsonl_roundtrip_skips_bad_lines(self):
        core.append_jsonl("completions", {"task": "quarterly report export", "date": "2026-01-12"})
        with open(self.tmp / "completions.jsonl", "a") as fh:
            fh.write("garbage\n")
        rows = core.read_jsonl("completions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[-1]["task"], "quarterly report export")

    def test_session_state_roundtrip(self):
        core.save_session_state("s/1:x", {"claims_fired": 1})
        self.assertEqual(core.session_state("s/1:x")["claims_fired"], 1)

    def test_data_dir_defaults_under_xdg_config_home(self):
        saved = os.environ.pop("GROUNDING_GATES_DATA")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        try:
            self.assertEqual(core.data_dir(), self.tmp / "xdg" / "grounding-gates")
        finally:
            os.environ.pop("XDG_CONFIG_HOME")
            os.environ["GROUNDING_GATES_DATA"] = saved


class TestConfig(unittest.TestCase):
    def setUp(self):
        env, self.tmp = make_env({}, config={"claims": {"mode": "block"}, "factual": {"mode": "off"}})
        self.patch = EnvPatch(env).__enter__()

    def tearDown(self):
        self.patch.__exit__()

    def test_user_config_overrides_defaults(self):
        self.assertEqual(core.gate_settings("claims")["mode"], "block")
        self.assertEqual(core.gate_settings("claims")["max_per_session"], 2)

    def test_mode_off_disables(self):
        self.assertFalse(core.gate_enabled("factual"))
        self.assertTrue(core.gate_enabled("claims"))

    def test_global_off_switch(self):
        os.environ["GROUNDING_GATES_OFF"] = "1"
        try:
            self.assertFalse(core.gate_enabled("claims"))
        finally:
            del os.environ["GROUNDING_GATES_OFF"]

    def test_per_gate_skip_env(self):
        os.environ["GROUNDING_GATES_SKIP"] = "claims, option-loop"
        try:
            self.assertFalse(core.gate_enabled("claims"))
            self.assertFalse(core.gate_enabled("option-loop"))
            self.assertTrue(core.gate_enabled("completion"))
        finally:
            del os.environ["GROUNDING_GATES_SKIP"]

    def test_skip_file_in_cwd(self):
        (self.tmp / core.SKIP_FILE).touch()
        self.assertFalse(core.gate_enabled("completion", str(self.tmp)))


class TestMatching(unittest.TestCase):
    ANSWERS = [
        {"q": "did you publish the quarterly report to the shared drive", "a": "yes, uploaded Monday",
         "keywords": ["quarterly", "report", "publish", "drive"]},
        {"q": "which payment provider should the billing service use", "a": "the existing provider, keep it",
         "keywords": ["payment", "provider", "billing", "existing"]},
    ]

    def test_similar_question_matches(self):
        hit = core.match_question("Did you publish the quarterly report?", self.ANSWERS)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["a"], "yes, uploaded Monday")

    def test_unrelated_question_misses(self):
        self.assertIsNone(core.match_question("What color for the dashboard header?", self.ANSWERS))

    def test_empty_question_misses(self):
        self.assertIsNone(core.match_question("   ", self.ANSWERS))

    def test_keyword_score(self):
        self.assertEqual(core.keyword_score("publish the quarterly REPORT now", ["quarterly", "report", "fax"]), 2)

    def test_keywords_for_skips_filler(self):
        kws = core.keywords_for("Which budget ceiling applies for the laptop purchase?")
        self.assertIn("budget", kws)
        self.assertNotIn("which", kws)


class TestTranscripts(unittest.TestCase):
    def test_nested_and_flat_rows(self):
        text = "\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
            json.dumps({"role": "assistant", "content": [{"type": "text", "text": "hello"}]}),
            "not json",
        ])
        rows = core.parse_rows(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(core.text_turns(rows), [("user", "hi"), ("assistant", "hello")])

    def test_tool_result_is_not_a_human_turn(self):
        rows = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "listed"}]}},
        ]
        self.assertEqual(len(core.current_turn_rows(rows)), 3)
        self.assertEqual(core.last_assistant_text(rows), "listed")

    def test_missing_transcript_reads_as_none(self):
        self.assertIsNone(core.read_transcript(None))
        self.assertIsNone(core.read_transcript(str(Path("/nonexistent/x.jsonl"))))


if __name__ == "__main__":
    unittest.main()
