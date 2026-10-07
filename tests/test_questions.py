"""Question gate (dedupe + craft lint) and answer capture."""
import json
import unittest

from _util import make_env, run_hook

SEED = {
    "answers": {"answers": [
        {"q": "should the dashboard use the dark starfield theme or plain",
         "a": "starfield but restrained, sophisticated over flashy", "date": "2026-01-01",
         "keywords": ["dashboard", "starfield", "theme", "plain"]},
    ]},
}
OPTS = [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]


def ask(questions, env):
    return run_hook("question", {"tool_name": "AskUserQuestion",
                                 "tool_input": {"questions": questions}, "session_id": "t-q"}, env)


class TestQuestionGate(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env(dict(SEED))

    def test_answered_question_denied_with_answer(self):
        r = ask([{"question": "Should the dashboard use the dark starfield theme or plain styling?",
                  "options": OPTS}], self.env)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertIn("ALREADY answered", out["permissionDecisionReason"])
        self.assertIn("sophisticated over flashy", out["permissionDecisionReason"])

    def test_exempt_marker_skips_dedupe(self):
        env, _ = make_env(dict(SEED), config={"question": {"exempt_markers": ["did you send"]}})
        r = ask([{"question": "Did you send the starfield dashboard theme mockup, or plain?",
                  "options": OPTS}], env)
        self.assertEqual(r.stdout.strip(), "")

    def test_lazy_question_bounced(self):
        r = ask([{"question": "Thoughts?", "options": OPTS}], self.env)
        self.assertIn("QUESTION CRAFT", r.stdout)
        r2 = ask([{"question": "Which retry policy should the sync job use for failed sends?"}], self.env)
        self.assertIn("QUESTION CRAFT", r2.stdout)

    def test_craft_lint_can_be_disabled(self):
        env, _ = make_env(dict(SEED), config={"question": {"craft_lint": False}})
        r = ask([{"question": "Thoughts?"}], env)
        self.assertEqual(r.stdout.strip(), "")

    def test_novel_wellformed_question_passes(self):
        r = ask([{"question": "Which retry policy should the sync job use for failed webhook sends?",
                  "options": OPTS}], self.env)
        self.assertEqual(r.stdout.strip(), "")

    def test_no_questions_is_silent(self):
        self.assertEqual(ask([], self.env).stdout.strip(), "")


class TestAnswerCapture(unittest.TestCase):
    Q = "Which budget ceiling applies for the replacement laptop purchase?"

    def test_capture_appends_with_keywords(self):
        env, tmp = make_env({"answers": {"answers": []}})
        r = run_hook("answer-capture", {
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": self.Q, "options": OPTS}]},
            "tool_response": {"answers": {self.Q: "max $1,000; it only needs to last a year"}},
        }, env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        reg = json.loads((tmp / "answers.json").read_text())
        self.assertEqual(len(reg["answers"]), 1)
        row = reg["answers"][0]
        self.assertIn("1,000", row["a"])
        self.assertIn("budget", row["keywords"])

    def test_string_response_is_captured(self):
        env, tmp = make_env({})
        run_hook("answer-capture", {"tool_input": {"questions": [{"question": self.Q}]},
                                    "tool_response": "max $800"}, env)
        reg = json.loads((tmp / "answers.json").read_text())
        self.assertEqual(reg["answers"][0]["a"], "max $800")

    def test_duplicate_not_recaptured(self):
        env, tmp = make_env({"answers": {"answers": [
            {"q": self.Q, "a": "max $1,000", "date": "2026-01-15",
             "keywords": ["budget", "ceiling", "laptop", "purchase"]}]}})
        run_hook("answer-capture", {"tool_input": {"questions": [{"question": self.Q}]},
                                    "tool_response": {"answers": {self.Q: "max $1,000"}}}, env)
        reg = json.loads((tmp / "answers.json").read_text())
        self.assertEqual(len(reg["answers"]), 1)


if __name__ == "__main__":
    unittest.main()
