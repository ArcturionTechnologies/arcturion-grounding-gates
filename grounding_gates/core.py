"""Harness-neutral core: configuration, registries, matching, transcript parsing.

Nothing in this module knows about any particular agent harness. Gates take
plain Python data (prompt text, a list of transcript rows, a tool input dict)
and return a ``Verdict``. Adapters (see ``grounding_gates.adapters``) turn a
harness's hook payload into those inputs and a ``Verdict`` back into whatever
the harness expects on stdout.

Configuration comes from environment variables only:

  GROUNDING_GATES_DATA     registry folder   (default: $XDG_CONFIG_HOME/grounding-gates
                                              or ~/.config/grounding-gates)
  GROUNDING_GATES_CONFIG   per-gate settings  (default: <data>/config.json; optional)
  GROUNDING_GATES_LOG      JSONL event log    (default: <data>/gates.log)
  GROUNDING_GATES_OFF=1    turn every gate off
  GROUNDING_GATES_SKIP     comma list of gate names to turn off for this process
  GROUNDING_GATES_AGENT    name recorded with captured answers and log events

A file named ``.grounding-skip`` in the session's working directory turns every
gate off for that directory. All readers fail soft; ``log`` never raises.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKIP_FILE = ".grounding-skip"
MAX_FUZZY_CHARS = 6_000

# Default behaviour per gate. "mode" is what the gate does when it fires:
#   block   - a Stop gate asks the agent to keep working (hard)
#   advise  - a Stop gate shows a message but lets the turn end (soft)
#   deny    - a PreToolUse gate refuses the tool call with a reason
#   inject  - a prompt gate adds context before the model answers
#   off     - the gate is silent
DEFAULTS: dict[str, dict[str, Any]] = {
    "grounding": {"mode": "inject", "min_prompt_chars": 30, "max_chars": 3600},
    "question": {"mode": "deny", "craft_lint": True, "exempt_markers": []},
    "answer-capture": {"mode": "capture"},
    "claims": {"mode": "advise", "max_per_session": 2, "absence_evidence": None},
    "completion": {"mode": "block", "tier_b_mode": "off", "external_write_tools": None},
    "factual": {"mode": "advise"},
    "factual-reminder": {"mode": "inject"},
    "option-loop": {"mode": "block"},
}


@dataclass
class Verdict:
    """What a gate decided. ``action`` is one of allow/advise/block/deny/inject."""

    gate: str
    action: str = "allow"
    message: str = ""
    event: str = ""
    details: dict = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.action != "allow" and bool(self.message)

    def to_json(self) -> dict:
        return {"gate": self.gate, "action": self.action, "message": self.message,
                "event": self.event, "details": self.details}


# ── paths + config ───────────────────────────────────────────────────────────

def data_dir() -> Path:
    override = os.environ.get("GROUNDING_GATES_DATA")
    if override:
        p = Path(override).expanduser()
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        p = Path(base).expanduser() / "grounding-gates"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def log_file() -> Path:
    override = os.environ.get("GROUNDING_GATES_LOG")
    return Path(override).expanduser() if override else data_dir() / "gates.log"


def load_config() -> dict:
    override = os.environ.get("GROUNDING_GATES_CONFIG")
    path = Path(override).expanduser() if override else data_dir() / "config.json"
    try:
        cfg = json.loads(path.read_text())
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def gate_settings(gate: str, config: dict | None = None) -> dict:
    merged = dict(DEFAULTS.get(gate, {}))
    cfg = load_config() if config is None else config
    user = cfg.get(gate)
    if isinstance(user, dict):
        merged.update(user)
    return merged


def gate_enabled(gate: str, cwd: str | None = None, config: dict | None = None) -> bool:
    if os.environ.get("GROUNDING_GATES_OFF") == "1":
        return False
    skipped = {s.strip() for s in os.environ.get("GROUNDING_GATES_SKIP", "").split(",") if s.strip()}
    if gate in skipped:
        return False
    try:
        if cwd and (Path(cwd) / SKIP_FILE).exists():
            return False
    except OSError:
        pass
    return gate_settings(gate, config).get("mode", "off") != "off"


# ── registries ───────────────────────────────────────────────────────────────
#
#   standing_directives.json  {"directives": [{id, status, text, keywords}]}
#   answers.json              {"answers": [{id, q, a, date, asked_by, keywords}]}
#   tool_routing.json         {"routes": [{job, tool, never, keywords}]}
#   completions.jsonl         one {task, date, proof, agent} per line
#   mistake_ledger.jsonl      one {class, text, ...} per line

def load_registry(name: str) -> dict:
    try:
        data = json.loads((data_dir() / f"{name}.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_registry(name: str, data: dict) -> None:
    target = data_dir() / f"{name}.json"
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(name: str, obj: dict) -> None:
    with open(data_dir() / f"{name}.jsonl", "a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(name: str) -> list[dict]:
    out: list[dict] = []
    try:
        for line in (data_dir() / f"{name}.jsonl").read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


# ── session state ────────────────────────────────────────────────────────────

def _state_file(session_id: str) -> Path:
    d = data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))[:80]
    return d / f"{safe}.json"


def session_state(session_id: str) -> dict:
    try:
        return json.loads(_state_file(session_id).read_text())
    except (OSError, ValueError):
        return {}


def save_session_state(session_id: str, state: dict) -> None:
    try:
        _state_file(session_id).write_text(json.dumps(state))
    except OSError:
        pass


# ── matching ─────────────────────────────────────────────────────────────────

def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def keyword_score(text: str, keywords: list[str]) -> int:
    """Count keywords that appear (as normalized substrings) in ``text``."""
    t = norm(text)
    return sum(1 for k in keywords if k and norm(k) in t)


# High-frequency filler that must never carry a question match on its own.
# Learned from a real false positive: a question about large build plans was
# denied as a "duplicate" of an unrelated question about agent tone, because
# the stored keywords "much", "agent" and "build" were bare substrings of the
# new text. A wrongly denied question is the defect to fear, so this list is
# short and deliberate rather than aggressive.
MATCH_STOPWORDS = frozenset({
    "much", "work", "build", "agent", "real", "doing", "pass", "recommended",
    "does", "doesn", "should", "would", "could", "will", "with", "that", "this",
    "have", "having", "just", "really", "actually", "task", "thing", "things",
    "good", "well", "make", "making", "need", "needs", "want", "wants", "like",
    "session", "user", "question", "answer", "asked", "asking",
})

# A stored answer may carry keywords from the ANSWER's topic that a rephrased
# question will never contain (the asker doesn't know the answer yet), so the
# fallback needs only three load-bearing hits.
KEYWORD_FALLBACK_MIN_HITS = 3

FUZZY_STOPWORDS = MATCH_STOPWORDS | frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "do", "for", "from", "get", "has", "how", "i", "if", "in", "is", "it",
    "its", "me", "my", "of", "on", "or", "our", "please", "so", "some",
    "the", "their", "them", "there", "these", "they", "to", "us", "we",
    "what", "when", "where", "which", "who", "why", "you", "your",
})
OPPOSITE_ACTIONS = (
    (frozenset({"enable", "activate", "allow", "start"}),
     frozenset({"disable", "deactivate", "deny", "stop"})),
    (frozenset({"keep", "retain", "preserve"}),
     frozenset({"delete", "remove", "discard"})),
)


def keyword_hits(text: str, keywords: list[str]) -> int:
    """Whole-word, non-filler keyword hits of ``keywords`` inside ``text``."""
    t = norm(text)
    if not t:
        return 0
    hits = 0
    for keyword in keywords:
        nk = norm(keyword)
        if not nk or nk in MATCH_STOPWORDS:
            continue
        if re.search(rf"\b{re.escape(nk)}\b", t):
            hits += 1
    return hits


def _bounded(text: str) -> str:
    if len(text) <= MAX_FUZZY_CHARS:
        return text
    half = MAX_FUZZY_CHARS // 2
    return text[:half] + " " + text[-half:]


def _question_intent(text: str) -> str:
    match = re.search(r"\b(why|where|when|how)\b", text)
    if not match:
        return ""
    if match.group(1) == "how" and re.match(r"how\s+(much|many)\b", text[match.start():]):
        return "quantity"
    return match.group(1)


def compatible_intent(q: str, stored: str) -> bool:
    """Why/where/when/how, negation and opposite verbs make a different question."""
    first, second = _question_intent(q), _question_intent(stored)
    if first and second and first != second:
        return False
    qt, st = set(q.split()), set(stored.split())
    negations = {"not", "never", "without"}
    if bool(qt & negations) != bool(st & negations):
        return False
    for positive, negative in OPPOSITE_ACTIONS:
        if (qt & positive and st & negative) or (qt & negative and st & positive):
            return False
    return True


def content_overlap(q: str, stored: str) -> bool:
    """Require shared discriminating words; shared question grammar is not a topic."""
    qt = set(q.split()) - FUZZY_STOPWORDS
    st = set(stored.split()) - FUZZY_STOPWORDS
    if not qt or not st:
        return False
    shared = qt & st
    if min(len(qt), len(st)) < 2:
        return qt == st
    return len(shared) >= 2 and len(shared) / len(qt | st) >= 0.5


def match_question(q: str, answers: list[dict], threshold: float = 0.55) -> dict | None:
    """Return the stored answer most similar to ``q``, or None.

    Exact normalized duplicates always match. A fuzzy match needs compatible
    intent, discriminating content overlap and a SequenceMatcher ratio at or
    above ``threshold``, or at least KEYWORD_FALLBACK_MIN_HITS whole-word,
    non-filler keyword hits.
    """
    best, best_ratio = None, 0.0
    nq = norm(q)
    if not nq:
        return None
    fuzzy_q = _bounded(nq)
    for a in answers:
        stored = norm(a.get("q", ""))
        if not stored:
            continue
        if nq.split() == stored.split():
            return a
        if not compatible_intent(nq, stored):
            continue
        ratio = difflib.SequenceMatcher(None, fuzzy_q, stored).ratio()
        if ratio >= threshold and ratio > best_ratio and content_overlap(nq, stored):
            best, best_ratio = a, ratio
        elif best is None and keyword_hits(nq, a.get("keywords", [])) >= KEYWORD_FALLBACK_MIN_HITS:
            best = a
    return best


_KEYWORD_STOP = {"this", "that", "with", "from", "have", "what", "which", "should",
                 "would", "could", "your", "them", "then", "than", "into", "does",
                 "about", "there", "here", "when", "where", "will", "want", "like"}


def keywords_for(text: str, cap: int = 8) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", text.lower())
    out: list[str] = []
    for w in words:
        if w not in _KEYWORD_STOP and w not in out:
            out.append(w)
        if len(out) >= cap:
            break
    return out


# ── transcripts ──────────────────────────────────────────────────────────────
#
# A transcript is JSON Lines. Each row has a role and content, either at the top
# level ({"role", "content"}) or nested ({"type", "message": {"role", "content"}}).
# Content is a string or a list of blocks: {"type": "text"}, {"type": "tool_use",
# "id", "name", "input"} and {"type": "tool_result", "tool_use_id", ...}.

def parse_rows(transcript_text: str) -> list[dict]:
    rows = []
    for line in transcript_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def row_role(row: dict) -> str:
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    return row.get("role") or message.get("role", "")


def row_content(row: dict):
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    content = row.get("content")
    if content is None:
        content = message.get("content", "")
    return content


def row_text(row: dict) -> str:
    content = row_content(row)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def is_human_turn(row: dict) -> bool:
    """True for a real user message, not a tool_result echo stored as a user row."""
    if row_role(row) != "user" and row.get("type") != "user":
        return False
    content = row_content(row)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def current_turn_rows(rows: list[dict]) -> list[dict]:
    """Rows after the latest human message."""
    last_human = -1
    for i, row in enumerate(rows):
        if is_human_turn(row):
            last_human = i
    return rows[last_human + 1:]


def last_assistant_text(rows: list[dict]) -> str:
    """Text of the last assistant row since the latest human message."""
    last = ""
    for row in current_turn_rows(rows):
        if row_role(row) == "assistant":
            last = row_text(row)
    return last


def text_turns(rows: list[dict]) -> list[tuple[str, str]]:
    """Ordered (role, text) pairs for user/assistant rows that contain text."""
    turns = []
    for row in rows:
        role = row_role(row)
        if role not in ("user", "assistant"):
            continue
        text = row_text(row)
        if text.strip():
            turns.append((role, text))
    return turns


def read_transcript(path: str | None) -> list[dict] | None:
    if not path:
        return None
    try:
        return parse_rows(Path(os.path.expanduser(path)).read_text(errors="replace"))
    except (OSError, ValueError, TypeError):
        return None


# ── misc ─────────────────────────────────────────────────────────────────────

def agent_name() -> str:
    return os.environ.get("GROUNDING_GATES_AGENT") or "agent"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(event: str, **kv) -> None:
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps({"ts": now(), "event": event, **kv}, ensure_ascii=False) + "\n")
    except Exception:
        pass
