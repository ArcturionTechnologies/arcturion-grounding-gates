"""Prove before claiming: evidence must be an observable chain, not completion-shaped prose."""
import json
import unittest

from _util import EnvPatch, make_env, run_hook, write_transcript

from grounding_gates import completion as gate
from grounding_gates import core

CHAIN = (
    "request_id=req-7; predicate=tests; command=python3 -m unittest; exit=0; "
    "artifact=report.json; sha256=" + "a" * 64 + "; readback=HTTP 200; observed_at=2026-08-03T12:00:00-0400"
)


class EvidenceTruth(unittest.TestCase):
    def test_bare_verified_is_not_evidence(self):
        self.assertFalse(gate.has_authoritative_evidence("Done. Verified."))

    def test_bare_confirmed_is_not_evidence(self):
        self.assertFalse(gate.has_authoritative_evidence("The deployment is live and confirmed."))

    def test_code_fence_is_not_evidence(self):
        self.assertFalse(gate.has_authoritative_evidence("Done.\n```\nlooks good\n```"))

    def test_inability_to_verify_is_disclosure_not_evidence(self):
        self.assertFalse(gate.has_authoritative_evidence("Done, but verification could not be performed."))

    def test_complete_observation_chain_is_evidence(self):
        self.assertTrue(gate.has_authoritative_evidence("Result complete. " + CHAIN))

    def test_complete_json_observation_chain_is_evidence(self):
        payload = {"request_id": "req-7", "predicate": "tests", "command": "python3 -m unittest",
                   "exit": 0, "artifact": "report.json", "sha256": "a" * 64,
                   "readback": "HTTP 200", "generated_at": "2026-08-03T12:00:00-04:00"}
        self.assertTrue(gate.has_authoritative_evidence(json.dumps(payload)))

    def test_json_chain_missing_field_is_not_evidence(self):
        payload = {"request_id": "req-7", "predicate": "tests", "command": "python3 -m unittest",
                   "exit": 0, "artifact": "report.json", "sha256": "a" * 64,
                   "generated_at": "2026-08-03T12:00:00-04:00"}
        self.assertFalse(gate.has_authoritative_evidence(json.dumps(payload)))

    def test_tier_b_internal_completion_fires_without_chain(self):
        fires, note = gate.tier_b_check("The internal refactor is done and verified.")
        self.assertTrue(fires)
        self.assertIn("tier b", note.lower())

    def test_tier_b_ignores_turn_without_tools(self):
        self.assertFalse(gate.tier_b_check("Yeah, that's done.", turn_did_work=False)[0])


def rows_of(*blocks):
    rows = [{"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Please help"}]}}]
    for role, block in blocks:
        rows.append({"type": role, "message": {"role": role, "content": [block]}})
    return rows


def shell(cid, cmd):
    return ("assistant", {"type": "tool_use", "id": cid, "name": "Bash", "input": {"command": cmd}})


def result(cid, content="ok", **extra):
    return ("user", {"type": "tool_result", "tool_use_id": cid, "content": content, **extra})


def say(text):
    return ("assistant", {"type": "text", "text": text})


