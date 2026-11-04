# Jizo Eval — Baseline and After-Change Record

Suite: `tests/test_jizo_eval.py` — 15 deterministic categories, scripted
model clients, throwaway database, no network, no quota.

## Baseline (before Jizo mission changes, 2026-11-04 ~23:15 IST)

Ran 15 tests in ~2s: **11 pass, 4 fail.**

- 2 failures were suite bugs (offline client refused direct answers;
  fixed in the suite, not the product).
- 2 failures were real product gaps:
  - Cat 12: no healthcare boundary in the system prompt.
  - Cat 13: direct answers had no token budget (uncapped cost/latency).

## After Stage 4 slice 1 (same night)

Ran 15 tests in ~2s: **15 pass.**

Product changes (all covered by the suite + existing regression tests):

1. Constitution v1.0 → v1.1: added the healthcare boundary (organise
   appointments/records/reminders only; never diagnose, interpret
   results, prescribe/change medication or dosage, or give medical
   advice; direct to qualified professionals / emergency services).
   Pin in `tests/test_outcomes.py` advanced deliberately.
2. `DIRECT_MAX_OUTPUT_TOKENS = 1500` on direct answers (planner 2000,
   lessons 4000): simple turns stay fast and cheap.
3. Router notes now ride into direct answers: previously a
   "need a time" / "didn't understand" note was silently dropped
   whenever the model answered directly; now the model sees and can
   address it.

## After Stage 4 slice 2 (same night)

Chat learned read-only tool lookups: the planner may emit agent
"tool" for exactly four harmless tools (maps_search_places,
maps_route, calendar_read_day, task_stats); the manager runs each as
an audited tool-runner child, summarises it in plain words, and
verifies the stored row. Consequential tools stay unemittable
(validation drops them, execution re-refuses them).

Ran the full suite: **248 tests green** (233 + 6 planning + 7
orchestration + 2 eval). No live-model verification yet: the new
`params` string keeps the schema strict-compatible, and any planner
failure still falls back to the rules router.

## After Stage 4 slice 3 (same night)

History fidelity + trace IDs. Multi-turn history now travels as a
typed Responses message list (`input_text` user turns, `output_text`
replayed assistant turns — the documented shape); lone user turns
stay plain strings, so planners, tutor, outcomes, and video send
byte-identical bodies to before. Every chat turn mints a 12-hex
trace id stamped on central, manager, and tool-child evidence and
returned to the caller.

**Live-verified**, throwaway DB, real key: threaded "My name is
Probe" → "What is my name?" returned "You told me your name is
Probe." (muse-spark-1.3, trace d225290700f4). The new wire format
is proven, not assumed.

Ran the full suite: **251 tests green** (+1 model mapping, +2
trace).

## Gaps the suite documents honestly (not yet fixed)

- Cat 10: "cancel that reminder" is read as a reminder missing a
  time — chat has no cancellation path. Jizo never claims it
  cancelled; the note asks for a time, which is honest but unhelpful.
- Chat reaches 2 specialists + 4 read-only tools. Gmail, bookings,
  weather, tutor stay screens-only by design (all need approval
  clicks or send data outward).
- Streaming and safe-op caching were evaluated and deferred: chat
  turns are small and already fast; briefings, maps, and calendar
  data go stale, so a wrong cache costs more than a fresh call.

## LIVE-ONLY (needs quota; not in the automated suite)

Quality judgement the suite cannot do: factual accuracy of direct
answers, Hindi reply quality, plan quality on genuinely ambiguous
requests, clarification skill, hallucination rate on open questions.
Run manually against the demo account before claiming them.
