"""Option-loop gate: catch a re-offered menu after the user already answered."""
import unittest

from _util import make_env, run_hook, write_transcript

MENU_A = (
    "Here are the options:\n"
    "1. Rebuild the pipeline with async workers\n"
    "2. Patch the existing cron job instead\n"
    "Which would you like to do?"
)
# Same content, reworded: still >= 0.6 overlap on significant words.
MENU_A_REWORDED = (
    "To recap, the choices are:\n"
    "1. Rebuild the pipeline using async workers\n"
    "2. Just patch the existing cron job\n"
    "Which one do you want?"
)
MENU_A_WITH_RECOMMENDATION = (
    "Here are the options:\n"
    "1. Rebuild the pipeline with async workers\n"
    "2. Patch the existing cron job instead\n"
    "I recommend option 1, it's more durable. Which would you like to do?"
)
MENU_B_UNRELATED = (
    "A couple of different options:\n"
    "1. Repaint the kitchen this weekend\n"
    "2. Replace the kitchen cabinets instead\n"
    "Which would you like to do?"
)
ORDINARY_QUESTION = "What time works best for the call tomorrow?"
# A numbered RECAP followed by an unrelated question in a separate paragraph.
SUMMARY = (
    "Here's what shipped:\n"
    "1. Fixed the retry bug in the sync job\n"
    "2. Deployed the updated config to prod\n"
    "\n"
    "Everything looks good on my end. Anything else you need?"
)
SUMMARY_REWORDED = (
    "Recap of what shipped:\n"
    "1. Fixed the retry bug in the sync job\n"
    "2. Deployed the updated config to production\n"
    "\n"
    "That's everything for this session. Anything else you need?"
)


