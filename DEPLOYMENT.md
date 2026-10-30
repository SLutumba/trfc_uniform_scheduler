# Deployment checklist

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
