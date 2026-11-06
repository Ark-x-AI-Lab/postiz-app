# Postiz for Social Studio

Status — 6 November 2026: the GitHub-built v2.25.3 replacement is live at postiz.arkx.group. The old service is retired with its original volumes and recovery backup retained. Checked lifetime post analytics are deployed on source d7a1911508da31fc20ea42a51c1bb2f4f38a7f31. The Facebook summary-counter image has passed its source build and offline checks but is not deployed.

## Purpose

Social Studio is where the team creates and approves content. Postiz is the publishing engine behind it. This separate fork lets us maintain connector fixes and safe diagnostics without changing Studio's editorial product.

## Current state

- Upstream: gitroomhq/postiz-app; deployment baseline v2.25.3.
- Our repository: Ark-x-AI-Lab/postiz-app; deployment branch deploy/arkx.
- Live source: d7a1911508da31fc20ea42a51c1bb2f4f38a7f31, v2.25.3; immutable commit image.
- Facebook, Instagram and LinkedIn test accounts are connected. One approved neutral scheduled post was published to all three; independent native reads matched caption, author and image.
- LinkedIn token exchange was repaired by correcting the existing app secret in persistent runtime configuration. No new app or expanded permission was needed for that repair.

## What must survive migration

Existing users and workspace, API keys, social integrations and encrypted credentials, posts and queue state, uploads and settings. Preserve the existing signing/encryption configuration. Reconcile rows using counts and hashes; never print credentials or personal records.

## Release path

Local branch → reviewed commit → GitHub deploy/arkx → Coolify GitHub App → source-built publisher. Changes on other branches do not deploy. Upstream updates are reviewed explicitly and merged with our patches retained.

## Acceptance

- [x] Fork exists in the correct organization and local clone has upstream remote.
- [x] Deployment definitions, startup guards, schema validation and isolated GitHub source build pass.
- [x] Coolify GitHub App connected to deploy/arkx; explicit deployment uses the commit-tagged image. Automatic deployment remains disabled; the replacement owns the public domain.
- [x] Isolated migration rehearsal passed with real sending disabled.
- [x] Independent database, Redis and media restoration plus old-image API recovery passed; a full old-version user-interface rollback was not exercised.
- [x] Live cutover preserved the original values across45tables; the upgraded schema has88tables. Redis7keys and3media files matched.
- [x] Old service retired after independent replacement checks; original4volumes retained.

## Scope

No advertising, public posting, provider permission expansion or new paid infrastructure is part of deployment validation. Diagnostic logs must exclude raw credentials, OAuth codes/state, request headers, profile data and raw provider response bodies. Public signup stays disabled; administrative helpers and databases stay private.

See [architecture](architecture.md), [migration plan](../plans/coolify-migration.md) and [deployment decision](decisions/001-github-maintained-publisher.md).

