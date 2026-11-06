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
- [x] Coolify GitHub App connected to deploy/arkx; candidate stopped, no public domain, automatic deployment disabled until migration passes.
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
