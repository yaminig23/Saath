# Saath — Architecture Slide Points

## One line
A Muse-powered AI companion that understands plain words, delegates
to specialist agents, executes through approval gates, and teaches
with locally-generated video — in English and Hindi.

## The stack (one slide)
- **Brain:** Muse Spark 1.3 (Meta Model API) for understanding,
  planning, lesson storyboards, illustrations — key backend-only.
- **Body:** FastAPI + SQLAlchemy + SQLite; React + TypeScript +
  Vite + Tailwind, mobile-first, served same-origin.
- **Deterministic core:** kernel (tasks/audit), approvals (single-use,
  expiring), memory (5 layers + consent), worker (retries, follow-up).
- **Proof:** 218 backend tests, voice regression (44 assertions),
  eslint clean, every claim backed by a stored row.

## The agent org (one slide)
Central Assistant → Daily Life Manager → specialists (reminders,
organiser, travel, bookings, weather, tutor, maps). Delegation depth
≤ 3, cycle-protected, every handoff an audited parent/child task.

## What is REAL (one slide — the differentiator)
- Live Muse chat with honest offline fallback (never fake replies).
- Real Google Calendar read + write, real Gmail send (per-user OAuth).
- Real Google Places + Routes with LIVE badges and spend guards.
- Real MP4 lessons: Muse plans → FFmpeg animates → ffprobe verifies.
- Real approvals, real audit log, real Hindi replies + lessons.

## What is HONESTLY demo (one slide — the trust slide)
- Bookings run the full workflow but providers are fictional:
  every reference is labeled DEMO-XXXX, reserves nothing.
- Volunteers/communities are clearly-marked sample data.
- Runway live-footage path is built but uncredentialed; the Local
  Studio (free, on-device) is the verified video engine.

## Safety headline
No autonomous money, bookings, messages, or data-sharing — ever.
Every consequential action pauses for one explicit human yes.