Build evidence: [source image and private-context checks](https://github.com/Ark-x-AI-Lab/postiz-app/actions/runs/42143668712), plus the upstream Build workflow, passed for commit 4235e774679e38c611b5a856fb553f7760c01690. That build was pre-cutover evidence; the later D7 checked-analytics source is live. Necessary legacy signing/OAuth configuration was transferred directly to runtime-only Coolify fields and compared exactly; no values entered Git.

## Deployment delivery

The maintained fork builds its commit-tagged image in GitHub Actions and Coolify pulls that exact image. Production keys are runtime-only. The image must pass an offline, read-only Prisma tool check before publication. The replacement is live; archived rehearsal evidence below describes the pre-cutover checks.

The private registry login is separate from the shared server login. Provider credentials belong to the publisher’s declared environment; infrastructure does not inherit the publisher’s global environment file. Default deployment startup continues to exclude the publisher until its explicit activation step.

Candidate deployment correction, prepared locally: production Temporal uses `config/dynamicconfig/docker.yaml` supplied by the pinned `temporalio/auto-setup:1.28.1` image, without a host configuration mount or the upstream development-only override. Compose preparation removes Coolify's recognized shared external network from all seven infrastructure services, retains their source internal networks, and preserves the publisher's routing attachment. Unknown network additions fail closed. Focused checks cover source, generated mapping/array and Compose-normalized configurations plus rejected configuration/network regressions. Corrected target-side Temporal readiness and infrastructure isolation remain open; this patch does not switch volumes or activate the publisher.

Production restart correction: the guarded wrapper starts nginx, runs the installed schema command, and replaces itself with foreground PM2 supervising the backend, orchestrator and frontend Node processes directly. This removes pnpm/dotenv process chains that left orphaned servers holding ports during the main session's isolated restart test. All three services retain their upstream paths and ports and receive the supplied production environment without a runtime `.env` file. Local startup checks cover invalid selections, startup failures, command order and supervisor process replacement. Main's isolated overlay test passed: all three services stayed online at exactly one restart each with zero `EADDRINUSE` errors; a rescheduled waiting run and timer survived restart, then deletion terminated both original and replacement runs with zero active runs and no delivery activities. This used the existing `1127657` application image with two source files copied into the fresh isolated container before startup, with recorded hashes; next-image continuous integration and production Temporal acceptance remain open.

## Current rehearsal evidence

The database-only upgrade preserved all45 original tables; repeated synchronization preserved88 resulting tables, and an independent restore reproduced the original45-table state. An isolated upgraded backend now passes the existing API-key and2-integration checks, rejects missing/invalid keys, and keeps registration closed. A token signed by the old publisher is accepted by the new backend; API keys remain unchanged. The full isolated runtime also serves its login page and all three restored media files with matching bytes. Future schedule creation, rescheduling and cancellation work; the direct-process startup patch additionally passes service restart with the same waiting workflow and no delivery activity. This is isolated runtime evidence, not live connector validity or cutover proof. The rehearsal has no public ports, no provider app credentials and an internal-only network.

Capacity recovery removed the owner-selected obsolete artifacts and inactive build cache, preserving current and rollback images, data volumes and backups. Available disk capacity is checked before every new pull; Docker accounting sizes are not treated as guaranteed reclaim.

## LinkedIn connection diagnostics

The maintained connector includes temporary, opt-in stage diagnostics for connect, callback, token exchange, scope checks, profile reads, Page discovery and Page saving. A random reference links safe browser/API/backend events. Logged fields are closed and exclude raw credentials, authorization state/codes, user/Page identifiers and response bodies. The flag defaults off; enabled runtime collection and the deployed pinned build still need acceptance. The21-test diagnostics harness passed offline with the pinned Node22/TypeScript5.5 image, including overlapping Page selections and invalid bindings. This does not itself establish or repair the live LinkedIn failure.

Recovery evidence: the old publisher image also booted against the independent restored old-schema database and a fresh Redis instance loaded from the verified RDB; its5 API checks pass. Configuration and uploads restored to fresh test volumes match every saved file name/content hash (0 config files,3 upload files; archive directories counted separately). A synthetic value encrypted by the actual old helper decrypts with the actual new helper under the preserved secret. No real integration token was read for that check. This is still not an independently verified full UI rollback or live cutover.

## Checked per-post audience measurements — deployed D7

The analytics response keeps its bare array shape and adds optional metric identity, lifetime basis and explicit-count provenance for reviewed saved single-image dispatch paths. Missing or invalid counts are omitted rather than replaced with zeros. Facebook reach, reactions and clicks remain distinct; LinkedIn error-shaped or missing counters cannot become zero. Unknown/video/story/personal-profile formats do not gain provenance. Studio requires these fields before ingesting measurements. The pinned source/image workflow and exact live image receipt passed. Real reads on the three approved neutral test posts passed Studio’s strict parser: Facebook supplied clicks only; Instagram and LinkedIn supplied explicit aggregate counts. Ordinary live editorial-post ingestion remains open; connection tests are not mirrored or resent.

## Facebook summary counters — next patch, not deployed

The current provider requests insights only, leaving supported node/edge counts unread. Three count-only probes on the exact existing neutral Page post returned explicit0 for LIKE-filtered reactions and stream comments; shares was omitted and remains unavailable. No actor rows, post writes, permission change or credential refresh occurred. The local implementation reads these aggregate summaries independently of insights, only for proven saved single-image Page posts. Four GETs maximum, fixed Meta origin/version, bearer headers, no redirects/retries/pagination, bounded time/body size. Invalid, mismatched or error-shaped summaries cannot become counts; explicit0 remains distinct from missing. Unknown/video/story paths retain their prior behavior.

Definitions follow the exact vendor filters: likes means the returned LIKE-filtered reaction total; comments means the returned stream-comment summary. This does not certify whether Meta groups CARE reactions or which replies its summary includes. Do not relabel all reactions as likes or compare different networks as if their definitions were identical. Source: [Meta SDK25 Post](https://github.com/facebook/facebook-python-business-sdk/blob/25.0.0/facebook_business/adobjects/post.py), [Profile types](https://github.com/facebook/facebook-python-business-sdk/blob/25.0.0/facebook_business/adobjects/profile.py) and [Comment filters](https://github.com/facebook/facebook-python-business-sdk/blob/25.0.0/facebook_business/adobjects/comment.py). Twenty-four focused tests and a bounded review pass. The exact candidate helper, transpiled in memory inside the existing Node22 publisher image, returned clicks0/likes0/comments0 accepted by Studio’s actual parser; shares/reach stayed missing. This read-only candidate check did not replace the running publisher or write saved Results. The exact e674 source build and pinned runtime checks passed in GitHub run 42280716589, including 45 tests. Exact deployed readback remains required; live source is still D7.

## Private publisher sign-in — reviewed image, not deployed

Closed registration currently rejects unknown Google accounts at final creation on an initialized instance, but still offers a signup form. The shared policy also permits first-account creation and generic-provider registrations. The local correction removes those exceptions when `DISABLE_REGISTRATION=true`, refuses unknown provider callbacks with a fixed private-instance message, and hides signup links. Existing linked Google and activated local accounts retain their sign-in paths; no account is linked merely by matching email. This is registration closure, not a new staff allowlist or a claim that every historical account is authorized. Source 3b3a7de passed the full image build and all runtime/privacy checks in GitHub run 42291438207, including 107 registration and 45 analytics/diagnostic tests with zero failures or skips. The private commit image is published. Eighteen isolated real-React browser cases pass at phone/tablet/desktop widths; the full Next layout and live staff-login acceptance remain open. This image is not deployed. See [scope and acceptance](../plans/private-registration.md).
