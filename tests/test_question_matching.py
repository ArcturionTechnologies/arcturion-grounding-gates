"""Regression tests for question matching: no false duplicates, no lost paraphrases.

Each case comes from a real false positive or a real paraphrase the matcher
must keep catching, rewritten with neutral subjects.
"""
import difflib
import unittest

import _util  # noqa: F401  (puts the repo on sys.path)

from grounding_gates import core as cc


class StopwordAndWholeWord(unittest.TestCase):
    def test_stopwords_alone_cannot_carry_a_match(self):
        # The original defect: only filler keywords overlapped, and the bare-
        # substring fallback still called it a duplicate.
        answers = [{
            "q": "How much personality survives when an agent is doing REAL work?",
            "a": "irrelevant",
            "keywords": ["much", "personality", "survives", "agent", "doing", "real", "work", "build"],
        }]
        q = "How much room does an agent keep to NOT raise one planning card on a big build?"
        self.assertIsNone(cc.match_question(q, answers))

    def test_whole_word_boundary_not_substring(self):
        self.assertEqual(cc.keyword_hits("we start the process tomorrow", ["art"]), 0)
        self.assertEqual(cc.keyword_hits("this is real art on the wall", ["art"]), 1)

    def test_non_generic_keywords_still_carry_a_match(self):
        answers = [{
            "q": "which payment provider should the billing service use",
            "a": "the existing provider, keep it",
            "keywords": ["payment", "provider", "billing", "existingco"],
        }]
        # Three of four keywords: a rephrasing of the QUESTION naturally omits the
        # answer-topic keyword. It must still match.
        hit = cc.match_question("which payment provider should billing use for invoices?", answers)
        self.assertIsNotNone(hit)


class QuestionRemediation(unittest.TestCase):
    def match(self, q, stored, keywords=None):
        return cc.match_question(q, [{"q": stored, "a": "existing decision", "keywords": keywords or []}])

    def test_framing_collision_is_not_suppressed(self):
        asked = "How should an agent's intelligence score be framed?"
        stored = "What should 'News & Intelligence HQ' cover?"
        self.assertGreater(difflib.SequenceMatcher(None, cc.norm(asked), cc.norm(stored)).ratio(), 0.55)
        self.assertIsNone(self.match(asked, stored, ["news", "intelligence", "hq", "cover"]))

    def test_delivery_vs_execution_collision_is_not_suppressed(self):
        asked = "Where do you want this surfaced to you?"
        stored = "How do you want the build to run?"
        self.assertGreater(difflib.SequenceMatcher(None, cc.norm(asked), cc.norm(stored)).ratio(), 0.55)
        self.assertIsNone(self.match(asked, stored, ["build", "run"]))

    def test_unrelated_matching_grammar_does_not_suppress(self):
        self.assertIsNone(self.match(
            "Which backup destination should the system use for recovery?",
            "Which display theme should the system use for dashboards?"))

    def test_unrelated_short_questions_do_not_suppress(self):
        self.assertIsNone(self.match("Where are the books?", "Where are the bills?"))

    def test_exact_short_duplicate_remains_protected(self):
        self.assertIsNotNone(self.match("Why?", "why"))
        self.assertIsNotNone(self.match("Should we restart the gateway?", "Should we restart the gateway?"))

    def test_punctuation_and_space_duplicate_remains_protected(self):
        self.assertIsNotNone(self.match("Which  payment provider, should billing use?",
                                        "which payment provider should billing use"))

    def test_genuine_fuzzy_paraphrase_remains_protected(self):
        self.assertIsNotNone(self.match(
            "Which backup destination do we use for recovery?",
            "Which backup destination should we use for recovery?"))

    def test_genuine_keyword_paraphrase_remains_protected(self):
        self.assertIsNotNone(self.match(
            "Senior engineer plus calm clinical register for the persona; what humor fits the reviewer bot?",
            "What humor belongs to the reviewer bot's senior engineer persona and calm clinical register?",
            ["reviewer", "persona", "senior", "engineer", "calm", "clinical", "register", "humor"]))

    def test_why_and_where_are_distinct_requests(self):
        self.assertIsNone(self.match("Where is the backup recovery destination?",
                                     "Why is the backup recovery destination unavailable?"))

    def test_opposite_action_is_a_new_decision(self):
        self.assertIsNone(self.match("Should we disable the background gateway?",
                                     "Should we enable the background gateway?",
                                     ["background", "gateway"]))

    def test_negated_decision_is_not_an_exact_duplicate(self):
        self.assertIsNone(self.match("Which payment provider should billing not use?",
                                     "Which payment provider should billing use?",
                                     ["payment", "provider", "billing"]))

    def test_generic_shared_words_do_not_supply_overlap(self):
        self.assertIsNone(self.match(
            "How much room does an agent keep to NOT raise one planning card on a big build?",
            "How much personality survives when an agent is doing REAL work?",
            ["much", "agent", "doing", "work", "personality", "survives"]))


if __name__ == "__main__":
    unittest.main()
