# ArcturionGroundingGates

Hooks that make an AI agent prove its claims, look things up before answering,
and stop re-asking questions that are already settled.

Agents that can run tools make a few avoidable mistakes over and over. They
say "done, it's live" because an API returned 200, without looking at what the
user actually sees. They say a service is broken without reproducing an error.
They ask a question the user answered last week. They hand back the same menu
of options after the user already replied.

ArcturionGroundingGates turns the fixes for those mistakes into small hooks
that run at the right moment: when a prompt arrives, before and after the agent
asks the user a question, and when the agent is about to end its turn.

Python 3.10+, standard library only. No network access.

> **Portfolio project.** This is an open-source sample of the tooling behind
> Arcturion's multi-agent setup. It is not a commercial product and makes no
> claims about revenue or customers. All bundled data is synthetic.

## The gates

| Gate | Runs | What it does | Default |
| --- | --- | --- | --- |
| `grounding` | prompt submit | Adds matching standing directives, earlier answers, finished tasks and preferred tools to the context | inject |
| `factual-reminder` | prompt submit | One-line reminder: verify factual claims or label them unverified | inject |
| `question` | before the ask-user tool | Denies a question the user already answered (and returns the answer); bounces questions with no options | deny |
| `answer-capture` | after the ask-user tool | Stores every new answer with keywords, for the two gates above | capture |
| `completion` | end of turn | After a successful external write (email sent, message posted, push, curl POST to a public host), completion language needs an evidence chain | block |
| `claims` | end of turn | "There's no token", "it's broken" and "it's fixed" need a search trail, a reproduced error, or a passing re-run | advise |
| `factual` | end of turn | A confident recommendation with no citation and no hedge gets a note | advise |
| `option-loop` | end of turn | Re-offering a near-identical options menu after the user already replied | block |

"Advise" shows a message and lets the turn end. "Block" asks the agent to keep
working. Every mode is configurable per gate. [DESIGN.md](DESIGN.md) covers the
failure modes, the matching rules, and why most gates are tuned to miss rather
than misfire.

## Quickstart with Claude Code

```bash
git clone https://github.com/ArcturionTechnologies/arcturion-grounding-gates.git
cd arcturion-grounding-gates
pip install .                      # puts `grounding-gates` on PATH

# optional: start from the sample registries and settings
mkdir -p ~/.config/grounding-gates
cp examples/registries/*.json ~/.config/grounding-gates/
cp examples/claude-code/config.json ~/.config/grounding-gates/
```

Then merge [`examples/claude-code/settings.json`](examples/claude-code/settings.json)
into your Claude Code settings (`~/.claude/settings.json` for every project, or
`.claude/settings.json` for one). It wires each gate to its hook event:

```json
"Stop": [{ "hooks": [
  { "type": "command", "command": "grounding-gates hook completion" },
  { "type": "command", "command": "grounding-gates hook claims" },
  { "type": "command", "command": "grounding-gates hook factual" },
  { "type": "command", "command": "grounding-gates hook option-loop" }
]}]
```

No install? Point the commands at the shim instead:
`"$HOME/src/arcturion-grounding-gates/bin/grounding-hook" completion`.

Try a gate by hand:

```bash
echo '{"prompt": "Which test runner should this repo use for CI checks?"}' \
  | grounding-gates hook grounding
# GROUNDING (consult before acting):
# - ALREADY ANSWERED (2026-01-03): Q: Which test runner should this repo use for CI? -> A: ...
```

## Using it with another harness

The gate logic doesn't know about Claude Code. Each gate is a plain function
that takes text or transcript rows and returns a `Verdict`
(`allow` / `advise` / `block` / `deny` / `inject` plus a message). Two ways in:

- **Command line, neutral output.** `grounding-gates hook <gate> --format json`
  reads the same payload fields (`prompt`, `tool_input`, `tool_response`,
  `transcript_path`, `session_id`, `cwd`, `stop_hook_active`) and always prints
  `{"gate", "action", "message", ...}`. Map that to your harness.
