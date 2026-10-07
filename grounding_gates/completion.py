"""Prove before claiming: completion language needs an evidence chain.

  Tier A (default: block)
    In the current human turn the agent made a SUCCESSFUL externally visible
    write (sent an email, posted a message, pushed, a curl POST to a public
    host, ...) and its final message says "done / sent / live / created"
    without citing evidence. This is the classic failure: the API returned
    200, the agent reported success, and nobody looked at the surface the
    user actually sees.

  Tier B (default: off)
    The same completion language after any tool use, internal or external.
    Turn it on with ``"completion": {"tier_b_mode": "advise"}``. It is pure
    language matching, so it should not hard-block.

Evidence is an observation chain, not prose. Words like "verified", a code
fence, or "I couldn't check" do not count. The final message must carry all
of: request_id, predicate, command/observation, exit/status, artifact,
sha256, readback, and observed_at/generated_at (key=value or JSON).

Only a typed tool_use/tool_result pair counts as an action: quoting
"curl -X POST" in text, or reading a log that contains one, does not.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from . import core

GATE = "completion"

# Named external surfaces. A bare "send_message" is an internal collaboration
# call on several harnesses, so only named external tools count. Extend with
# ``"completion": {"external_write_tools": "<regex>"}`` for your own MCP tools.
DEFAULT_EXTERNAL_WRITE_TOOLS = (
    r"mcp__gmail__send|mcp__outlook__send"
    r"|mcp__\w+__gmail_(?:send_email|send_draft|forward_emails)"
    r"|mcp__slack__post|mcp__discord__send|mcp__telegram__send_message"
    r"|mcp__google-calendar__create|mcp__apple-calendar__create|create_calendar_event"
    r"|mcp__notion-?\w*__notion-create-pages|mcp__notion-?\w*__notion-update-page"
    r"|mcp__github__create_pull_request|mcp__github__push|mcp__github__delete"
)

SHELL_TOOLS = {"Bash", "shell", "unified_exec", "local_shell", "exec_command", "functions.exec_command"}
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

COMPLETION = re.compile(
    r"\b("
    r"(?:is\s+)?(?:now\s+)?(?:live|done|complete|completed|finished|ready)"
    r"|(?:has\s+been\s+)?(?:sent|created|saved|deployed|shipped|installed|configured"
    r"|pushed|posted|uploaded|published|activated|enabled|fired|triggered|scheduled)"
    r"|(?:successfully\s+)?(?:set\s+up|set\s+live|went\s+live)"
    r"|all\s+(?:done|set|good|green)"
    r")\b",
    re.IGNORECASE,
)

EVIDENCE_FIELDS = (
    re.compile(r'(?<!\w)["\']?request[_ -]?id["\']?\s*[:=]\s*["\']?[^"\'\s,;}]+', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?predicate["\']?\s*[:=]\s*["\']?[^"\'\s,;}]+', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?(?:command|observation)["\']?\s*[:=]\s*["\']?[^"\'\s,;}]+', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?(?:exit|status)["\']?\s*[:=]\s*["\']?(?:0|ok|pass|2\d{2})\b', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?artifact["\']?\s*[:=]\s*["\']?[^"\'\s,;}]+', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?sha256["\']?\s*[:=]\s*["\']?[0-9a-f]{64}\b', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?readback["\']?\s*[:=]\s*["\']?[^"\'\s,;}]+', re.IGNORECASE),
    re.compile(r'(?<!\w)["\']?(?:observed_at|generated_at)["\']?\s*[:=]\s*["\']?\d{4}-\d{2}-\d{2}T\S+',
               re.IGNORECASE),
)


def has_authoritative_evidence(text: str) -> bool:
    return bool(text) and all(p.search(text) for p in EVIDENCE_FIELDS)


def tool_evidence(rows: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair typed calls with typed results by call id within the current human turn."""
    calls, results = [], {}
    for row in core.current_turn_rows(rows):
        content = core.row_content(row)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                calls.append(block)
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[block["tool_use_id"]] = block
    return [(call, results.get(call["id"])) for call in calls]


def turn_had_tool_use(rows: list[dict]) -> bool:
    """False for a purely conversational reply, so plain chat is never gated."""
    return bool(tool_evidence(rows))


def result_succeeded(result: dict | None) -> bool:
    if result is None or result.get("is_error") or result.get("isError"):
        return False
    if result.get("effect_disposition") in {"none", "unknown"}:
        return False
    candidates = [result]
    content = result.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = None
    if isinstance(content, dict):
        candidates.append(content)
    for candidate in candidates:
        if candidate.get("is_error") or candidate.get("isError"):
            return False
        if str(candidate.get("status", "")).lower() in {"error", "failed", "cancelled"}:
            return False
        for key in ("exit_code", "exitCode"):
            if key in candidate and candidate[key] != 0:
                return False
    return True


def _public_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return bool(host) and host not in {"localhost", "127.0.0.1", "::1"}


