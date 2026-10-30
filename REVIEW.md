# TRFC Uniform Scheduler review

Reviewed on 30 October 2026. Source and fixes are saved in
`C:\Users\sheki\Documents\TRFC uniform scheduler`.

The directory was initially empty. The latest saved application sources were
restored into their backend, static and test directories before review. The
application was tested using disposable SQLite databases and synthetic accounts.
The test accounts and their database are not part of the installed application.
The disposable server has been stopped. Automatic approval review blocked
temporary-file cleanup because filesystem approval is unavailable in this
session, so temporary review folders remain locally. They are excluded from Git
and from the installer; the application's normal `local.db` was not created.

## Result

**29 automated backend tests pass.** Python, browser JavaScript and the Windows
installer pass syntax checks. Real browser checks passed for administrator
login, scheduling, publication, registration, approval and signed QR attendance.

The current app supports continued local development. Passing these checks does
not establish that every feature in the larger project specification exists.
The remaining access and reporting gaps below need attention before relying on
the app for official church records.

## Defects fixed

| Area | Defect and resulting behavior |
| --- | --- |
| Schedule dates | Server preview rows omitted the day number, displaying `undefined`. Dates now render correctly. |
| Preview consistency | The browser could display a different local scheduling algorithm while the server preview was unavailable. The UI now waits for the server and displays preview errors. |
| Mandatory rules | Unavailable outfits were silently skipped. Matching mandatory rules now block preview/publication with a date-specific explanation. |
| Rule conflicts | Conflicting rules at the same priority silently selected the newest. Conflicts now return a validation error; date-specific overrides retain priority. |
| Approved colours | Confirmation previously applied only to ties. All garment colours must now be confirmed before their outfit is scheduled. |
| Account creation | A duplicate administrator-created account raised an uncaught database error. It now returns HTTP 409 and rolls back safely. |
| Validation | Malformed label objects and invalid garment references could crash configuration updates. They now produce validation errors. |
| Authentication | Spaces and casing could bypass the per-account login limiter. Email keys are normalized, stale failures expire, and an additional per-client failure limit applies. Credential input lengths are bounded. |
| Secret comparisons | Non-ASCII invalid secrets could raise an exception. Setup, CSRF and QR comparisons now fail safely. |
| Caching | Private API responses lacked a no-store policy. API responses and application/check-in pages now include it. |
| CSV export | Custom column labels and attendance department names could be interpreted as spreadsheet formulas. User-controlled CSV values are escaped consistently. |
| Database integrity | SQLite foreign keys were disabled. They are now enabled for every connection. |
| UTC timestamps | PostgreSQL-aware timestamps could acquire an invalid extra `Z`. Serialization handles aware and naive UTC values; new timestamp writes preserve timezone information. |
| Compatibility migration | The legacy boolean column migration used `DEFAULT 1`. It now uses `DEFAULT TRUE`, which is compatible with PostgreSQL booleans. |
| Account interface | Creating an admin could leave the next member's department selector hidden. Role and required-field state now reset together. The administrator's own password action opens the current-password form. |
| Mobile attendance | A two-column attendance history unnecessarily inherited a wide table minimum. Both date and status now fit without horizontal scrolling. Wider uniform tables have a swipe hint and keyboard-focusable scroll region. |
| Branding | Initial loading depended on a remote logo. Startup now uses a neutral TRFC wordmark; administrators can upload an approved image. |
| Windows and CI | Added timezone data and a development dependency file, corrected the missing JavaScript path in CI, and excluded secrets, local databases and review files from the installer. |

## Verification evidence

- Before fixes: the expanded suite reproduced **12 failing regression cases**;
  all 11 original tests passed.
- Final backend run: **29 passed in 9.09 seconds**, no failures or skips.
  Machine-readable results: [test-results/backend.xml](test-results/backend.xml).
- Browser checks: [test-results/browser-review.json](test-results/browser-review.json).
  These are recorded interactive checks, not a separately automated browser suite.
- Both dashboards were checked at 320, 375, 390, 768, 1024 and 1440 pixels, with
  no page-level horizontal overflow. Wide uniform tables scroll inside their
  own container. The compact attendance table was checked again at 390 pixels.
- A member registered into Protocol, saw the published department schedule,
  was refused QR attendance while unapproved, and checked in after approval.
  The real server time was after the 15:00 SAST cutoff, so the recorded status
  was correctly `absent`. Reloading the signed link returned the original record.
  The automated suite checks all four exact time boundaries separately.
- The QR PNG loaded successfully. The checked QR page reported no browser
  console warnings or errors. The final backend restart preserved sessions and
  the previously recorded attendance.
- Visual evidence: [test-results/member-mobile.png](test-results/member-mobile.png).

## Remaining findings and feature gaps

| Priority | Finding | Required follow-up |
| --- | --- | --- |
| High | Historical rosters are reconstructed from current department memberships. Transfers/removals can change a past meeting's roster. | Snapshot meeting participants and define historical membership/retention semantics before treating reports as durable attendance registers. |
| High | All administrator accounts have church-wide access; there is no scoped department-administrator role. | Add a separate role and enforce department assignments on every administrative endpoint if department staff will administer their own teams. |
| Medium | Temporary password changes are requested by the UI but are not enforced by the backend. | Restrict temporary-password sessions until the password is changed. |
| Medium | QR links are valid for the meeting date until closed, without short-lived rotation. A forwarded link can still be used by an approved member. | Add rotating tokens/in-person verification if physical-presence assurance is required. |
| Medium | Member history lists recorded check-ins; missing check-ins appear as absent in the admin roster but not as member-history entries. | Define a complete personal attendance-history read model. |
| Medium | Manual attendance corrections with reasons, configurable cutoffs and publication history are not implemented. | Implement these features with immutable audit records. |
| Medium | Production schema changes still use startup table creation and an ad hoc migration. | Adopt reviewed Alembic migrations and test upgrade/backup/restore procedures against PostgreSQL. |
| Medium | Login rate limiting is process-local; registration/setup do not have equivalent limits. Email ownership is not verified. | Add shared or proxy-level limiting and an account verification policy before an unrestricted public launch. |
| Feature gap | Inventory has three relabelable garment slots, rather than an arbitrary number of categories. | Extend the inventory model if departments require more layers. |

Production PostgreSQL, production HTTPS, real-device camera scanning, load tests
and database recovery were not exercised in this local review. No Git checkout
existed in the directory, and this task did not create a remote commit or deployment.

## Run and test

From this directory in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
node --check static/app.js
node --check static/checkin.js
```

To start the app, set private `SETUP_KEY` and `ATTENDANCE_SIGNING_KEY` environment
variables, then run `.\.venv\Scripts\python.exe -m uvicorn app:app --reload`.
Open `http://127.0.0.1:8000` and create the initial administrator. Keep the
attendance signing key stable. See README.md for setup and operation details.
