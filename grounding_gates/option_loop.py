"""Catch option-menu loops: the agent re-offers a menu the user already answered.

Deliberately narrow. ALL of these must hold:

  (a) The FINAL assistant message contains an options block (2+ numbered
      items with a question anchored to them, or a "would you like A or B?"
      shape) and NO recommendation marker (recommend / my pick / I suggest /
      default: / (Recommended)). The agent handed over a menu instead of deciding.
  (b) An EARLIER assistant message in the same transcript has a substantially
      similar options block: Jaccard overlap of significant words >= 0.6.
  (c) At least one real user message sits between the two offers, and none of
      them asks to see the menu again ("what were the options?", "repeat those").

A fresh menu, a menu with a recommendation, or a menu the user asked to see
again always passes. Fewer than three assistant messages is not enough
history to prove a loop. An ordinary numbered recap followed by an unrelated
question in a separate paragraph ("...\\n\\nAnything else?") is not a menu.
"""
from __future__ import annotations

import re

from . import core

GATE = "option-loop"

MIN_ASSISTANT_TURNS = 3
OVERLAP_THRESHOLD = 0.6

# Trailing strip is [ \t]* (not \s*) so the match never swallows the newline;
# the anchor check needs to tell "immediately after" from "a new paragraph".
_NUMBERED_ITEM = re.compile(r"^\s*\d+[.)]\s+(.+?)[ \t]*$", re.MULTILINE)

_ALT_QUESTION = re.compile(
    r"\b(?:would\s+you\s+like|do\s+you\s+want|should\s+i|"
    r"which\s+(?:one\s+)?do\s+you\s+(?:want|prefer)|want\s+me\s+to)\b"
    r"(?P<body>.{0,160}?)\?",
    re.IGNORECASE,
)
_OR_SPLIT = re.compile(r"\bor\b", re.IGNORECASE)

_RECOMMENDATION_MARKER = re.compile(
    r"\brecommend(?:ed|s|ation)?\b"
    r"|\bmy\s+pick\b"
    r"|\bi\s+suggest\b"
    r"|\bdefault\s*:"
    r"|\(recommended\)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "is", "are", "for", "in",
    "on", "with", "would", "you", "like", "do", "want", "should", "i", "we",
    "it", "this", "that", "me", "my", "your", "be", "as", "at", "by", "if",
    "so", "go", "either",
}
_WORD = re.compile(r"[a-z0-9]+")

# Re-presentation REQUESTS stand the pairing down. These are anchored to request
# phrasing, not bare keywords: "option"/"choice" appear in nearly every ANSWER
# to a menu ("option 1 please"), and matching them would defeat the gate on
# real loops. If a message both selects and asks to see the menu again, the
# request wins (ambiguity resolves toward not blocking).
_REOFFER_REQUEST = re.compile(
    r"\bwhat\s+(?:were|was)\s+(?:the\s+)?(?:options?|choices?)\b"
    r"|\b(?:repeat|show|list)\s+(?:the\s+)?(?:options?|choices?|menu)\b"
    r"|\b(?:repeat|show|list)\s+those\b"
    r"|\bremind\s+me\s+(?:of\s+)?(?:the\s+)?(?:options?|choices?)\b"
    r"|\b(?:the\s+)?(?:options?|choices?)\s+again\b"
    r"|\brun\s+(?:those|that|them)\s+by\s+me\s+again\b",
    re.IGNORECASE,
)

_DETAIL_FLAG = re.compile(r"^\s*!(?:detail|long|full)\b", re.IGNORECASE)

REASON = (
    "OPTION LOOP: this message re-offers an options menu that closely matches one already "
    "offered in this session, after the user already responded. Re-asking a settled choice "
    "wastes their time.\n\n"
    "Rewrite this message: decide, state the pick with a one-line reason, and proceed."
)


def _menu_question_anchored(text: str, matches: list[re.Match]) -> bool:
    """Does a "?" belong to this numbered block, not just appear somewhere else?

    Anchored means a "?" on an option line, in the text right after the last
    option (before a blank line), or in the paragraph right above the first.
    """
    for m in matches:
        if "?" in m.group(0):
            return True
    last_end = matches[-1].end()
    trailing = text[last_end:last_end + 220]
    if "?" in re.split(r"\n\s*\n", trailing, maxsplit=1)[0]:
        return True
    first_start = matches[0].start()
    leading = text[max(0, first_start - 220):first_start]
    if "?" in re.split(r"\n\s*\n", leading)[-1]:
        return True
    return False


def option_menu_present(text: str) -> tuple[bool, list[str]]:
    if not text:
        return False, []
    matches = list(_NUMBERED_ITEM.finditer(text))
    if len(matches) >= 2:
        lines = [m.group(1).strip() for m in matches if m.group(1).strip()]
        if len(lines) >= 2 and _menu_question_anchored(text, matches):
            return True, lines
    for m in _ALT_QUESTION.finditer(text):
        body = m.group("body")
        if not _OR_SPLIT.search(body):
            continue
        parts = [p.strip(" ,.;:—-") for p in _OR_SPLIT.split(body)]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return True, parts
    return False, []


def _final_turn_qualifies(text: str) -> tuple[bool, list[str]]:
    found, lines = option_menu_present(text)
    if not found or _RECOMMENDATION_MARKER.search(text):
        return False, []
    return True, lines


def _tokenize(lines: list[str]) -> set[str]:
    words: set[str] = set()
    for line in lines:
        for w in _WORD.findall(line.lower()):
            if len(w) > 1 and w not in _STOPWORDS:
                words.add(w)
    return words


def token_overlap(a_lines: list[str], b_lines: list[str]) -> float:
    a, b = _tokenize(a_lines), _tokenize(b_lines)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _user_between(turns: list[tuple[str, str]], start: int, end: int) -> bool:
    found_user = False
    for i in range(start + 1, end):
        role, text = turns[i]
        if role != "user":
            continue
        found_user = True
        if _REOFFER_REQUEST.search(text):
            return False
    return found_user


def detail_mode(turns: list[tuple[str, str]]) -> bool:
    last = ""
    for role, text in turns:
        if role == "user" and text.strip():
            last = text
    return bool(_DETAIL_FLAG.match(last))


def decide(turns: list[tuple[str, str]]) -> tuple[bool, str]:
    """Pure decision over (role, text) turns. Returns (fires, reason)."""
    assistant_positions = [i for i, (r, _t) in enumerate(turns) if r == "assistant"]
    if len(assistant_positions) < MIN_ASSISTANT_TURNS:
        return False, ""
    final_pos = assistant_positions[-1]
    qualifies, final_lines = _final_turn_qualifies(turns[final_pos][1])
    if not qualifies:
        return False, ""
    for pos in assistant_positions[:-1]:
        found, prior_lines = option_menu_present(turns[pos][1])
        if not found or token_overlap(final_lines, prior_lines) < OVERLAP_THRESHOLD:
            continue
        if _user_between(turns, pos, final_pos):
            return True, REASON
    return False, ""


def evaluate(rows: list[dict], settings: dict | None = None) -> core.Verdict:
    settings = settings or core.gate_settings(GATE)
    turns = core.text_turns(rows)
    if detail_mode(turns):
        return core.Verdict(GATE)
    fires, reason = decide(turns)
    if not fires:
        return core.Verdict(GATE)
    final_text = next((t for r, t in reversed(turns) if r == "assistant"), "")
    mode = "advise" if settings.get("mode") == "advise" else "block"
    return core.Verdict(GATE, mode, reason, "option_loop", {"snippet": final_text[:300]})
