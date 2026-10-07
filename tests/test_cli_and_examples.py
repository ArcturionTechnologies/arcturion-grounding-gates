"""CLI registry commands, the hook shim, and the shipped examples."""
import json
import os
import subprocess
import sys
import unittest

from _util import ROOT, make_env

from grounding_gates import hooks


def cli(args, env):
    return subprocess.run([sys.executable, "-m", "grounding_gates", *args],
                          capture_output=True, text=True, env=env, timeout=15, cwd=str(ROOT))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.env, self.tmp = make_env({})

    def test_complete_then_check(self):
        cli(["complete", "quarterly report export", "--proof", "reports/q4.pdf", "--agent", "writer"], self.env)
        r = cli(["complete-check", "work on the quarterly report"], self.env)
        self.assertIn("quarterly report export", r.stdout)

    def test_directive_add_and_list(self):
        cli(["directive", "add", "Never push on Fridays.", "--keywords", "push,deploy"], self.env)
        r = cli(["directive", "list"], self.env)
        self.assertIn("sd-001 [live] Never push on Fridays.", r.stdout)
        reg = json.loads((self.tmp / "standing_directives.json").read_text())
        self.assertEqual(reg["directives"][0]["keywords"], ["push", "deploy"])

    def test_answer_add_and_find(self):
        cli(["answer", "add", "Which region should staging run in?", "The same region as production."], self.env)
        r = cli(["answer", "find", "which region should staging run in"], self.env)
        self.assertIn("same region as production", r.stdout)
        self.assertIn("no match", cli(["answer", "find", "what font for the docs"], self.env).stdout)

    def test_mistake_and_defect_ledgered(self):
        cli(["mistake", "add", "--class", "unverified-claim", "--text", "said deploy was live from a 200"], self.env)
        cli(["defect", "add", "--gate", "question", "--text", "denied a new question as duplicate"], self.env)
        rows = [json.loads(x) for x in (self.tmp / "mistake_ledger.jsonl").read_text().splitlines()]
        self.assertEqual([r["class"] for r in rows], ["unverified-claim", "gate-defect"])

    def test_gates_listing(self):
        r = cli(["gates"], self.env)
        for gate in hooks.ALL_GATES:
            self.assertIn(gate, r.stdout)

    def test_replay_command(self):
        log = self.tmp / "events.log"
        log.write_text(json.dumps({"event": "tier_b", "snippet": "It is done."}) + "\n")
        r = cli(["replay", str(log)], self.env)
        self.assertEqual(json.loads(r.stdout)["warn_total"], 1)

    def test_unknown_gate_rejected_by_parser(self):
        self.assertNotEqual(cli(["hook", "risk-calibrator"], self.env).returncode, 0)

    def test_global_off_switch_silences_hooks(self):
        env = dict(self.env, GROUNDING_GATES_OFF="1")
        r = subprocess.run([sys.executable, "-m", "grounding_gates", "hook", "factual-reminder"],
                           input=json.dumps({"prompt": "anything"}), capture_output=True,
                           text=True, env=env, timeout=15, cwd=str(ROOT))
        self.assertEqual(r.stdout.strip(), "")


class TestShimAndExamples(unittest.TestCase):
    def test_hook_shim_runs_without_install(self):
        env, _ = make_env({})
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "grounding-hook"), "factual-reminder"],
                           input=json.dumps({"prompt": "Pick a queue library."}),
                           capture_output=True, text=True, env=env, timeout=15, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("VERIFY BEFORE ASSERTING", r.stdout)

    def test_settings_example_references_only_real_gates(self):
        settings = json.loads((ROOT / "examples" / "claude-code" / "settings.json").read_text())
        commands = [h["command"] for groups in settings["hooks"].values()
                    for group in groups for h in group["hooks"]]
        self.assertTrue(commands)
        named = {c.split()[-1] for c in commands}
        self.assertTrue(named <= set(hooks.ALL_GATES), named - set(hooks.ALL_GATES))
        self.assertEqual(named, set(hooks.ALL_GATES))

    def test_example_registries_parse_and_match(self):
        reg_dir = ROOT / "examples" / "registries"
        env, tmp = make_env({})
        for f in reg_dir.iterdir():
            (tmp / f.name).write_text(f.read_text())
            if f.suffix == ".json":
                json.loads(f.read_text())
        r = subprocess.run([sys.executable, "-m", "grounding_gates", "hook", "grounding"],
                           input=json.dumps({"prompt": "Which test runner should this repo use for CI checks?"}),
                           capture_output=True, text=True, env=env, timeout=15, cwd=str(ROOT))
        self.assertIn("ALREADY ANSWERED", r.stdout)

    def test_no_security_gating_shipped(self):
        names = {p.stem for p in (ROOT / "grounding_gates").glob("*.py")}
        self.assertNotIn("risk_calibrator", names)
        self.assertFalse(any("PreToolUse" == hooks.EVENT_NAMES[g] and g != "question"
                             for g in hooks.ALL_GATES))
        self.assertTrue(os.access(ROOT / "bin" / "grounding-hook", os.X_OK))


if __name__ == "__main__":
    unittest.main()
