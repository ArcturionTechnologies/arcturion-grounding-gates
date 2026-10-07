"""Claims gate: absence / broken / fixed claims need evidence."""
import json
import unittest

from _util import make_env, run_hook, text_row, write_transcript


class TestClaimsGate(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env({})

    def stop(self, text, sid, active=False, env=None):
        path = write_transcript(self.tmp / f"{sid}.jsonl",
                                [text_row("user", "go"), text_row("assistant", text)])
        return run_hook("claims", {"transcript_path": path, "session_id": sid,
                                   "stop_hook_active": active, "cwd": str(self.tmp)}, env or self.env)

    def test_absence_without_search_is_flagged(self):
        r = self.stop("Bad news: we don't have a token for the DNS provider, so I can't proceed.", "a1")
        self.assertIn("PROOF OF SEARCH", json.loads(r.stdout)["systemMessage"])

    def test_absence_with_search_trail_allowed(self):
        r = self.stop("I searched the secret store and environment variables; we don't have a token "
                      "for the DNS provider. Search trail: store list, env dump.", "a2")
        self.assertEqual(r.stdout.strip(), "")

    def test_custom_absence_evidence_pattern(self):
        env, _ = make_env({}, config={"claims": {"absence_evidence": r"vaultctl search"}})
        r = self.stop("Ran vaultctl search deploy: we don't have a token for it.", "a3", env=env)
        self.assertEqual(r.stdout.strip(), "")
        r2 = self.stop("I searched everywhere; we don't have a token for it.", "a4", env=env)
        self.assertIn("PROOF OF SEARCH", r2.stdout)

    def test_broken_without_evidence_flagged(self):
        r = self.stop("The message bridge is broken, that's why nothing arrives.", "b1")
        self.assertIn("PROVENANCE", r.stdout)

    def test_broken_with_repro_allowed(self):
        r = self.stop("The bridge is broken: reproduced after restart, exit code 1, "
                      "traceback ConnectionRefusedError on port 8091.", "b2")
        self.assertEqual(r.stdout.strip(), "")

    def test_fixed_without_rerun_flagged(self):
        r = self.stop("Great news, the audio daemon is fixed.", "f1")
        self.assertIn("PROVENANCE", r.stdout)

    def test_fixed_with_rerun_allowed(self):
        r = self.stop("The audio daemon is fixed: re-ran the smoke test, exit 0, audio verified.", "f2")
        self.assertEqual(r.stdout.strip(), "")

    def test_status_word_resolved_is_not_a_fix_claim(self):
        r = self.stop("This card is listed as both pending and resolved, so the router won't close it.", "f3")
        self.assertEqual(r.stdout.strip(), "")

    def test_proposed_or_negated_fix_is_not_a_fix_claim(self):
        for i, text in enumerate(("It isn't fixed yet; I will propose a repair plan.",
                                  "Want me to get this repaired next session?",
                                  "Nothing was fixed this turn.")):
            r = self.stop(text, f"f4{i}")
            self.assertEqual(r.stdout.strip(), "", text)

    def test_first_person_fix_claim_still_caught(self):
        for i, text in enumerate(("I fixed the hook.", "Fixed: the audio daemon.",
                                  "The bridge is working now.", "We've resolved the outage.")):
            r = self.stop(text, f"f5{i}")
            self.assertIn("PROVENANCE", json.loads(r.stdout)["systemMessage"], text)

    def test_block_mode_emits_stop_decision(self):
        env, _ = make_env({}, config={"claims": {"mode": "block"}})
        r = self.stop("Great news, the audio daemon is fixed.", "m1", env=env)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_stop_hook_active_never_loops(self):
        r = self.stop("we don't have a token for X.", "loop", active=True)
        self.assertEqual(r.stdout.strip(), "")

    def test_session_cap_two(self):
        for _ in range(2):
            self.stop("we don't have a token for Y.", "cap")
        r = self.stop("we don't have a token for Y.", "cap")
        self.assertEqual(r.stdout.strip(), "")

    def test_skip_file_silences(self):
        (self.tmp / ".grounding-skip").touch()
        r = self.stop("we don't have a token for Z.", "skip")
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