def turn(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


def loop(reply, final=MENU_A_REWORDED, first=MENU_A):
    return [turn("assistant", "Starting."), turn("assistant", first),
            turn("user", reply), turn("assistant", final)]


class Base(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env({})
        self.transcript = self.tmp / "transcript.jsonl"

    def payload(self, **extra):
        return {"cwd": str(self.tmp), "transcript_path": str(self.transcript),
                "session_id": "test-session", **extra}

    def run_turns(self, turns, env=None, **extra):
        write_transcript(self.transcript, turns)
        r = run_hook("option-loop", self.payload(**extra), env or self.env)
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def assert_blocks(self, turns, **kw):
        self.assertIn('"decision": "block"', self.run_turns(turns, **kw))

    def assert_passes(self, turns, **kw):
        self.assertEqual(self.run_turns(turns, **kw).strip(), "")


class OptionLoopGate(Base):
    def test_true_loop_blocks(self):
        out = self.run_turns(loop("I'm not sure, what do you think?"))
        self.assertIn('"decision": "block"', out)
        self.assertIn("OPTION LOOP", out)
        self.assertIn("option_loop", (self.tmp / "gates.log").read_text())

    def test_first_time_menu_passes(self):
        self.assert_passes([turn("assistant", "Starting the task."), turn("user", "Go ahead."),
                            turn("assistant", "Working on it."), turn("assistant", MENU_A)])

    def test_menu_with_recommendation_passes(self):
        self.assert_passes(loop("Hmm, not sure.", final=MENU_A_WITH_RECOMMENDATION))

    def test_ordinary_question_passes(self):
        self.assert_passes([turn("assistant", "Sure, let's find a time."), turn("user", "Okay."),
                            turn("assistant", "Sounds good."), turn("assistant", ORDINARY_QUESTION)])

    def test_bang_detail_flag_passes(self):
        self.assert_passes(loop("!detail give me everything"))

    def test_stop_hook_active_passes(self):
        self.assert_passes(loop("Not sure."), stop_hook_active=True)

    def test_skip_file_passes(self):
        (self.tmp / ".grounding-skip").touch()
        self.assert_passes(loop("Not sure."))

    def test_skip_env_passes(self):
        env = dict(self.env, GROUNDING_GATES_SKIP="option-loop")
        self.assert_passes(loop("Not sure."), env=env)

    def test_advise_mode(self):
        env, _ = make_env({}, config={"option-loop": {"mode": "advise"}})
        self.assertIn("systemMessage", self.run_turns(loop("Not sure."), env=env))

    def test_fewer_than_three_assistant_messages_passes(self):
        self.assert_passes([turn("assistant", MENU_A), turn("user", "Not sure."),
                            turn("assistant", MENU_A_REWORDED)])

    def test_no_user_turn_between_offers_passes(self):
        self.assert_passes([turn("assistant", "Starting."), turn("assistant", MENU_A),
                            turn("assistant", MENU_A_REWORDED)])

    def test_dissimilar_prior_menu_passes(self):
        self.assert_passes(loop("Not sure.", first=MENU_B_UNRELATED, final=MENU_A))

    def test_would_you_like_a_or_b_shape_blocks(self):
        self.assert_blocks(loop(
            "Let me think about it.",
            first="Would you like the async worker rewrite or the cron job patch?",
            final="Would you like the async worker rewrite, or the cron job patch instead?"))


class FailOpen(Base):
    def test_corrupt_transcript(self):
        self.transcript.write_text("{not valid json\n{{{ garbage")
        self.assertEqual(run_hook("option-loop", self.payload(), self.env).stdout.strip(), "")

    def test_missing_transcript_path(self):
        p = self.payload()
        del p["transcript_path"]
        self.assertEqual(run_hook("option-loop", p, self.env).stdout.strip(), "")

    def test_nonexistent_transcript_file(self):
        p = self.payload(transcript_path=str(self.tmp / "does-not-exist.jsonl"))
        self.assertEqual(run_hook("option-loop", p, self.env).stdout.strip(), "")

    def test_malformed_stdin(self):
        r = run_hook("option-loop", None, self.env, stdin_text="not json at all")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


class FalsePositiveFixes(Base):
    """A user asking to see the menu again is not a loop; a recap is not a menu."""

    def test_what_were_the_options_passes(self):
        self.assert_passes(loop("Sorry, what were the options again?"))

    def test_repeat_those_passes(self):
        self.assert_passes(loop("Can you repeat those?"))

    def test_remind_me_passes(self):
        self.assert_passes(loop("Remind me of the choices?"))

    def test_genuine_loop_still_blocks(self):
        self.assert_blocks(loop("I'm still thinking about it, give me a sec."))

    def test_numbered_recap_with_trailing_question_passes(self):
        self.assert_passes([turn("assistant", "Starting."), turn("user", "Go ahead."),
                            turn("assistant", "Working on it."), turn("assistant", SUMMARY)])

    def test_repeated_recap_still_passes(self):
        self.assert_passes(loop("Great, thanks.", first=SUMMARY, final=SUMMARY_REWORDED))

    def test_menu_with_immediate_question_still_blocks(self):
        self.assert_blocks(loop("Not sure."))

    def test_leading_interrogative_menu_detected(self):
        self.assert_blocks(loop(
            "Not sure.",
            first=("Which approach do you want?\n1. Rebuild the pipeline with async workers\n"
                   "2. Patch the existing cron job instead"),
            final=("Which approach do you want?\n1. Rebuild the pipeline using async workers\n"
                   "2. Just patch the existing cron job")))


class ReofferRequestAnchoring(Base):
    """Selection answers that merely contain 'option'/'choice' must not defeat the gate."""

    def test_selection_answer_with_keywords_still_blocks(self):
        self.assert_blocks(loop("I like option 1, let's ship it. That's my final choice, go do it."))

    def test_repeat_the_options_request_passes(self):
        self.assert_passes(loop("repeat the options?"))

    def test_short_selection_still_blocks(self):
        self.assert_blocks(loop("option 2 please"))

    def test_mixed_selection_and_request_passes(self):
        self.assert_passes(loop("I'd take option 1, but what were the choices again?"))


if __name__ == "__main__":
    unittest.main()
