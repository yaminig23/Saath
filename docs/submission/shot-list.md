# Saath — Screenshot Shot List

Capture at 1280×800 (desktop) and 390×844 (mobile, Chrome device
toolbar). Save under `docs/submission/shots/`.

| # | Screen | What must be visible |
|---|--------|----------------------|
| 1 | Home (desktop) | Saath hero, greeting, five entry points |
| 2 | Home (mobile) | Bottom nav, Saath, mic button |
| 3 | Chat | Jaipur plan + delegated task references |
| 4 | Tasks | Parent + child rows, audit events |
| 5 | Bookings | DEMO-XXXX reference (cab booking) |
| 6 | Approvals | Pending approval with Approve/Deny |
| 7 | Videos list | 4 COMPLETED lessons, Hindi badge if present |
| 8 | Video player | Kettle lesson playing, captions on |
| 9 | Calendar | Live Google events for today |
| 10 | Nearby | Dentist results with LIVE badge + route |
| 11 | Settings | "Saath replies in: Hindi" selected |
| 12 | Connect/Nearby | Sample-data labels visible (honesty proof) |
| 13 | Bookings | REAL HANDOFF panel (Uber, route filled in) |
| 14 | Flights tab | Offer list + test-mode banner + traveler form |
| 15 | Home + sidebar | Welcome overlay (fresh profile) + left panel |
| 16 | Home dark | Dark mode with sidebar (ALREADY HAVE: `restyle/dash-dark-desktop.png`) |
| 17b | Home light | Light mode (ALREADY HAVE: `restyle/dash-light-desktop.png`) |
| 17c | Home mobile dark | Stacked panels (ALREADY HAVE: `restyle/dash-dark-mobile.png`) |
| 17 | Trusted People | Contact list + Tell flow (More → Trusted People) |
| 18 | Approval popup | Modal over Home + spoken approval |

How: `cd Documents\Saath; .\.venv\Scripts\python.exe -m uvicorn
main:app --host 127.0.0.1 --port 8091`, open `/web/`, log in as
`demostudio`. Use Chrome headless only if unattended; human-taken
shots look better for submission.
