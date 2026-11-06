# Postiz for Social Studio

Status: fork prepared; replacement deployment and migration in progress. The existing publisher remains live.

## Purpose

Social Studio is where the team creates and approves content. Postiz is the publishing engine behind it. This separate fork lets us maintain connector fixes and safe diagnostics without changing Studio's editorial product.

## Current state

- Upstream: gitroomhq/postiz-app; deployment baseline v2.25.3.
- Our repository: Ark-x-AI-Lab/postiz-app; deployment branch deploy/arkx.
- Existing live service: postiz.arkx.group, still upstream v2.10.1.
- Facebook and the new Instagram test account are connected in the existing service. LinkedIn connection is incomplete. No three-network test post has been published.
- Fork creation does not establish upgrade compatibility or fix LinkedIn.

## What must survive migration

Existing users and workspace, API keys, social integrations and encrypted credentials, posts and queue state, uploads and settings. Preserve the existing signing/encryption configuration. Reconcile rows using counts and hashes; never print credentials or personal records.

## Release path

Local branch → reviewed commit → GitHub deploy/arkx → Coolify GitHub App → source-built publisher. Changes on other branches do not deploy. Upstream updates are reviewed explicitly and merged with our patches retained.

## Acceptance

- [x] Fork exists in the correct organization and local clone has upstream remote.
- [x] Deployment definitions, startup guards, schema validation and isolated GitHub source build pass.
- [x] Coolify GitHub App connected to deploy/arkx; candidate has no public domain and automatic deployment stays disabled until migration passes. Private infrastructure exists; no replacement publisher is active.
- [ ] Isolated migration rehearsal succeeds with real sending disabled.
- [ ] Backup restoration and old-version rollback are demonstrated.
- [ ] Live cutover preserves authentication, workspace, integrations and files.
- [ ] Old service retired after independent replacement checks.

## Scope

No advertising, public posting, provider permission expansion or new paid infrastructure is part of deployment validation. Diagnostic logs must exclude raw credentials, OAuth codes/state, request headers, profile data and raw provider response bodies. Public signup stays disabled; administrative helpers and databases stay private.

See [architecture](architecture.md), [migration plan](../plans/coolify-migration.md) and [deployment decision](decisions/001-github-maintained-publisher.md).

Build evidence: [source image and private-context checks](https://github.com/Ark-x-AI-Lab/postiz-app/actions/runs/42143668712), plus the upstream Build workflow, passed for commit 4235e774679e38c611b5a856fb553f7760c01690. Live migration has not occurred. Necessary legacy signing/OAuth configuration was transferred directly to runtime-only Coolify fields and compared exactly; no values entered Git.

## Deployment delivery

The maintained fork builds its commit-tagged image in GitHub Actions and Coolify pulls that exact image. Production keys are runtime-only. The image must pass an offline, read-only Prisma tool check before publication. The existing publisher remains live while database upgrade, recovery and replacement readiness are tested.

The private registry login is separate from the shared server login. Provider credentials belong to the publisher’s declared environment; infrastructure does not inherit the publisher’s global environment file. Default deployment startup continues to exclude the publisher until its explicit activation step.

Candidate deployment correction, prepared locally: production Temporal uses `config/dynamicconfig/docker.yaml` supplied by the pinned `temporalio/auto-setup:1.28.1` image, without a host configuration mount or the upstream development-only override. Compose preparation removes Coolify's recognized shared external network from all seven infrastructure services, retains their source internal networks, and preserves the publisher's routing attachment. Unknown network additions fail closed. Focused checks cover source, generated mapping/array and Compose-normalized configurations plus rejected configuration/network regressions. Corrected target-side Temporal readiness and infrastructure isolation remain open; this patch does not switch volumes or activate the publisher.

Production restart correction: the guarded wrapper starts nginx, runs the installed schema command, and replaces itself with foreground PM2 supervising the backend, orchestrator and frontend Node processes directly. This removes pnpm/dotenv process chains that left orphaned servers holding ports during the main session's isolated restart test. All three services retain their upstream paths and ports and receive the supplied production environment without a runtime `.env` file. Local startup checks cover invalid selections, startup failures, command order and supervisor process replacement. Main's isolated overlay test passed: all three services stayed online at exactly one restart each with zero `EADDRINUSE` errors; a rescheduled waiting run and timer survived restart, then deletion terminated both original and replacement runs with zero active runs and no delivery activities. This used the existing `1127657` application image with two source files copied into the fresh isolated container before startup, with recorded hashes; next-image continuous integration and production Temporal acceptance remain open.

## Current rehearsal evidence

The database-only upgrade preserved all45 original tables; repeated synchronization preserved88 resulting tables, and an independent restore reproduced the original45-table state. An isolated upgraded backend now passes the existing API-key and2-integration checks, rejects missing/invalid keys, and keeps registration closed. A token signed by the old publisher is accepted by the new backend; API keys remain unchanged. The full isolated runtime also serves its login page and all three restored media files with matching bytes. Future schedule creation, rescheduling and cancellation work; the direct-process startup patch additionally passes service restart with the same waiting workflow and no delivery activity. This is isolated runtime evidence, not live connector validity or cutover proof. The rehearsal has no public ports, no provider app credentials and an internal-only network.

Capacity recovery removed the owner-selected obsolete artifacts and inactive build cache, preserving current and rollback images, data volumes and backups. Available disk capacity is checked before every new pull; Docker accounting sizes are not treated as guaranteed reclaim.

## LinkedIn connection diagnostics

The maintained connector includes temporary, opt-in stage diagnostics for connect, callback, token exchange, scope checks, profile reads, Page discovery and Page saving. A random reference links safe browser/API/backend events. Logged fields are closed and exclude raw credentials, authorization state/codes, user/Page identifiers and response bodies. The flag defaults off; enabled runtime collection and the deployed pinned build still need acceptance. The21-test diagnostics harness passed offline with the pinned Node22/TypeScript5.5 image, including overlapping Page selections and invalid bindings. This does not itself establish or repair the live LinkedIn failure.

Recovery evidence: the old publisher image also booted against the independent restored old-schema database and a fresh Redis instance loaded from the verified RDB; its5 API checks pass. Configuration and uploads restored to fresh test volumes match every saved file name/content hash (0 config files,3 upload files; archive directories counted separately). A synthetic value encrypted by the actual old helper decrypts with the actual new helper under the preserved secret. No real integration token was read for that check. This is still not an independently verified full UI rollback or live cutover.
