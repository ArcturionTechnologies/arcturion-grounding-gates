"""Provenance for three risky kinds of claim in the agent's final message.

  absence  "we don't have a token/key/credential" with no sign anything was searched
  broken   "X is broken/down/failing" with no reproduced failure (error, exit code, ...)
  fixed    "I fixed X" / "X is working now" with no passing re-run

Advisory by default and capped per session (default 2), because this is
language matching and a false positive must never stop real work. Set
``"claims": {"mode": "block"}`` in config.json to make it hard.

What counts as absence evidence is configurable (``absence_evidence`` regex),
so you can name your own secret store.
"""
from __future__ import annotations

import re

from . import core

GATE = "claims"

ABSENCE = re.compile(
    r"(don'?t|do not|doesn'?t|no longer) have (a|the|any) (token|key|credential|api.?key|password|secret)"
    r"|no (token|credential|api.?key|secret) (exists|found|available|is available)"
    r"|there('?s| is) no (token|credential|api.?key|secret)", re.IGNORECASE)
DEFAULT_ABSENCE_EVIDENCE = (
    r"searched|looked up|checked (the )?(vault|secret store|keychain|password manager|env)"
    r"|secret store|password manager|keychain|environment variables?|search trail"
)

BROKEN = re.compile(
    r"\b(is|it'?s|its|are|seems?) (currently )?(broken|failing|down|dead|not working)\b", re.IGNORECASE)
BROKEN_EVIDENCE = re.compile(
    r"error|traceback|exit code|failed with|exception|reproduced|restart|stderr|status [45]\d\d", re.IGNORECASE)

# A fix CLAIM needs a subject or a leading "Fixed:". A bare status word
# ("listed as pending and resolved") is not one.
FIXED = re.compile(
    r"\b(?:i|we|i'?ve|we'?ve|it'?s|that'?s|is|are|was|were|has been|have been|now|just)"
    r"\s+(?:now\s+|also\s+|finally\s+)?(?:fixed|repaired|resolved)\b"
    r"|^\W*(?:fixed|repaired|resolved)\b"
    r"|\b(?:working now|now works|(?:is|are) back up)\b", re.IGNORECASE | re.MULTILINE)
NEGATION = re.compile(r"\b(?:not|never|nothing|no)\b|n'?t\b", re.IGNORECASE)
FIXED_EVIDENCE = re.compile(
    r"re-?ran|passes|passed|verified|exit 0|test|output|now returns|confirmed by|screenshot", re.IGNORECASE)


def fixed_claim(text: str) -> bool:
    for m in FIXED.finditer(text):
        if not NEGATION.search(text[max(0, m.start() - 25):m.start() + 6]):
            return True
    return False


def classify(text: str, absence_evidence: str | None = None) -> tuple[str, str]:
    """Return (event, message) for the first unproven claim in ``text``, or ("", "")."""
    if not text:
        return "", ""
    evidence = re.compile(absence_evidence or DEFAULT_ABSENCE_EVIDENCE, re.IGNORECASE)
    if ABSENCE.search(text) and not evidence.search(text):
        return ("absence_claim",
                "PROOF OF SEARCH: you said a credential is absent without showing where you looked. "
                "Search the configured secret store, cite the searches you ran, then state the result.")
    if BROKEN.search(text) and not BROKEN_EVIDENCE.search(text):
        return ("broken_claim",
                "PROVENANCE: you said something is broken without a reproduced failure. "
                "Reproduce it (after a clean restart) and show the failing output, "
                "or say 'not yet verified' instead.")
    if fixed_claim(text) and not FIXED_EVIDENCE.search(text):
        return ("fixed_claim",
                "PROVENANCE: you said something is fixed without a passing re-run. "
                "Re-run it, show the passing output, then claim it.")
    return "", ""


def evaluate(final_text: str, session_id: str = "unknown", settings: dict | None = None) -> core.Verdict:
    settings = settings or core.gate_settings(GATE)
    state = core.session_state(session_id)
    if state.get("claims_fired", 0) >= int(settings.get("max_per_session", 2)):
        return core.Verdict(GATE)
    event, message = classify(final_text, settings.get("absence_evidence"))
    if not event:
        return core.Verdict(GATE)
    state["claims_fired"] = state.get("claims_fired", 0) + 1
    core.save_session_state(session_id, state)
    mode = "block" if settings.get("mode") == "block" else "advise"
    return core.Verdict(GATE, mode, message, event, {"snippet": final_text[:200]})
