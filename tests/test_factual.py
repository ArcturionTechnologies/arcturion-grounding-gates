"""Verify before asserting: prompt reminder + Stop-time audit."""
import json
import unittest

from _util import make_env, run_hook, text_row, write_transcript

from grounding_gates import factual


class FactualReminder(unittest.TestCase):
    def test_injects_rule_for_nonempty_prompt(self):
        v = factual.reminder("Recommend a tool.")
        self.assertEqual(v.action, "inject")
        self.assertIn("primary source", v.message)

    def test_empty_prompt_is_silent(self):
        self.assertFalse(factual.reminder("  ").fired)

    def test_hook_prints_plain_context(self):
        env, _ = make_env({})
        r = run_hook("factual-reminder", {"prompt": "Which database should I pick?"}, env)
        self.assertIn("VERIFY BEFORE ASSERTING", r.stdout)


class FactualAudit(unittest.TestCase):
    def test_confident_recommendation_without_hedge_warns(self):
        warn, note = factual.audit_check("I recommend the premium plan; it offers a free migration.")
        self.assertTrue(warn)
        self.assertIn("does not block", note)

    def test_citation_satisfies(self):
        self.assertFalse(factual.audit_check(
            "I recommend the premium plan (source: https://example.com/pricing).")[0])

    def test_hedge_satisfies(self):
        self.assertFalse(factual.audit_check(
            "I recommend the premium plan, but I haven't verified the migration offer.")[0])

    def test_plain_text_passes(self):
        self.assertFalse(factual.audit_check("Here is the summary you asked for.")[0])

    def test_hook_is_advisory_only(self):
        env, tmp = make_env({})
        path = write_transcript(tmp / "t.jsonl", [
            text_row("user", "what now?"),
            text_row("assistant", "Next step: you should use the bulk import feature."),
        ])
        r = run_hook("factual", {"transcript_path": path, "session_id": "f", "cwd": str(tmp)}, env)
        out = json.loads(r.stdout)
        self.assertIn("systemMessage", out)
        self.assertNotIn("decision", out)


if __name__ == "__main__":
    unittest.main()
