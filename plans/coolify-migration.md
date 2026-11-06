# GitHub-backed publisher migration

Approved direction: Mounssif, 6 November 2026. Work is in progress, not deployed.

1. Fork and clone. Verify parent, organization, baseline tag and upstream remote. Complete.
2. Prepare source-built Compose and private dependencies. Preserve registration-off, secret isolation and persistent volumes. Connect the existing Coolify GitHub App without switching the public domain.
3. Inventory current volumes, schema, row counts, scheduled jobs and required runtime variables. Preserve credentials privately and freeze publishing before a coherent backup.
4. Restore a disposable clone. Rehearse the v2.25.3 schema and scheduling migration with external delivery blocked at the network/process boundary. No real social tokens should be usable in a rehearsal process.
5. Prove restored accounts, API contract and media, plus blocked outbound traffic. Prove recovery to the previous version from the saved recovery point. Never start two schedulers against the same data.
6. Stop the old publisher, take the final recovery point, migrate and switch the existing domain. Read back the deployed commit and test login, API authorization, workspace/integration counts, media and registration denial.
7. Retire only the old service resources identified at action time, after recovery checks. Preserve backups separately from volumes deleted with the old service. Do not delete the active migrated database or upload volume.

Open prerequisites: current host capacity, backup/restore path, exact Temporal topology and GitHub App repository access. No local dependency installation or Docker image pull on the nearly full laptop.

Source preflight: the target recovery workflow only scans eligible already-overdue posts from the previous two days, and requires RUN_CRON. Do not rely on it to transfer future schedules. Inventory and reconcile every pending schedule; preserve old Redis recovery state. The fork retains the old User.marketplace field; normal schema guards remain enabled, with no accept-data-loss/reset flags.
