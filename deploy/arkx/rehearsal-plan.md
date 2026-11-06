# Restored-database migration rehearsal

Goal: prove a fresh restore, normal guarded candidate schema update, preserved data, and an independent database rollback restore before cutover. Status: reviewed preparation; execution evidence is recorded after each server run.

## Requests and acceptance

- [x] Provide a standalone Python standard-library script; explicit fresh prefix, run identity, immutable image IDs, existing backup directory, relative dump path and expected SHA-256 checksum.
- [x] Implement fresh PostgreSQL 14.5 storage, custom-format logical restore, and aggregate checks: 1 User, 1 Organization, 2 Integrations, 0 Media, 0 Posts and 0 queued database posts.
- [x] Implement private all-original-table/all-original-column value comparison, normal guarded Prisma update, a repeat update, and a separate rollback database restore.
- [x] Implement separate host HTTPS baseline, internal-network Node HTTPS failure check, and candidate-to-database authentication check before schema execution. No backend/orchestrator startup exists in this script.
- [x] Implement explicit owned-target container/network cleanup; preserve both database volumes. No automatic cleanup, broad deletion, old-volume attachment or data-loss override.
- [ ] The operator executes the real restored-data stage and captures checks/gaps. Preparation and mocked checks are not execution proof.
- [ ] The operator separately proves the complete application, old-version rollback, authentication, media, queue/workflows and approved live provider paths.

## Source and current state

The fork retains the legacy User marketplace field. Schema compatibility must be proven on a restored copy with normal safety guards enabled.

Recorded 6 November 2026 snapshot (refresh mutable state before execution):

- Candidate Coolify resource: `1alwcyoouzoa5mf1jrnfzm87`.
- Candidate tag: `1alwcyoouzoa5mf1jrnfzm87_postiz:81706e45e31df42f9950cce0d947628e4245ce4b`.
- Candidate immutable ID: `sha256:13d1905aebe85f2022fb079118de0cd7ced833aaed82a1453e763e3e9b40dab3`.
- Old actual immutable ID: `sha256:065d2780396b88c5a03853e912703bca007e28abdab748b4c370457e967064a3` (record only; this script never runs the old app).
- Existing containers: `postiz-8hopq6f9yhiyr8x7nnv6fhnb`, **`postgres-8hopq6f9yhiyr8x7nnv6fhnb`**, `redis-8hopq6f9yhiyr8x7nnv6fhnb`; four old volumes must remain untouched. This script has no argument for any old container or volume.
- Operator's root SSH reads worked. Use the authorized `arkxadmin` SSH account with passwordless sudo.
- Infrastructure start failed at an environment-file path before containers started; the root-path correction is maintained in the deployment files. This standalone stage does not depend on that Compose startup and does not start those seven services.
- Operator's offline candidate check found Node `v22.23.4`, installed `/app/node_modules/prisma/build/index.js`, schema and wrapper, no runtime secret keys and no `/app/.env`. The script rechecks Prisma version 6.5.0 and Prisma dotenv locations in its own candidate container.

## Operator's exact execution boundary

This script is **database-only**. The operator must first have a completed, coherent backup from the backup procedure, verify its exact dump path/checksum, available capacity, and obtain the exact local PostgreSQL 14.5 image ID. Keep the backup immutable throughout both restores. Existing services and volumes are not stopped or modified by this script. A new snapshot is needed for an eventual cutover; rehearsal is not a final import.

The operator selected `/var/backups/arkx-postiz` as the dedicated root-owned mode-0700 base; pass the exact completed `postiz-migration-YYYYMMDDTHHMMSS.microZ` child directory to `--backup-dir`. No old backup base is hardcoded and no permission relaxation is needed.

Use Linux root with Python 3.8+ and Docker already available. The Docker CLI targets only `unix:///var/run/docker.sock`; inherited remote contexts, environment and proxy settings are excluded. Image tags and missing targets fail closed. No image is pulled and no package is downloaded. Backup data is streamed through a verified file descriptor, never mounted into a container. The backup is checksum-checked before and after each restore.

First review the default plan using exact operator-selected values. The following is a template: replace every uppercase placeholder; none contains a credential. Choose a unique prefix and a fresh 32-character lowercase hexadecimal run ID (an ownership label, not an authentication secret).

```sh
python3 deploy/arkx/rehearse-migration.py \
  --prefix arkx-migration-rehearsal-UNIQUELOWERCASE \
  --run-id FRESH32LOWERCASEHEX \
  --pg-image sha256:EXACTLOCALPOSTGRES145IMAGEID \
  --candidate-image sha256:13d1905aebe85f2022fb079118de0cd7ced833aaed82a1453e763e3e9b40dab3 \
  --backup-dir /EXACT/COMPLETED/BACKUP \
  --dump-relative EXACT/CUSTOM.dump \
  --dump-sha256 EXACT64LOWERCASEHEX
```

Default `--stage plan` reads local path metadata only; it never accesses Docker. Add **`--stage db-schema`** only for main's authorized server execution. Neither mode accepts production runtime secrets. All Docker execution requires Linux root. One local lock serializes operations for each prefix; main must not manipulate that prefix concurrently through another tool.

The execution sequence is:

