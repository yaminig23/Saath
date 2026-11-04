# Saath — Submission Checklist (before 7 AM IST)

## 1. Start the app (5 min)
```powershell
cd $HOME\Documents\Saath
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8091
```
- Open http://127.0.0.1:8091/web/ — Home with Saath loads.
- Log in as `demostudio` (seeded; owns 4 COMPLETED video lessons).
  Password was reset before recording — use the new one, not any old one.
- If port 8091 is busy, try 8092–8100 (same URL pattern).

## 2. Verify live services (10 min)
- [ ] Chat answers (live Muse if quota left; honest stub otherwise).
- [ ] Say "Hi Saath" once — Talk must open hands-free.
- [ ] Videos: kettle lesson plays with captions.
- [ ] Calendar screen shows today's Google events (user 11 grant;
      log in as `minimum` if demostudio shows "not connected").
- [ ] Bookings → "Find real places": "dentist" returns LIVE results;
      "Get real route & time" shows distance + steps.
- [ ] Gmail screen shows connected status.

## 3. Record (60–90 min)
- [ ] Follow `demo-script.md` (3 min narration).
- [ ] Capture all 18 shots in `shot-list.md` (desktop + mobile;
      dashboard shots 16/17b/17c already exist under `restyle/`).
- [ ] Keep one blooper-free take; authenticity beats polish.

## 4. Package (20 min)
- [ ] Video file under the hackathon size limit (re-encode 720p if needed).
- [ ] Description: Muse-powered, approvals-gated, Hindi + voice,
      local video studio, 278 backend tests + 53 voice checks.
      Link `architecture.md` points.
- [ ] NEVER include: `.env`, API keys, tokens, `saath.db` (real
      Google grants inside), `media/` user videos.

## 5. Safety before upload
- [ ] `git status` shows no `.env`, no `saath.db`, no `media/`.
- [ ] If the repo is public, rotate the Meta key + Google OAuth
      client after judging (grants are per-user refresh tokens).
- [ ] Maps key: confirm Cloud console has Places + Routes
      restriction, 500/day quota, $1 budget alert (local
      `GOOGLE_MAPS_DAILY_CAP` is only the second guard).
