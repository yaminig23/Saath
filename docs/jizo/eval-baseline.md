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

## Gaps the suite documents honestly (not yet fixed)

- Cat 10: "cancel that reminder" is read as a reminder missing a
  time — chat has no cancellation path. Jizo never claims it
  cancelled; the note asks for a time, which is honest but unhelpful.
- Chat reaches only 2 specialists (reminder, daily-organiser).
  Calendar, maps, Gmail, weather, bookings, tutor are screens-only.
  The planner schema provably cannot emit consequential tools
  (Cat 11) — safe, but narrow.
- Multi-turn history is flattened into one input string
  (`_build_responses_body`); the model never sees real turn roles.

## LIVE-ONLY (needs quota; not in the automated suite)

Quality judgement the suite cannot do: factual accuracy of direct
answers, Hindi reply quality, plan quality on genuinely ambiguous
requests, clarification skill, hallucination rate on open questions.
Run manually against the demo account before claiming them.
