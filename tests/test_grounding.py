"""Grounding injection: directives, prior answers, completions, tool routes."""
import json
import time
import unittest
from pathlib import Path

from _util import make_env, run_hook

SEED = {
    "standing_directives": {"directives": [
        {"id": "sd-001", "status": "live",
         "text": "Sign documents with the approved signature block before staging them for delivery.",
         "keywords": ["sign", "signature", "document", "packet"]},
        {"id": "sd-002", "status": "revoked", "text": "Old rule about packets.", "keywords": ["packet"]},
    ]},
    "answers": {"answers": [
        {"q": "which payment provider should the billing service use", "a": "the existing provider, keep it",
         "date": "2026-01-05", "keywords": ["payment", "provider", "billing"]},
    ]},
    "tool_routing": {"routes": [
        {"job": "secrets / tokens", "tool": "your secret manager CLI",
         "never": "hard-coding a value or claiming absence without searching",
         "keywords": ["token", "secret", "credential"]},
    ]},
    "completions.jsonl": [
        {"task": "quarterly report export prepared", "date": "2026-01-12",
         "proof": "reports/q4.pdf", "agent": "writer"},
    ],
}


def gate(prompt, env, sid="s"):
    return run_hook("grounding", {"prompt": prompt, "session_id": sid, "cwd": "/tmp"}, env)


class TestGrounding(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env(dict(SEED))

    def test_completed_task_injected(self):
        r = gate("let's work on the quarterly report export task today please", self.env)
        self.assertIn("ALREADY COMPLETED", r.stdout)
        self.assertIn("quarterly report export", r.stdout)

    def test_live_directive_injected_revoked_one_not(self):
        r = gate("prepare and sign the documents in the delivery packet", self.env)
        self.assertIn("STANDING DIRECTIVE", r.stdout)
        self.assertIn("approved signature block", r.stdout)
        self.assertNotIn("Old rule", r.stdout)

    def test_prior_answer_injected(self):
        r = gate("hmm, which payment provider should the billing service use for invoices?", self.env)
        self.assertIn("ALREADY ANSWERED", r.stdout)
        self.assertIn("the existing provider", r.stdout)

    def test_tool_route_injected(self):
        r = gate("I need to find the deploy token for the new release pipeline", self.env)
        self.assertIn("CANONICAL TOOL", r.stdout)
        self.assertIn("secret manager", r.stdout)

    def test_injection_is_logged_to_isolated_log(self):
        r = gate("which payment provider should the billing service use here?", self.env)
        self.assertEqual(r.returncode, 0)
        log = Path(self.env["GROUNDING_GATES_LOG"])
        self.assertTrue(log.is_file())
        self.assertIn("grounding_injected", log.read_text())

    def test_short_prompt_is_silent(self):
        self.assertEqual(gate("sign it", self.env).stdout.strip(), "")

    def test_no_matches_is_silent(self):
        r = gate("write a short poem about the ocean for the fun of it", self.env)
        self.assertEqual(r.stdout.strip(), "")

    def test_json_format_reports_verdict(self):
        r = run_hook("grounding", {"prompt": "write a short poem about the ocean, nothing else"},
                     self.env, fmt="json")
        self.assertEqual(json.loads(r.stdout)["action"], "allow")

    def test_large_prompt_is_bounded_and_still_matches(self):
        answers = [
            {"q": f"unrelated question number {i} with distinct subject {i}",
             "a": "not relevant", "keywords": [f"subject{i}", f"number{i}"]}
            for i in range(180)
        ] + SEED["answers"]["answers"]
        (self.tmp / "answers.json").write_text(json.dumps({"answers": answers}))
        prompt = (" ".join(f"word{i % 997}" for i in range(5_500))
                  + " which payment provider should the billing service use? "
                  + " ".join(f"tail{i % 991}" for i in range(5_500)))
        start = time.monotonic()
        r = gate(prompt, self.env)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5.0, f"grounding took {elapsed:.3f}s")
        self.assertIn("ALREADY ANSWERED", r.stdout)
        self.assertLessEqual(len(r.stdout), 3601 + 1)


if __name__ == "__main__":
    unittest.main()
