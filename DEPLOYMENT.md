# Deployment checklist

## Prepared Render configuration

The approved test `render.yaml` creates one free Python web service and one free
PostgreSQL 17 database in Frankfurt. The database uses the smallest compute
plan, 1 GB of fixed storage, and no public database
access. The service receives its private database URL through `fromDatabase`;
Render generates separate setup and attendance signing secrets. Python 3.13
is selected through `.python-version`.

The owner approved the free test deployment on 30 October 2026 in My Workspace.
The database expires 30 days after creation and has no backups. Use synthetic
test data; upgrade or export before expiry if retaining it. No paid upgrade or
automatic paid storage expansion is authorized. The web service sleeps after
15 idle minutes and may take about one minute to wake on the next visit.
Resource IDs, the expiration date and verification results will be recorded
after deployment.

Production startup rejects SQLite, missing/short signing and setup secrets,
and example placeholder secrets instead of silently starting with unsafe
defaults. After deployment, the owner creates the first administrator using
the setup form and `SETUP_KEY` from the service's Render Environment page.
Do not put the setup key or database URL into chat, source code, or screenshots.

Deployment must be verified through `/api/health`, the HTTPS setup/sign-in
page, protected schedule endpoints, and runtime logs before sharing the URL.
The free database does not include backups. Take a logical export before schema
upgrades or expiry if keeping test data. Upgrade to durable paid infrastructure
and test a restore before storing official attendance data.

## Release checks

- [ ] Store source in a repository you control.
- [ ] Provision durable PostgreSQL, enable backups and verify a restore.
- [ ] Deploy this app as a Python service (`pip install -r requirements.txt` then `uvicorn app:app --host 0.0.0.0 --port $PORT`).
- [ ] Set `APP_ENV=production`, durable `DATABASE_URL`, strong unique `SETUP_KEY`, and strong stable `ATTENDANCE_SIGNING_KEY`.
- [ ] Ensure service HTTPS and ensure a reverse proxy properly forwards public host/protocol.
- [ ] Create initial admin using setup key and lock down its distribution.
- [ ] Create a second administrator for redundancy via Manage access.
- [ ] Validate signup: member selects one department and cannot access other departments by direct API calls.
- [ ] Validate member accounts cannot edit, preview drafts, publish or unpublish schedules.
- [ ] Approve test member, display QR and scan with real device; verify 08:30 and 15:00 cutoffs using the server clock.
- [ ] Confirm attendance data and email addresses cannot be read by unauthenticated clients.
- [ ] Obtain approval for church branding, privacy policy and attendance data retention.
- [ ] Set up DB backups, monitoring, rate limiting, and incident response before mass rollout.

**Do not deploy this on ephemeral SQLite storage.** Never put production passwords or setup/signing keys in Git.