1. Verify all seven container names, the network and both volume names are unused; verify local image IDs, PostgreSQL 14.5 metadata and baked environment keys. Refuse unknown baked environment keys.
2. Verify healthy host HTTPS access to the same public hostname used by the blocked probe (`example.com` by default; configurable with `--probe-host`). Host resolution must yield only public addresses. Disable host proxy use.
3. Create only the internal bridge network and two new labeled volumes. Create PostgreSQL with no published ports and no old mounts. Verify the live engine is 14.5. Restore the custom dump with `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`.
4. Check the supplied aggregates. Read the original public table/column inventory and calculate per-table row counts plus order-independent value fingerprints inside PostgreSQL. Keep fingerprints in Python memory; print only counts and pass/fail. This includes original user/authentication values, organization memberships, integration identity/tokens/settings and all other original columns. Read-only SQL here is operational verification on disposable restores, not a new application query path. No records or token fingerprints are printed or saved.
5. Override the candidate entrypoint with `node`. Check installed Prisma 6.5.0 and refuse Prisma `.env` files. No default command, healthcheck, backend, orchestrator or wrapper starts.
6. From candidate Node on the internal-only network, attempt HTTPS fetch to the public hostname with a five-second abort timeout. **Any HTTP response fails isolation.** DNS/TLS/configuration errors fail closed; only timeout or explicit network-connectivity failures pass. Inspect internal networking and allowlisted endpoints before every container creation and again before each Node-only start.
7. From candidate Node, authenticate/connect to the fresh PostgreSQL database using installed Prisma Client. This separately proves allowed database connectivity. No secret is fetched from the old application. The disposable database password exists in Python memory and Docker runtime metadata, supplied through `--env-file /dev/stdin`, never a command argument or generated environment file.
8. Run exactly `node /app/node_modules/prisma/build/index.js db push --schema /app/libraries/nestjs-libraries/src/database/prisma/schema.prisma --skip-generate`. The skip avoids client-generation writes; Prisma's normal database safety guard remains enabled. There is no force/reset/accept-data-loss option. Stop on any warning requiring an override; the operator owns private diagnosis and any schema correction.
9. Compare every original table/column value fingerprint and counts. Snapshot the entire resulting public-table inventory, repeat the same guarded schema command and compare again for idempotence.
10. Start a second fresh PostgreSQL 14.5 instance and restore the same original dump. Compare original column inventory and all original row fingerprints. This proves an independent **database restore rollback**; it does not run the old application against either schema.

Containers have explicit names and ownership labels, no published ports, no host mounts, no inherited image healthchecks and disabled Docker logging. Candidate-only containers are read-only, unprivileged and use a temporary `/tmp`; PostgreSQL gets only its own new volume. Core dumps are disabled for the orchestrator. Docker inspection deliberately reads narrow fields and never prints container environment. Raw restore, Prisma and database output is captured privately in memory and suppressed on failure. The operator should not enable shell tracing or print runtime environment while diagnosing.

The public-host probe plus Docker `internal=true` verifies the bounded isolation claim on this host; it is not a general firewall audit or proof against a privileged host administrator. No broader application-start permission is implied by its success.

## Failure, cleanup and remaining stages

On failure, interruption or timeout, retain all targets; do not retry creation or assume the operation failed before side effects. Inspect the exact current labeled targets privately. Existing names always refuse a rerun; select a new prefix after diagnosing. The script reports the active check and a safe failure message, never raw credential-bearing errors. A backup or guard failure never triggers a weaker fallback.

For a separately authorized cleanup, first review exact target names from the plan and fresh server state. Run:

```sh
python3 deploy/arkx/rehearse-migration.py --stage cleanup \
  --prefix EXACTORIGINALPREFIX --run-id EXACTORIGINALRUNID \
  --confirm-cleanup EXACTORIGINALPREFIX
```

Cleanup preflights every present allowlisted container's current ownership and network, refuses unknown network endpoints, rechecks immutable IDs, removes only those exact IDs and the exact owned network, then verifies absence. It **does not delete volumes**. Volume deletion, backups, original services and the lock-file lifecycle are outside this script; do not use a broad prune command.

Operator-only open stages, all mandatory before a cutover claim:

- Full isolated application startup with an explicit reviewed command/runtime guard decision, fresh infrastructure, network isolation rechecked on the actual app, and no OAuth/mail/AI configuration copied by default. Preserve the exact legacy JSON Web Token secret only if that later stage needs it: narrow old-container `node` read into Python memory and transfer directly through Docker stdin environment input. Never place it in logs, shell arguments, reports or environment files.
- Old-version application rollback on a matching restored database/Redis/uploads/configuration set using the recorded old image; do not point it at the migrated schema or old live volumes. Database restore alone does not prove login or app compatibility.
- Temporal persistence, namespace/search attributes, workers and visibility behavior; queue membership/disposition from the backup procedure's Redis proof. This script's `queued-database-posts=0` does **not** prove Redis has zero jobs.
- Existing login plus blocked enrollment, organization/integration access, media URL/storage continuity and existing API contracts. Live OAuth validity and any provider refresh/publishing side effect require main's explicit safe execution stage and applicable approval. Do not enable two publishing owners.
- Final coherent import, source/image/version confirmation, capacity acceptance, exact cutover authorization and a recovery decision. No deployment or production enablement is implemented here.

## Preparation checks

Syntax and 34 mocked checks passed, covering path/checksum guards, fresh-target ownership, stdin-only disposable credentials, network probes, guarded schema/restore order, private output and exact cleanup. A separate find-only review reported no serious defects. These checks do not establish real migration success.
