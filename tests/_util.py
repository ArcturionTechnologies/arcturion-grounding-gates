"""Shared test harness: an isolated data dir, seeded registries, a subprocess hook runner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_KEYS = ("GROUNDING_GATES_DATA", "GROUNDING_GATES_CONFIG", "GROUNDING_GATES_LOG",
            "GROUNDING_GATES_OFF", "GROUNDING_GATES_SKIP", "GROUNDING_GATES_AGENT")


def make_env(seed: dict | None = None, config: dict | None = None) -> tuple[dict, Path]:
    """Create a temp data dir and seed registries.

    ``seed`` maps a registry name to a dict (written as <name>.json) or, for
    names ending in .jsonl, a list of dict rows.
    """
    tmp = Path(tempfile.mkdtemp(prefix="grounding-gates-test-"))
    for name, data in (seed or {}).items():
        if name.endswith(".jsonl"):
            (tmp / name).write_text("".join(json.dumps(x) + "\n" for x in data))
        else:
            (tmp / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False))
    (tmp / "config.json").write_text(json.dumps(config or {}))
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env["GROUNDING_GATES_DATA"] = str(tmp)
    env["GROUNDING_GATES_LOG"] = str(tmp / "gates.log")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env, tmp


def run_hook(gate: str, payload: dict | None, env: dict, *, stdin_text: str | None = None,
             fmt: str = "claude-code") -> subprocess.CompletedProcess:
    source = stdin_text if stdin_text is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-m", "grounding_gates", "hook", gate, "--format", fmt],
        input=source, capture_output=True, text=True, env=env, timeout=15, cwd=str(ROOT),
    )


def write_transcript(path: Path, rows: list[dict]) -> str:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def text_row(role: str, text: str) -> dict:
    return {"type": role, "message": {"role": role, "content": [{"type": "text", "text": text}]}}


class EnvPatch:
    """Point the in-process core at a temp data dir for the duration of a test."""

    def __init__(self, env: dict):
        self.env = env
        self.saved: dict = {}

    def __enter__(self):
        for k in ENV_KEYS:
            self.saved[k] = os.environ.pop(k, None)
        for k in ENV_KEYS:
            if k in self.env:
                os.environ[k] = self.env[k]
        return self

    def __exit__(self, *exc):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
            if self.saved.get(k) is not None:
                os.environ[k] = self.saved[k]
        return False
