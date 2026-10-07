"""grounding-gates command line.

  grounding-gates hook <gate> [--format claude-code|json]   run a gate on a stdin payload
  grounding-gates gates                                     list gate names
  grounding-gates directive add "<text>" --keywords a,b,c   | directive list
  grounding-gates answer add "<q>" "<a>"                    | answer find "<text>"
  grounding-gates complete "<task>" --proof P [--agent A]   | complete-check "<text>"
  grounding-gates mistake add --class C --text T [--agent A]
  grounding-gates defect add --gate G --text T
  grounding-gates replay <events.log>                       tier-B corpus report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import completion, core, hooks


def cmd_hook(args) -> int:
    return hooks.run(args.gate, args.format)


def cmd_gates(_args) -> int:
    for gate in hooks.ALL_GATES:
        settings = core.gate_settings(gate)
        print(f"{gate:17} {hooks.EVENT_NAMES[gate]:17} mode={settings.get('mode')}")
    return 0


def cmd_directive(args) -> int:
    reg = core.load_registry("standing_directives")
    ds = reg.setdefault("directives", [])
    if args.action == "add":
        if not args.text:
            print("directive text required", file=sys.stderr)
            return 2
        ds.append({"id": f"sd-{len(ds) + 1:03d}", "issued": core.now()[:10], "status": "live",
                   "text": args.text,
                   "keywords": [k.strip() for k in (args.keywords or "").split(",") if k.strip()]})
        core.save_registry("standing_directives", reg)
        print(f"registered {ds[-1]['id']}")
    else:
        for d in ds:
            print(f"{d['id']} [{d.get('status')}] {d['text']}")
    return 0


def cmd_answer(args) -> int:
    reg = core.load_registry("answers")
    answers = reg.setdefault("answers", [])
    if args.action == "add":
        if not args.a:
            print("answer text required", file=sys.stderr)
            return 2
        answers.append({"id": f"ans-{len(answers) + 1:04d}", "q": args.q, "a": args.a,
                        "date": core.now()[:10], "asked_by": core.agent_name(),
                        "keywords": core.keywords_for(args.q + " " + args.a)})
        core.save_registry("answers", reg)
        print(f"registered {answers[-1]['id']}")
    else:
        hit = core.match_question(args.q, answers)
        print(json.dumps(hit, ensure_ascii=False) if hit else "no match")
    return 0


def cmd_complete(args) -> int:
    core.append_jsonl("completions", {"task": args.task, "date": core.now()[:10],
                                      "proof": args.proof, "agent": args.agent or core.agent_name()})
    print("stamped")
    return 0


def cmd_complete_check(args) -> int:
    for row in core.read_jsonl("completions"):
        if core.keyword_score(args.text, str(row.get("task", "")).split()) >= 2:
            print(json.dumps(row, ensure_ascii=False))
            return 0
    print("no completion found")
    return 0


def cmd_mistake(args) -> int:
    core.append_jsonl("mistake_ledger", {"class": args.cls, "text": args.text,
                                         "agent": args.agent or core.agent_name(),
                                         "date": core.now()[:10]})
    print("ledgered")
    return 0


def cmd_defect(args) -> int:
    core.append_jsonl("mistake_ledger", {"class": "gate-defect", "gate": args.gate,
                                         "text": args.text, "date": core.now()[:10]})
    core.log("gate_defect", gate=args.gate, text=args.text[:200])
    print("gate defect ledgered: calibrate the gate, not the person")
    return 0


def cmd_replay(args) -> int:
    rows = []
    for line in Path(args.log).read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    print(json.dumps(completion.replay_warn_rows(rows), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grounding-gates")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hook", help="run one gate on a JSON payload from stdin")
    h.add_argument("gate", choices=hooks.ALL_GATES)
    h.add_argument("--format", choices=["claude-code", "json"], default="claude-code")
    h.set_defaults(func=cmd_hook)

    g = sub.add_parser("gates", help="list gates and their configured modes")
    g.set_defaults(func=cmd_gates)

    d = sub.add_parser("directive")
    d.add_argument("action", choices=["add", "list"])
    d.add_argument("text", nargs="?")
    d.add_argument("--keywords")
    d.set_defaults(func=cmd_directive)

    a = sub.add_parser("answer")
    a.add_argument("action", choices=["add", "find"])
    a.add_argument("q")
    a.add_argument("a", nargs="?")
    a.set_defaults(func=cmd_answer)

    c = sub.add_parser("complete")
    c.add_argument("task")
    c.add_argument("--proof", required=True)
    c.add_argument("--agent")
    c.set_defaults(func=cmd_complete)

    cc = sub.add_parser("complete-check")
    cc.add_argument("text")
    cc.set_defaults(func=cmd_complete_check)

    m = sub.add_parser("mistake")
    m.add_argument("action", choices=["add"])
    m.add_argument("--class", dest="cls", required=True)
    m.add_argument("--text", required=True)
    m.add_argument("--agent")
    m.set_defaults(func=cmd_mistake)

    df = sub.add_parser("defect")
    df.add_argument("action", choices=["add"])
    df.add_argument("--gate", required=True)
    df.add_argument("--text", required=True)
    df.set_defaults(func=cmd_defect)

    r = sub.add_parser("replay", help="summarize logged tier-B events")
    r.add_argument("log")
    r.set_defaults(func=cmd_replay)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
