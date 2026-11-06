# GitHub-backed publisher migration

Approved direction: Mounssif, 6 November 2026. Work is in progress, not deployed.

1. Fork and clone. Verify parent, organization, baseline tag and upstream remote. Complete.
2. Prepare commit-image Compose and private dependencies. Preserve registration-off, secret isolation and persistent volumes. Connect the existing Coolify GitHub App without switching the public domain.
3. Inventory current volumes, schema, row counts, scheduled jobs and required runtime variables. Preserve credentials privately and freeze publishing before a coherent backup.
4. Restore a disposable clone. Rehearse the v2.25.3 schema and scheduling migration with external delivery blocked at the network/process boundary. No real social tokens should be usable in a rehearsal process.
5. Prove restored accounts, API contract and media, plus blocked outbound traffic. Prove recovery to the previous version from the saved recovery point. Never start two schedulers against the same data.
6. Stop the old publisher, take the final recovery point, migrate and switch the existing domain. Read back the deployed commit and test login, API authorization, workspace/integration counts, media and registration denial.
7. Retire only the old service resources identified at action time, after recovery checks. Preserve backups separately from volumes deleted with the old service. Do not delete the active migrated database or upload volume.

GitHub App repository access and paused resource creation are confirmed; source build and privacy checks pass. A coherent recovery archive and isolated database restore are proven. Open prerequisites: full application recovery, available resources at activation time, and runtime Temporal/application topology. No local dependency installation or Docker image pull on the nearly full laptop.

Source preflight: the target recovery workflow only scans eligible already-overdue posts from the previous two days, and requires RUN_CRON. Do not rely on it to transfer future schedules. Inventory and reconcile every pending schedule; preserve old Redis recovery state. The fork retains the old User.marketplace field; normal schema guards remain enabled, with no accept-data-loss/reset flags.

First server build completed, including an offline image check showing no runtime secret variables or root `.env` inside the artifact. The initial infrastructure start failed on Coolify's generated relative environment-file path. A coordinated root-path correction and blocked/allowed synthetic regression are prepared; repeat the live infrastructure startup before restoring data.

Deployment strategy: GitHub Actions builds and publishes the commit image; Coolify pulls and verifies its source/revision labels instead of compiling on the constrained host. Image publication and offline tool checks passed. The package stays private, with an owner-approved dedicated read-only registry credential isolated from the existing shared login. Private pull and the effective Coolify command remain live acceptance checks. No replacement domain or active publisher has been switched.

Database-only rehearsal passed against the GitHub-built `f65ecd28` image: the guarded upgrade preserved all values/membership in 45 original tables, a second schema synchronization preserved the resulting 88-table state, and a separate restore from the original backup reproduced all 45 original tables and column values. Candidate Node authenticated to the clone while HTTPS egress was blocked. No backend or publishing worker ran. Full application, Redis/media, Temporal, old-image recovery and cutover checks remain open.

Coolify startup uses a checked transformation to remove its injected global service environment file, retaining explicit bindings and routing labels. The private pull wrapper has passed against the existing commit image through its separate credential config. New startup transformation and the complete private infrastructure deployment remain target-side acceptance checks.

Capacity gate: the initial 953c0f91 infrastructure attempt failed before creating services because BusyBox rejected a GNU timeout option. Portable forced timeouts are fixed and passed in the actual helper; commit1a4c4f98 passed its GitHub checks. Downloading the prior 6.9 GB-on-disk candidate exhausted host free space, so further deployment is held. Exact superseded test-artifact cleanup is awaiting owner approval. A smaller, dependency-preserving runtime recipe and archive-size gate are prepared; no smaller-image claim is made until CI measures it.