class TypedActionTruth(unittest.TestCase):
    def assert_tier_a(self, rows, expected, tools=None):
        turn = core.last_assistant_text(rows)
        self.assertEqual(gate.tier_a_check(rows, turn, tools)[0], expected)

    def test_read_only_log_result_quoting_post_passes(self):
        rows = rows_of(shell("read", "rg 'hook denial' log.jsonl"),
                       result("read", "prior denial: curl -X POST https://example.com"),
                       say("The audit plan is complete."))
        self.assert_tier_a(rows, False)
        self.assertTrue(gate.turn_had_tool_use(rows))

    def test_successful_external_post_without_readback_blocks(self):
        rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"),
                       result("post", "HTTP 200", exit_code=0), say("The update is done."))
        self.assert_tier_a(rows, True)

    def test_successful_post_with_chain_passes(self):
        rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"),
                       result("post", "HTTP 200"), say("The update is done. " + CHAIN))
        self.assert_tier_a(rows, False)

    def test_localhost_post_is_internal(self):
        rows = rows_of(shell("post", "curl -X POST http://localhost:8080/x -d hello"),
                       result("post", "HTTP 200"), say("The update is done."))
        self.assert_tier_a(rows, False)

    def test_failed_post_passes(self):
        rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"),
                       result("post", "request failed", is_error=True), say("The draft is done."))
        self.assert_tier_a(rows, False)

    def test_failed_post_in_result_content_passes(self):
        rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"),
                       result("post", json.dumps({"exit_code": 7, "output": "connection refused"})),
                       say("The draft is done."))
        self.assert_tier_a(rows, False)

    def test_external_send_without_confirmed_effect_passes(self):
        rows = rows_of(("assistant", {"type": "tool_use", "id": "send", "name": "mcp__gmail__send",
                                      "input": {"to": "recipient@example.com"}}),
                       result("send", "No message sent", effect_disposition="none"),
                       say("The draft is done."))
        self.assert_tier_a(rows, False)

    def test_user_quote_does_not_count_as_action(self):
        rows = rows_of(say("Your quoted curl -X POST example is clear. The plan is done."))
        self.assert_tier_a(rows, False)
        self.assertFalse(gate.turn_had_tool_use(rows))

    def test_internal_send_message_passes(self):
        rows = rows_of(("assistant", {"type": "tool_use", "id": "msg", "name": "send_message",
                                      "input": {"target": "agent", "message": "status"}}),
                       result("msg"), say("The coordination is done."))
        self.assert_tier_a(rows, False)

    def test_external_send_blocks(self):
        rows = rows_of(("assistant", {"type": "tool_use", "id": "send", "name": "mcp__gmail__send",
                                      "input": {"to": "recipient@example.com"}}),
                       result("send", "sent"), say("The email is sent."))
        self.assert_tier_a(rows, True)

    def test_connector_style_gmail_send_blocks(self):
        rows = rows_of(("assistant", {"type": "tool_use", "id": "send",
                                      "name": "mcp__claude_apps__gmail_send_email",
                                      "input": {"to": "recipient@example.com"}}),
                       result("send", "sent"), say("The email is sent."))
        self.assert_tier_a(rows, True)

    def test_custom_external_tool_pattern(self):
        rows = rows_of(("assistant", {"type": "tool_use", "id": "p", "name": "mcp__music__playlist_create",
                                      "input": {"name": "focus"}}),
                       result("p", "created"), say("The playlist is created."))
        self.assert_tier_a(rows, False)
        self.assert_tier_a(rows, True, tools=r"mcp__music__playlist_\w+")

    def test_previous_turn_action_does_not_count(self):
        rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"), result("post", "HTTP 200"))
        rows.append({"type": "user", "message": {"role": "user", "content": "thanks, now summarize"}})
        rows.append({"type": "assistant", "message": {"role": "assistant", "content": "Summary is ready."}})
        self.assert_tier_a(rows, False)


class HookBehaviour(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env({})
        self.rows = rows_of(shell("post", "curl -X POST https://example.com -d hello"),
                            result("post", "HTTP 200"), say("The update is done."))

    def payload(self, rows=None, **extra):
        path = write_transcript(self.tmp / "t.jsonl", rows or self.rows)
        return {"transcript_path": path, "session_id": "s", "cwd": str(self.tmp), **extra}

    def test_tier_a_blocks_via_hook(self):
        r = run_hook("completion", self.payload(), self.env)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")
        self.assertIn("tier_a", (self.tmp / "gates.log").read_text())

    def test_advise_mode(self):
        env, _ = make_env({}, config={"completion": {"mode": "advise"}})
        r = run_hook("completion", self.payload(), env)
        self.assertIn("systemMessage", json.loads(r.stdout))

    def test_tier_b_off_by_default_and_opt_in(self):
        rows = rows_of(shell("ls", "ls"), result("ls"), say("The refactor is done."))
        self.assertEqual(run_hook("completion", self.payload(rows), self.env).stdout.strip(), "")
        env, _ = make_env({}, config={"completion": {"tier_b_mode": "advise"}})
        r = run_hook("completion", self.payload(rows), env)
        self.assertIn("tier B", json.loads(r.stdout)["systemMessage"])

    def test_skip_file_silences(self):
        (self.tmp / ".grounding-skip").touch()
        self.assertEqual(run_hook("completion", self.payload(), self.env).stdout.strip(), "")

    def test_stop_hook_active_never_loops(self):
        r = run_hook("completion", self.payload(stop_hook_active=True), self.env)
        self.assertEqual(r.stdout.strip(), "")

    def test_malformed_input_fails_open(self):
        r = run_hook("completion", None, self.env, stdin_text="{")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_missing_transcript_fails_open(self):
        r = run_hook("completion", {"cwd": str(self.tmp), "session_id": "m"}, self.env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


class WarnCorpusReplay(unittest.TestCase):
    def test_replay_reports_unknowns_instead_of_claiming_tuning(self):
        rows = [
            {"event": "tier_b", "snippet": "The build is done."},
            {"event": "tier_b", "snippet": "No completion phrase survived truncation"},
            {"event": "tier_a", "snippet": "Done."},
        ]
        report = gate.replay_warn_rows(rows)
        self.assertEqual(report["warn_total"], 2)
        self.assertEqual(report["evaluable"], 1)
        self.assertEqual(report["unknown_truncated"], 1)
        self.assertEqual(report["false_positive_rate"], "UNKNOWN")


class InProcessEvaluate(unittest.TestCase):
    def test_evaluate_returns_verdict(self):
        env, _ = make_env({})
        with EnvPatch(env):
            v = gate.evaluate(rows_of(shell("post", "curl -X POST https://example.com -d x"),
                                      result("post", "200"), say("It is live.")))
        self.assertEqual(v.action, "block")
        self.assertEqual(v.details["tool_hint"], "curl POST")


if __name__ == "__main__":
    unittest.main()
