"""Stop the agent re-asking settled questions.

Two halves around a structured "ask the user" tool call:

  ``evaluate_question`` (before the call)
    1. Dedupe: if the user already answered a similar question, deny the call
       and hand the stored answer back to the agent.
    2. Question craft: bounce structurally lazy questions (no options offered,
       or text too short to name the decision it unblocks).

  ``capture_answers`` (after the call)
    Every new answer is stored with keywords, so the user answers each
    question once.

Lenient by design: a wrongly denied question is a gate defect, so the lint
only catches unambiguous cases. Question texts containing an exempt marker
(configurable) skip the dedupe check.
"""
from __future__ import annotations

from . import core

GATE = "question"
CAPTURE_GATE = "answer-capture"


def evaluate_question(questions: list[dict], settings: dict | None = None) -> core.Verdict:
    settings = settings or core.gate_settings(GATE)
    if not questions:
        return core.Verdict(GATE)
    answers = core.load_registry("answers").get("answers", [])
    exempt = [m.lower() for m in settings.get("exempt_markers") or []]

    for q in questions:
        text = (q.get("question") or "").strip()
        low = text.lower()
        if exempt and any(m in low for m in exempt):
            continue
        hit = core.match_question(text, answers)
        if hit:
            return core.Verdict(
                GATE, "deny",
                f"The user ALREADY answered this on {hit.get('date', '?')}: "
                f"Q: '{hit['q']}' -> A: '{hit['a']}'. Use that answer and continue. "
                "Only re-ask if you have concrete evidence it is stale, and say so.",
                "question_dedupe_deny", {"q": text[:150], "matched": str(hit.get("q", ""))[:150]},
            )
        if settings.get("craft_lint", True) and (len(text) < 15 or not q.get("options")):
            return core.Verdict(
                GATE, "deny",
                "QUESTION CRAFT: rework this question first. Name the decision it unblocks, "
                "ask one concept, and offer 2-4 concrete options with a recommendation. "
                "Batch it with any other open questions.",
                "question_craft_bounce", {"q": text[:150]},
            )
    return core.Verdict(GATE)


def _answer_map(response) -> dict:
    # Accept {"answers": {"<question>": "<answer>"}}, a plain mapping, or a string.
    if isinstance(response, dict):
        inner = response.get("answers")
        return inner if isinstance(inner, dict) else response
    return {}


def capture_answers(questions: list[dict], response, agent: str | None = None) -> int:
    """Store each newly answered question. Returns how many were added."""
    if not questions or response is None:
        return 0
    ans_map = _answer_map(response)
    reg = core.load_registry("answers")
    answers = reg.setdefault("answers", [])
    added = 0
    for q in questions:
        qtext = (q.get("question") or "").strip()
        if not qtext:
            continue
        atext = None
        for key, val in ans_map.items():
            if isinstance(val, str) and (key.strip() == qtext or qtext.startswith(str(key).strip()[:40])):
                atext = val
                break
        if atext is None and isinstance(response, str):
            atext = response
        if not atext:
            continue
        if core.match_question(qtext, answers):
            continue  # already registered
        answers.append({
            "id": f"ans-{len(answers) + 1:04d}",
            "q": qtext,
            "a": str(atext)[:1000],
            "date": core.now()[:10],
            "asked_by": agent or core.agent_name(),
            "keywords": core.keywords_for(qtext + " " + str(atext)),
        })
        added += 1
    if added:
        core.save_registry("answers", reg)
        core.log("answers_captured", count=added, agent=agent or core.agent_name())
    return added