def shell_write_hint(name: str, tool_input: dict) -> str:
    if name not in SHELL_TOOLS:
        return ""
    command = tool_input.get("cmd", tool_input.get("command", ""))
    if not isinstance(command, str):
        return ""
    try:
        args = shlex.split(command)
    except ValueError:
        return ""
    if not args:
        return ""
    binary = Path(args[0]).name
    if binary == "curl":
        method = "GET"
        for i, arg in enumerate(args):
            if arg in {"-X", "--request"} and i + 1 < len(args):
                method = args[i + 1].upper()
            elif arg.startswith("--request="):
                method = arg.split("=", 1)[1].upper()
            elif arg in {"-d", "--data", "--data-raw", "--json", "-F", "--form"}:
                method = "POST"
        if method in WRITE_METHODS and any(_public_url(a) for a in args):
            return f"curl {method}"
    if binary in {"http", "https"} and any(a.upper() in WRITE_METHODS for a in args[1:3]):
        if any(_public_url(a) for a in args):
            return f"{binary} external write"
    return ""


def successful_external_hint(rows: list[dict], tools_regex: str | None = None) -> str:
    pattern = re.compile(tools_regex or DEFAULT_EXTERNAL_WRITE_TOOLS, re.IGNORECASE)
    for call, result in tool_evidence(rows):
        if not result_succeeded(result):
            continue
        name = call.get("name", "")
        if not isinstance(name, str):
            continue
        if pattern.fullmatch(name):
            return name
        tool_input = call.get("input")
        if isinstance(tool_input, dict):
            hint = shell_write_hint(name, tool_input)
            if hint:
                return hint
    return ""


TIER_A_REASON = (
    "PROVE BEFORE CLAIMING (tier A): an externally visible action was taken ({hint!r}) and "
    "your final message uses completion language, but cites no evidence.\n\n"
    "The user cannot verify this from the message alone. Before it reaches them, either:\n"
    "  1. Read back the surface the user will actually see (fetch the playlist, open the "
    "calendar, check the sent folder) and include the result, or\n"
    "  2. If readback is impossible, label the work partial/unverified and drop the "
    "completion claim. Saying you could not check is disclosure, not proof.\n\n"
    "Bypass for this folder: touch .grounding-skip (only with independent proof)."
)
TIER_B_NOTE = (
    "PROVE BEFORE CLAIMING (tier B): completion language without a request-bound predicate, "
    "command/result, artifact hash, readback and fresh timestamp. Provide that chain or label "
    "the work partial/unverified."
)


def tier_a_check(rows: list[dict], last_turn: str, tools_regex: str | None = None) -> tuple[bool, str, str]:
    """Return (fires, reason, tool_hint)."""
    if not last_turn or not COMPLETION.search(last_turn):
        return False, "", ""
    hint = successful_external_hint(rows, tools_regex)
    if not hint:
        return False, "", ""
    if has_authoritative_evidence(last_turn):
        return False, "", hint
    return True, TIER_A_REASON.format(hint=hint.strip()), hint


def tier_b_check(last_turn: str, turn_did_work: bool = True) -> tuple[bool, str]:
    if not last_turn or not COMPLETION.search(last_turn):
        return False, ""
    if not turn_did_work:
        return False, ""
    if has_authoritative_evidence(last_turn):
        return False, ""
    return True, TIER_B_NOTE


def evaluate(rows: list[dict], settings: dict | None = None) -> core.Verdict:
    settings = settings or core.gate_settings(GATE)
    last_turn = core.last_assistant_text(rows)
    fires, reason, hint = tier_a_check(rows, last_turn, settings.get("external_write_tools"))
    if fires:
        mode = "advise" if settings.get("mode") == "advise" else "block"
        return core.Verdict(GATE, mode, reason, "tier_a", {"tool_hint": hint, "snippet": last_turn[:300]})
    tier_b_mode = settings.get("tier_b_mode", "off")
    if tier_b_mode in ("advise", "block"):
        warn, note = tier_b_check(last_turn, turn_had_tool_use(rows))
        if warn:
            return core.Verdict(GATE, tier_b_mode, note, "tier_b", {"snippet": last_turn[:300]})
    return core.Verdict(GATE)


def replay_warn_rows(rows: list[dict]) -> dict:
    """Replay a corpus of logged tier-B events without inventing ground truth.

    Logged snippets are truncated, so a missing match is not a proven false
    positive. Report observable coverage and leave the false-positive rate
    UNKNOWN until the corpus is labelled by a person.
    """
    warns = [r for r in rows if r.get("event") == "tier_b"]
    matches = [bool(COMPLETION.search(str(r.get("snippet", "")))) for r in warns]
    evaluable = sum(matches)
    return {
        "warn_total": len(warns),
        "evaluable": evaluable,
        "block_candidates": evaluable,
        "unknown_truncated": len(warns) - evaluable,
        "false_positive_rate": "UNKNOWN",
        "tuning_status": "NOT_CLAIMED",
    }
