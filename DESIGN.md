# Design notes

These gates exist because capable agents make the same few avoidable mistakes,
and a written rule ("verify before you claim") doesn't stop them. A rule that
runs as code at the right moment does.

## Three failure modes

| Failure | What it looks like | Gate |
| --- | --- | --- |
| **Unproven claims** | "Done, it's live." The API returned 200, but nobody looked at the page the user sees. "The service is broken" with no error shown. "We don't have a key for that" without searching. | `completion`, `claims`, `factual` |
| **Answering without a lookup** | Re-doing a task that was finished last week. Ignoring a standing instruction because it wasn't in this session's context. Using an ad-hoc tool when a sanctioned one exists. | `grounding`, `factual-reminder` |
| **Re-asking settled questions** | Asking the user something they already answered. Handing back the same options menu after they replied. | `question`, `answer-capture`, `option-loop` |

## Working habits the gates encode

1. **Simulate before you execute.** Play a consequential action forward to its end state first.
2. **Serve the evident goal, not just the literal words.** When the letter and the intent
   of a request diverge, say so.
3. **Re-verify any state you are about to rely on.** Files, servers, tokens and task
   status change between sessions.
4. **Treat anomalies as stop signs.** When a result doesn't fit the story, diagnose
   before pushing on. Check magnitudes.
5. **Never claim what you didn't observe.** Every factual claim traces to a tool call,
   a file, or a search, or it is labelled "not yet verified".
6. **Connect what you already know.** Before asking or acting, consult the registries.
   Knowing A and B and acting on neither is the signature avoidable mistake.

## Asking good questions

- **Name the decision the question unblocks.** If you can't, you don't need the question yet.
- **Search before asking.** A question whose answer is already recorded is a defect.
- **One concept per question.** Never double-barreled, never leading.
- **Offer 2-4 concrete options with a recommendation.** People critique faster than they specify.
- **Stop when the decision is unblocked**, not when curiosity is satisfied.

The `question` gate enforces the mechanical parts: dedupe against stored
answers, and bounce a question that has no options or is too short to name a
decision.

## Example incidents (generic)

These are the kinds of incidents the tests replay.

- **The 200 that wasn't.** An agent created a calendar event through an API, got a
  success response, and said "it's on your calendar". The event had landed on the
  wrong calendar. Fix: after an external write, the final message needs an evidence
  chain that includes a readback of the user-facing surface (`completion`, tier A).
- **The missing token that existed.** An agent said "we don't have an API key for the
  DNS provider" and stopped. The key was in the secret store under a different name.
  Fix: absence claims need a visible search trail (`claims`).
- **"Broken" from a hunch.** An agent blamed a message bridge for a delivery failure
  without reproducing anything. The real cause was elsewhere. Fix: "broken" needs a
  reproduced error; "fixed" needs a passing re-run (`claims`).
- **The duplicate that wasn't.** A new question about planning a large build was
  denied as a duplicate of an older question about agent tone, because the stored
  keywords "much", "agent" and "build" all appeared in the new text. Fix: whole-word
  matching, a filler-word stoplist, and intent checks (`core.match_question`).
- **The settled theme question.** An agent asked which dashboard theme to use. The user
  had answered that exact question a week earlier. Fix: answers are captured after
  every structured question and checked before the next one (`question`, `answer-capture`).
- **The menu loop.** The user replied "I'm still thinking", and the agent re-sent the same
  two-option menu instead of recommending one. Fix: `option-loop`.

## How matching works

`core.match_question` decides whether a new question repeats a stored one.

1. Exact match after normalization (lowercase, punctuation stripped) always matches.
2. Otherwise the two questions must have **compatible intent**: the same leading
   why/where/when/how (with "how much/many" as its own intent), the same negation
   polarity, and no opposite verbs (enable vs. disable, keep vs. delete).
3. Then either a **fuzzy match** (SequenceMatcher ratio >= 0.55 plus at least two shared
   non-filler words making up half the vocabulary) or a **keyword fallback** (three or
   more whole-word, non-filler keyword hits).

Long prompts are trimmed to their first and last 3,000 characters before the fuzzy
comparison so a pasted log can't make the hook slow.

## False positives are the design constraint

Every gate here is pattern matching over natural language, so each one is tuned
to miss rather than to misfire:

- **Advisory by default** where the signal is language alone (`claims`, `factual`).
  `completion` tier A blocks only when there is a typed, successful external write in
  the same turn, not on wording alone. Tier B (wording alone) ships off.
- **Caps**: `claims` fires at most twice per session.
- **No loops**: Stop gates stand down when the harness reports `stop_hook_active`.
- **Fail open**: an unreadable payload or transcript means "allow". A gate that can't
  see its inputs must never trap the agent.
- **Escape hatches**: `.grounding-skip` in the working folder, `GROUNDING_GATES_OFF=1`,
  or `GROUNDING_GATES_SKIP=<gate,...>`.
- **Defects are recorded**: `grounding-gates defect add --gate <g> --text ...` logs a
  wrong denial so the gate gets recalibrated, not the person.

## What is deliberately not here

This repo does not decide whether an action is *allowed*. Permission and
security gating (which commands may run, which destinations are off limits)
is a separate concern with a different threat model, and pattern-matching
heuristics like these are the wrong tool for it. These gates only ask an agent
to show its work.
