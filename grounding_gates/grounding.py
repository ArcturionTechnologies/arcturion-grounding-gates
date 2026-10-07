"""Lookup-before-answering: inject what is already known before the model replies.

Runs on prompt submit. Matches the prompt against four registries and, when
anything matches, returns a short context block:

  - standing directives that are still live (do not re-litigate them),
  - answers the user already gave to a similar question (do not re-ask),
  - tasks already recorded as complete (verify before redoing),
  - canonical tool routes for the job at hand (use the sanctioned tool).

No match means silence. The block is capped so it can't flood the context.
"""
from __future__ import annotations

from . import core

GATE = "grounding"


def evaluate(prompt: str, settings: dict | None = None) -> core.Verdict:
    settings = settings or core.gate_settings(GATE)
    prompt = (prompt or "").strip()
    if len(prompt) < int(settings.get("min_prompt_chars", 30)):
        return core.Verdict(GATE)

    sections: list[str] = []

    for d in core.load_registry("standing_directives").get("directives", []):
        if d.get("status") == "live" and core.keyword_score(prompt, d.get("keywords", [])) >= 1:
            sections.append(f"STANDING DIRECTIVE (still live, do not re-litigate): {d['text']}")

    hit = core.match_question(prompt, core.load_registry("answers").get("answers", []))
    if hit:
        sections.append(
            f"ALREADY ANSWERED ({hit.get('date', '?')}): Q: {hit['q']} -> A: {hit['a']} "
            "[use it; do not re-ask]"
        )

    for row in core.read_jsonl("completions"):
        if core.keyword_score(prompt, str(row.get("task", "")).split()) >= 2:
            sections.append(
                f"ALREADY COMPLETED {row.get('date', '?')}: {row['task']} "
                f"(proof: {row.get('proof', 'ledger')}). Verify before re-opening; do not redo silently."
            )
            break

    for route in core.load_registry("tool_routing").get("routes", []):
        if core.keyword_score(prompt, route.get("keywords", [])) >= 1:
            sections.append(
                f"CANONICAL TOOL: {route['job']} -> {route['tool']} (never: {route.get('never', '-')})"
            )

    if not sections:
        return core.Verdict(GATE)
    block = "GROUNDING (consult before acting):\n" + "\n".join(f"- {s}" for s in sections)
    return core.Verdict(GATE, "inject", block[: int(settings.get("max_chars", 3600))],
                        "grounding_injected", {"sections": len(sections)})
