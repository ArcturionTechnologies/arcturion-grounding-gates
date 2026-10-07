"""Hook runner: payload in, verdict out, rendered for a harness.

Every gate is reachable as ``grounding-gates hook <gate> [--format ...]``. The
runner reads one JSON payload on stdin, extracts the fields that gate needs,
calls the harness-neutral gate function, and prints the verdict.

Payload fields used (Claude Code's hook payload has all of them):

  prompt            prompt-submit gates
  tool_input        question / answer-capture  ({"questions": [...]})
  tool_response     answer-capture
  transcript_path   Stop gates (JSON Lines transcript, see core.py)
  session_id, cwd, stop_hook_active

Formats:

  claude-code  inject -> plain stdout (added to context)
               deny   -> {"hookSpecificOutput": {"permissionDecision": "deny", ...}}
               block  -> {"decision": "block", "reason": ...}
               advise -> {"systemMessage": ...}
  json         the Verdict as JSON, always printed (for other harnesses or scripts)

Every gate fails open: any error, missing transcript or bad payload means
"allow". A gate that cannot read its inputs must never trap the agent.
"""
from __future__ import annotations

import json
import sys

from . import claims, completion, core, factual, grounding, option_loop, questions

PROMPT_GATES = {"grounding", "factual-reminder"}
STOP_GATES = {"claims", "completion", "factual", "option-loop"}
TOOL_GATES = {"question", "answer-capture"}
ALL_GATES = sorted(PROMPT_GATES | STOP_GATES | TOOL_GATES)

EVENT_NAMES = {
    "grounding": "UserPromptSubmit", "factual-reminder": "UserPromptSubmit",
    "question": "PreToolUse", "answer-capture": "PostToolUse",
    "claims": "Stop", "completion": "Stop", "factual": "Stop", "option-loop": "Stop",
}


def evaluate(gate: str, payload: dict) -> core.Verdict:
    """Run one gate against a hook payload. Never raises."""
    try:
        return _evaluate(gate, payload)
    except Exception as exc:  # fail open
        core.log("gate_error", gate=gate, error=f"{type(exc).__name__}: {exc}"[:300])
        return core.Verdict(gate)


def _evaluate(gate: str, payload: dict) -> core.Verdict:
    if gate not in ALL_GATES:
        raise ValueError(f"unknown gate: {gate}")
    if not isinstance(payload, dict):
        return core.Verdict(gate)
    cwd = payload.get("cwd")
    if not core.gate_enabled(gate, cwd):
        return core.Verdict(gate)

    if gate == "grounding":
        return grounding.evaluate(str(payload.get("prompt") or ""))
    if gate == "factual-reminder":
        return factual.reminder(str(payload.get("prompt") or payload.get("user_message") or ""))

    tool_input = payload.get("tool_input") or {}
    if gate == "question":
        return questions.evaluate_question(tool_input.get("questions") or [])
    if gate == "answer-capture":
        questions.capture_answers(tool_input.get("questions") or [], payload.get("tool_response"))
        return core.Verdict(gate)

    # Stop gates
    if payload.get("stop_hook_active"):
        return core.Verdict(gate)  # never loop the loop-guard
    rows = core.read_transcript(payload.get("transcript_path"))
    if rows is None:
        return core.Verdict(gate)
    session_id = str(payload.get("session_id") or "unknown")
    if gate == "claims":
        return claims.evaluate(core.last_assistant_text(rows), session_id)
    if gate == "completion":
        return completion.evaluate(rows)
    if gate == "factual":
        return factual.evaluate(core.last_assistant_text(rows))
    return option_loop.evaluate(rows)


def render(verdict: core.Verdict, fmt: str = "claude-code") -> str:
    if fmt == "json":
        return json.dumps(verdict.to_json(), ensure_ascii=False)
    if not verdict.fired:
        return ""
    if verdict.action == "inject":
        return verdict.message
    if verdict.action == "deny":
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": EVENT_NAMES.get(verdict.gate, "PreToolUse"),
            "permissionDecision": "deny",
            "permissionDecisionReason": verdict.message,
        }}, ensure_ascii=False)
    if verdict.action == "block":
        return json.dumps({"decision": "block", "reason": verdict.message}, ensure_ascii=False)
    return json.dumps({"systemMessage": verdict.message}, ensure_ascii=False)


def run(gate: str, fmt: str = "claude-code", stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        payload = {}
    verdict = evaluate(gate, payload)
    if verdict.fired:
        core.log(verdict.event or f"{gate}_fired", gate=gate, action=verdict.action,
                 agent=core.agent_name(), session=str((payload or {}).get("session_id", "")),
                 **{k: v for k, v in verdict.details.items() if k in ("snippet", "q", "matched",
                                                                      "tool_hint", "sections")})
    out = render(verdict, fmt)
    if out:
        stdout.write(out + "\n")
    return 0
