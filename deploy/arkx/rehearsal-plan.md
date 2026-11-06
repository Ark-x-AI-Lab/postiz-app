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

1. Before any Docker inspection or mutation, verify the backup descriptor/checksum, resolve the probe hostname (`example.com` by default) on the host using IPv4 only, reject non-public addresses, and select one public IPv4 deterministically. Connect directly to **that IP** on port 443 using `socket.create_connection`, wrap it with `ssl.create_default_context()` and the hostname as Server Name Indication, then send `HEAD /` with the original `Host` header. Require HTTP 200–399; keep certificate/hostname validation enabled. No proxy, redirect following, second endpoint or connection to source services is used. Baseline failure stops before all Docker calls.
2. Verify all seven container names, the network and both volume names are unused; verify local image IDs, PostgreSQL 14.5 metadata and baked environment keys. Refuse unknown baked environment keys.
3. Create only the internal bridge network and two new labeled volumes. Create PostgreSQL with no published ports and no old mounts. Verify the live engine is 14.5. Restore the custom dump with `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`.
4. Check the supplied aggregates. Read the original public table/column inventory and calculate per-table row counts plus order-independent value fingerprints inside PostgreSQL. Keep fingerprints in Python memory; print only counts and pass/fail. This includes original user/authentication values, organization memberships, integration identity/tokens/settings and all other original columns. Read-only SQL here is operational verification on disposable restores, not a new application query path. No records or token fingerprints are printed or saved.
5. Override the candidate entrypoint with `node`. Check installed Prisma 6.5.0 and refuse Prisma `.env` files. No default command, healthcheck, backend, orchestrator or wrapper starts.
6. Only the candidate Node **egress** container receives `--add-host <probe-host>:<same-selected-public-IPv4>`. Inspect `.HostConfig.ExtraHosts` and require exactly that one mapping before starting it. Every other role must have no extra host mappings. On the internal-only network, fetch HTTPS using the original hostname, pinned to that host-proven IP, with a five-second abort timeout. **Any HTTP response fails isolation.** DNS/TLS/configuration errors, including `EAI_AGAIN`, still fail closed; only the existing timeout or explicit network-connectivity failures pass. Inspect internal networking and allowlisted endpoints before every container creation and again before each Node-only start.
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

## Reported run and bounded DNS correction

Main-reported execution evidence, 6 November 2026 (not independently re-executed by this patch worker): the original restore covered 45 tables and passed the supplied aggregate counts; the candidate tool check exited 0. The run then stopped at egress exit code 52 **before schema execution**. The isolated diagnostic found `TypeError` caused by `EAI_AGAIN`; Docker's internal-network DNS could not resolve the public hostname. Network inspection showed an internal bridge and exclusive attachment. A healthy host hostname request alone had not eliminated container DNS as a confounder.

The correction removes that confounder by proving one public IPv4 directly on the host with verified TLS and pinning exactly that IPv4 only for the egress container. It does not classify DNS failure as blocked egress, relax TLS or add an allowed error code.

Current failed prefix `arkx-migration-rehearsal-20261106a` remains retained with PostgreSQL/tools/egress containers, its network and two fresh volumes. **Do not retry this prefix.** Main owns fresh exact-ID cleanup of only the labeled containers/network, retaining both volumes. Main's intended next prefix is `arkx-migration-rehearsal-20261106b` with a fresh run ID, after fresh review. Neither cleanup nor a retry was performed here. Backup base and artifact facts above remain unchanged.

Patch checklist:

- [x] Direct same-IP verified TLS/SNI/HEAD baseline, public IPv4 selection, and pre-Docker failure gate.
- [x] Exact egress-only Docker host mapping; forbid missing, wrong, duplicate or extra mappings and mappings on other roles.
- [x] Keep the existing HTTP-response failure and timeout/connectivity-only pass logic; DNS/TLS still fail closed.
- [x] Syntax and 40 targeted synthetic checks passed, including actual local Node execution with a mocked `fetch` (no network). Tested HTTP 200/302/404/500 failure; DNS `EAI_AGAIN`/`ENOTFOUND` and certificate failure; existing four connectivity errors and abort timeout; exact mapping allow/refusal cases; healthy and failed host baselines; zero Docker calls after failed baseline.
- [ ] Main fresh review and new-prefix real execution; guarded schema/idempotence/database rollback remain unproven.

Only `rehearse-migration.py` and this plan changed in the patch. No Docker, SSH, private API, browser, source-service connection, deployment, install or real-data operation ran.

## Preparation checks

Original preparation passed syntax and 34 mocked checks covering path/checksum guards, fresh-target ownership, stdin-only disposable credentials, guarded schema/restore order, private output and exact cleanup; the earlier find-only review reported no serious defects. The DNS correction subsequently passed the 40 targeted checks above. Those checks are synthetic evidence, not real migration success. Main owns fresh review of this correction.

## Bundled engine requirement

Run B proved same-IP egress blocking and candidate database authentication, then the schema step failed. A separate read-only Prisma diff reproduced an attempt to write the missing schema engine under `/app/node_modules/@prisma/engines`. Image inventory found only the client query engine. The image must be rebuilt with Prisma tools bundled; no runtime download or writable-image exception is allowed. The script now checks executable schema and readable client engines against lockfile commit `173f8d54f8d52e692c7e27e72a88314ec7aeff60`, then explicitly selects those binaries for schema work. Main ran this new gate against the old image with network disabled/read-only/unprivileged: exit71, `ENGINE_BUNDLE_INVALID`, empty stderr. Syntax passes; an engine-specific synthetic suite and successful rebuilt-image/schema/rollback execution remain unproven. Both failed prefixes are retained. Never rerun against their existing targets.
