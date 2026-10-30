# TRFC Uniform Scheduler — departments & QR attendance

A responsive multi-department system for **The Righteous and Faithful City**. Independent community project, not an official church application. Obtain permission before using church branding publicly.

## Who can do what

| Feature | Administrator | Department member |
|---|---|---|
| Choose department | All departments | One department, locked when registered |
| View published uniforms | Yes | Own department only |
| View unapproved schedule drafts | Yes | No |
| Create/edit colours, outfits, rules | Yes | No |
| Generate/publish/unpublish schedule | Yes | No |
| Create/close QR attendance meeting | Yes | No |
| View department roll-call roster and export CSV | Yes | No |
| Scan QR and record own attendance | No (admin-only account) | Yes, after admin approval |
| Approve accounts/transfer departments | Yes | No |

New users register with an email, password and one department. They **cannot change their own department**. Their email becomes visible to administrators. New sign-ups can view their department's published schedule immediately, but require an administrator's approval before QR check-in. Admins can transfer a member to a different department, if necessary.

Old public schedules have been replaced by **login-protected, department-scoped published schedules**. Direct links cannot expose other departments' schedules. The bootstrap administrator may create additional administrator accounts through Manage access.

## Attendance workflow

1. An administrator signs in, chooses Protocol, Choir, Ushers, Hospitality or another department, then opens the **Attendance** tab.
2. Choose today's meeting date and click **Open QR attendance**. Reopening that department/date returns the same meeting; each department/date has at most one meeting.
3. Display the QR code in the meeting. Members scan it using their phone camera, open the website and sign in with their own member account (if needed). The scan completes check-in automatically.
4. The **server** classifies the first successful check-in, according to `Africa/Johannesburg` time:
   - Before **08:30:00** — `on_time`.
   - **08:30:00** through **14:59:59** — `late`.
   - At or after **15:00:00** — `absent`.
5. Members who do not scan are shown as **pending** before 15:00, and **absent** from 15:00 onward. After the day ends, missing check-ins remain absent. Existing check-ins are immutable; repeat scans never overwrite the original classification.
6. Open the roll-call view to see the roster, approval state, status and timestamp. Export CSV, refresh or close the QR session as needed. A closed QR cannot be used again.

**Limitations:** A QR/link can be photographed or forwarded. Login and membership/approval checks make it department-specific but cannot prove physical presence. For fraud-resistant attendance, add live, short-lived/rotating codes and in-person verification. The current implementation does not send notifications or verify email ownership. Obtain consent and set data retention policies before real use.

## Run locally

Requires Python 3.11+ (Windows recommended). On Windows PowerShell, from the project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SETUP_KEY = 'replace-this-with-a-long-random-secret'
$env:ATTENDANCE_SIGNING_KEY = 'replace-this-with-a-different-long-random-secret'
uvicorn app:app --reload
```

Visit <http://127.0.0.1:8000>, create the first administrator with `SETUP_KEY`, and then use the dashboard. The Protocol department starts with initial outfits and last-Sunday all-black rule. The Choir, Ushers and Hospitality departments start blank. Admins can create more departments.

**Important:** Keep `ATTENDANCE_SIGNING_KEY` stable, or previously printed QR URLs become invalid after app restart. Use a secret of at least 32 random bytes. If unspecified, `SETUP_KEY` is used as fallback; if both are missing, a process-local value is created and old QR links break on restart. Put production secrets in the hosting platform's environment variables, never in Git.

### Saving on Windows

Place the entire project directory at:

`C:\Users\sheki\Documents\TRFC uniform scheduler`

If you are running the included `install-to-documents.ps1` from a copied/extracted project folder, it will copy source files into that Windows directory without deleting existing files. It does not magically transfer files from ChatGPT onto your PC; you must first obtain a local copy of the project.

## Main API

| Route | Who | Description |
|---|---|---|
| `GET /api/departments` | Public | Names/IDs for registration |
| `POST /api/register` | Public | New member in one locked department |
| `POST /api/login`, `GET /api/me` | Account | Sign in/session |
| `GET /api/my-schedule` | Member | Only assigned department's published table |
| `GET /api/teams`, `GET /api/teams/{id}` | Account | Admin: all/draft; member: own department/minimal info |
| `PUT /api/teams/{id}/config` | Admin | Edit uniform palettes/outfits/rules |
| `POST /api/teams/{id}/publish` | Admin | Publish department snapshot |
| `POST /api/teams/{id}/unpublish` | Admin | Remove publication |
| `GET /api/public/{slug}` | Signed in + scoped | Department-protected published snapshot (legacy name) |
| `GET/POST /api/users` | Admin | Manage member/admin accounts |
| `PUT /api/users/{id}/approval` | Admin | Approve/suspend QR eligibility |
| `PUT /api/users/{id}/teams` | Admin | Explicit one-department transfer |
| `POST /api/teams/{id}/attendance` | Admin | Open QR session for department/date |
| `GET /api/teams/{id}/attendance` | Admin | Meeting history |
| `GET /api/attendance/{id}` | Admin | Roster and calculated missing attendance |
| `GET /api/attendance/{id}/csv` | Admin | Export roll call |
| `GET /api/attendance/{id}/qr.png` | Admin | QR image |
| `POST /api/attendance/{id}/check-in` | Approved member | Scan QR, record time on server |
| `GET /api/me/attendance` | Member | Own recorded check-ins |
| `POST /api/attendance/{id}/close` | Admin | Invalidate the QR |

Mutable account endpoints require a valid CSRF header from login/`GET /api/me`, except setup/registration/login. All sensitive API reads are authenticated, and published schedules are still department-scoped. Server checks are authoritative, not merely UI restrictions.

## Database/deployment

- SQLite locally; set `DATABASE_URL` for managed PostgreSQL in production.
- SQLAlchemy creates tables and includes a compatibility migration for the old `users.approved` field and former `coordinator` role. For established production deployments, use controlled Alembic migrations, backups and schema testing before upgrading.
- `APP_ENV=production` enables Secure session cookies. HTTPS is required for members scanning QR codes on phones.
- Configure a durable managed PostgreSQL database with regular backups. **Free ephemeral databases are not suitable for church attendance records.**
- `render.yaml` is a starting deployment blueprint. Secrets (`SETUP_KEY`, `ATTENDANCE_SIGNING_KEY`, `DATABASE_URL`) must be configured privately on the hosting platform.
- The app starts with a neutral TRFC wordmark and makes no external logo request. Upload a church-approved PNG/JPEG/WEBP through Branding before official use.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node --check static/app.js
node --check static/checkin.js
```

Tests cover department locking, admin-only writes, QR signing/approval, check-in deadlines, duplicate scans, wrong-day and cross-department attempts, rules and schedule publishing. The local browser review covers login, registration, approval, publishing and signed QR check-in at six viewport widths. Production PostgreSQL and HTTPS deployment still require validation. See REVIEW.md and test-results/.

## Review notes (30 October 2026)

Mandatory rules with unavailable outfits and conflicting rules at the same priority now block preview/publication with an actionable error. Date-specific overrides retain priority over recurring rules. Draft changes and failed publication leave the published snapshot intact.

Use `python -m pip install -r requirements-dev.txt` before running the tests. Windows timezone data is included in the runtime dependencies.

The complete local review, known feature gaps and test evidence are in [REVIEW.md](REVIEW.md).