- **Library.** `from grounding_gates import completion, core` and call
  `completion.evaluate(core.read_transcript(path))`.

Transcripts are JSON Lines. Each row carries `role` and `content`, either at
the top level or under `message`, with `text`, `tool_use` and `tool_result`
blocks. That's Claude Code's format and is easy to produce from others.

## The registries

Plain JSON files in `$GROUNDING_GATES_DATA` (default
`~/.config/grounding-gates`). Edit them by hand or with the CLI.

| File | Holds | Written by |
| --- | --- | --- |
| `standing_directives.json` | Instructions that stay in force until revoked | `grounding-gates directive add "<text>" --keywords a,b` |
| `answers.json` | Questions already answered, with keywords | `answer-capture`, or `grounding-gates answer add "<q>" "<a>"` |
| `tool_routing.json` | The preferred tool for a job, and what never to use | by hand |
| `completions.jsonl` | Finished tasks with a proof pointer | `grounding-gates complete "<task>" --proof <path>` |
| `mistake_ledger.jsonl` | Caught mistakes and gate defects | `grounding-gates mistake add` / `defect add` |

A gate that wrongly denies something is a defect in the gate. Record it with
`grounding-gates defect add --gate question --text "..."` and recalibrate.

## Configuration

Environment variables only. Nothing reads a credential store.

| Variable | Meaning | Default |
| --- | --- | --- |
| `GROUNDING_GATES_DATA` | Registry folder | `$XDG_CONFIG_HOME/grounding-gates`, else `~/.config/grounding-gates` |
| `GROUNDING_GATES_CONFIG` | Per-gate settings file | `<data>/config.json` |
| `GROUNDING_GATES_LOG` | JSON Lines event log | `<data>/gates.log` |
| `GROUNDING_GATES_OFF=1` | Turn every gate off | unset |
| `GROUNDING_GATES_SKIP` | Comma list of gates to turn off | unset |
| `GROUNDING_GATES_AGENT` | Name recorded with captured answers | `agent` |

A `.grounding-skip` file in a working folder turns every gate off there.

`config.json` sets each gate's `mode` (`block`, `advise`, `deny`, `inject` or `off`)
and a few knobs: `claims.max_per_session`, `claims.absence_evidence` (a regex
naming your secret store), `completion.external_write_tools` (a regex for your
own MCP tool names), `completion.tier_b_mode`, `question.exempt_markers`,
`question.craft_lint`, `grounding.max_chars`. See
[`examples/claude-code/config.json`](examples/claude-code/config.json).

## What is not included

No permission or security gating. These hooks never decide whether a command
may run; they only ask the agent to show its work. That boundary is deliberate
(see the end of [DESIGN.md](DESIGN.md)).

## Project layout

```
grounding_gates/core.py         config, registries, matching, transcript parsing (harness-neutral)
grounding_gates/grounding.py    lookup-before-answering injection
grounding_gates/questions.py    question dedupe + craft lint, answer capture
grounding_gates/completion.py   prove before claiming (tier A / tier B)
grounding_gates/claims.py       absence / broken / fixed provenance
grounding_gates/factual.py      verify-before-asserting reminder and audit
grounding_gates/option_loop.py  option-menu loop detection
grounding_gates/hooks.py        payload -> gate -> Claude Code or JSON output
grounding_gates/cli.py          `grounding-gates` command
bin/grounding-hook              run a gate without installing
examples/                       Claude Code settings, config, sample registries
tests/                          146 tests, stdlib unittest
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Every test runs against a temporary data folder with synthetic data and no
network. Hook tests run each gate as a real subprocess, the way a harness would.

## License

MIT. See [LICENSE](LICENSE).

Implementation is AI-assisted; architecture, requirements, and testing directed by Robert Lingoes.
