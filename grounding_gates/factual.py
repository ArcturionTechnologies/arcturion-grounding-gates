"""Verify before asserting.

Two light pieces for the same rule: a factual claim that drives a
recommendation should be checked against a primary source, or labelled
unverified/secondhand in the same breath.

  ``reminder``  prompt-submit injection of the rule (deterministic, one line).
  ``evaluate``  Stop-time audit. Confident recommendation language with no
                citation or hedge anywhere in the final message produces an
                advisory note. It never blocks: it is pattern matching with a
                real false-positive rate, and that rate has not been measured.
"""
from __future__ import annotations

import re

from . import core

GATE = "factual"
REMINDER_GATE = "factual-reminder"

REMINDER = (
    "VERIFY BEFORE ASSERTING: for factual claims that affect a recommendation or next step, "
    "verify against a primary source when practical; otherwise label the claim unverified or "
    "secondhand in the same breath."
)

CONFIDENT_CLAIM = re.compile(
    r"\b("
    r"next\s+step|safest\s+move|best\s+option|the\s+way\s+to\s+do\s+this"
    r"|(?:i\s+)?recommend(?:s|ed)?"
    r"|you\s+should\s+(?:do|use|apply|go\s+with|try)"
    r"|that'?s\s+your\s+(?:safest|best|way)"
    r"|offers\s+a|provides\s+a|has\s+a\s+.{0,40}?feature"
    r")\b",
    re.IGNORECASE,
)

HEDGE_OR_CITATION = re.compile(
    r"https?://\S+"
    r"|\bverified\b|\bconfirmed\b|\bunverified\b|\bunconfirmed\b"
    r"|haven'?t\s+(?:verified|checked)|not\s+(?:yet\s+)?verified|could\s+not\s+verify"
    r"|second-?hand|paraphrase[d]?|checked\s+directly|checked\s+against"
    r"|according\s+to\s+\S+'?s\s+own|per\s+the\s+official|source\s*:",
    re.IGNORECASE,
)

AUDIT_NOTE = (
    "VERIFY BEFORE ASSERTING (audit): confident recommendation language with no citation and "
    "no unverified/secondhand hedge in this message. If the underlying claim wasn't checked "
    "against its primary source, say so next to the recommendation. (Logged; does not block.)"
)


def reminder(prompt: str) -> core.Verdict:
    if not (prompt or "").strip():
        return core.Verdict(REMINDER_GATE)
    return core.Verdict(REMINDER_GATE, "inject", REMINDER, "factual_reminder")


def audit_check(last_turn: str) -> tuple[bool, str]:
    if not last_turn or not CONFIDENT_CLAIM.search(last_turn):
        return False, ""
    if HEDGE_OR_CITATION.search(last_turn):
        return False, ""
    return True, AUDIT_NOTE


def evaluate(final_text: str) -> core.Verdict:
    warn, note = audit_check(final_text)
    if not warn:
        return core.Verdict(GATE)
    return core.Verdict(GATE, "advise", note, "factual_audit", {"snippet": final_text[:300]})
