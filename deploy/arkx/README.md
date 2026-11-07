# Ark-x Coolify candidate — v2.25.3

Status — 6 November 2026: source d7a1911508da31fc20ea42a51c1bb2f4f38a7f31 is live at postiz.arkx.group. Three neutral test accounts published the approved scheduled post. The old service was retired with original volumes and recovery backup preserved. Automatic deploy remains disabled. The detailed candidate/rehearsal notes below are historical preparation evidence, not the current cutover status. Checked lifetime analytics passed pinned image and live read checks. The combined Facebook aggregate-summary/private-registration image at source 3b3a7de passed its source build and 152 offline tests; it is not deployed. Isolated component-browser checks pass; full Next layout and live staff-login acceptance remain open.

## Files and choices

- `compose.rehearsal.yaml`: seven infrastructure services, **no Postiz service**, no published ports, both networks internal. No application database changes or publishing workflows can run through this file.
- `compose.production.yaml`: the same infrastructure plus the commit-tagged application image on internal port **5000**, behind the `production` profile. Only the application joins an outbound-capable network. Coolify must route its app domain to `postiz:5000`, never to infrastructure services.
- `runtime-start.sh`: requires the exact runtime value `ARKX_RUNTIME=production`, starts nginx, runs the normal installed `prisma-db-push` command, then replaces itself with `pm2-runtime start /app/deploy/arkx/ecosystem.config.cjs`. A failed nginx or schema command prevents application startup. This custom wrapper variable is not a Postiz feature flag. It also blocks an accidental explicit `up postiz`, which would bypass profile selection.
- `ecosystem.config.cjs`: directly supervises backend, orchestrator and frontend Node processes in production. It preserves the upstream compiled paths, backend/orchestrator Node flag and frontend port 4200, without long-lived pnpm/dotenv wrappers or loading a runtime `.env` file.
- `dynamicconfig/development-sql.yaml`: exact upstream development configuration, mounted read-only in rehearsal only. Production uses the pinned Temporal image's built-in `config/dynamicconfig/docker.yaml` without a host mount or development-only override.
- `.env.example`: synthetic values for configuration rendering only. It intentionally sets `ARKX_RUNTIME=rehearsal`, so it cannot start Postiz through this wrapper. Never use it to launch infrastructure either; replace every synthetic value.
- `preflight.py`: Python standard library checks; no installs, no container startup, no image build. The default checks use synthetic values and shell command stubs only.

The compose files use JSON-formatted YAML so the Python standard library can inspect their exact content. Docker Compose accepts this syntax; Coolify's handling still needs target-side confirmation.

The baseline follows the tagged [official v2.25.3 compose](https://github.com/gitroomhq/postiz-app/blob/v2.25.3/docker-compose.yaml): Redis 7.2, Temporal auto-setup 1.28.1, its separate PostgreSQL 16 database, Elasticsearch 7.17.27, UI 2.34.0, and the matching admin tools. **App PostgreSQL stays at 14.5 by explicit request**; no major database upgrade is bundled. Optional Spotlight monitoring is omitted. Temporal SQL-only visibility is not proposed here; Elasticsearch remains in the candidate. No capacity claim is made for the nominal 8 GB / 4 CPU host.

## Environment and storage

All operator-supplied values use required `${NAME:?...}` interpolation; missing values fail Compose rendering. Selected Facebook, LinkedIn, and Google credentials exist only under the application runtime environment, never build arguments. This source uses `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` for Google sign-in and YouTube; separate `GOOGLE_CLIENT_*` flags are not invented.

Set all values listed in `.env.example` in **Coolify runtime settings only**, with build-time availability disabled. Set a fresh `ARKX_CANDIDATE_ID` (8–40 lowercase letters/digits/hyphens, starting with a letter). Use independent random database passwords of at least 32 URL-safe characters (`A-Z`, `a-z`, digits, `_`, `-`); the application database URL interpolates the password without URL encoding. For migration, preserve the exact existing JWT secret; do not generate a replacement. A completely new disposable installation may use a fresh random secret. `ARKX_PUBLIC_URL` must be the HTTPS origin with no trailing slash.

There are four named infrastructure volumes in rehearsal and six total in production. Names begin `arkx-postiz-<candidate-id>-rehearsal-` or `arkx-postiz-<candidate-id>-production-`. Neither file references an external volume or production bind mount. Rehearsal and production cannot share their named data volumes. **Unused names on the real host are unproven until the first-create preflight runs there.** Never point this candidate at existing production mounts. Subsequent deliberate restarts reuse the new candidate's own volumes; `--check-new-volumes` is specifically a first-create check.

The fork builds from the repository root using upstream [Dockerfile.dev](https://github.com/gitroomhq/postiz-app/blob/v2.25.3/Dockerfile.dev), with an explicit Prisma tool preparation step added. Its only supplied build argument is the public version. It copies the source tree, and this fork extends `.dockerignore` to exclude runtime `.env` files and private operation files. Keep real secrets outside the checkout anyway. `preflight.py --mode ...` rejects non-example `.env` files. Coolify-generated environment files and secret/build-time settings need separate review by main before building. The source build fetches public packages; no paid provider or outbound integration is configured for the build.

Registration uses the supported `DISABLE_REGISTRATION=true`. Upstream code still permits the first organization on an empty database (and generic OAuth is an exception). This setting is not an absolute first-account lock. Generic OAuth is not configured in this candidate.

## Exact commands and guard

From the repository root, review-only checks (no daemon needed):

```sh
python3 deploy/arkx/preflight.py
docker compose --project-directory . --env-file deploy/arkx/.env.example -f deploy/arkx/compose.rehearsal.yaml config --quiet
docker compose --project-directory . --env-file deploy/arkx/.env.example -f deploy/arkx/compose.production.yaml config --quiet
sh -n deploy/arkx/runtime-start.sh
```

For a future authorized first creation, with actual values already supplied securely in the target process environment (do not copy them into the repository):

```sh
python3 deploy/arkx/preflight.py --mode rehearsal --check-new-volumes
docker compose --project-directory . --env-file /dev/null -f deploy/arkx/compose.rehearsal.yaml up -d
```

This creates infrastructure only. It is **not a Postiz schema rehearsal**, restored-data rehearsal, browser rehearsal, or proof that imported schedules will not publish. Do not merge this file with the production file to rehearse. No claimed application outbound isolation has been tested. A future actual application rehearsal needs separately proven outbound controls before any backend/orchestrator process starts.

Future production activation is a separate explicit choice with real sends possible (social posting, queued workflows, provider calls, email when configured). Upstream startup also runs `prisma-db-push`, which can change the selected application database schema. Main must review restored queues, account bootstrap, database backup/restore compatibility, and schema safety before activation:

```sh
python3 deploy/arkx/preflight.py --mode production --check-new-volumes
docker compose --project-directory . --env-file /dev/null -f deploy/arkx/compose.production.yaml --profile production up -d
```

That command requires runtime `ARKX_RUNTIME=production`. In Coolify, the selected production runtime must explicitly apply **`--profile production`** to the Compose startup invocation, plus provide that runtime environment value. The candidate does not assume Coolify activates profiles automatically. Leave the GitHub-connected resource paused until the operator confirms its actual command/profile handling. Selecting the production file without the profile leaves the publisher stopped. Giving the wrong runtime value exits the application with code 64 before nginx or Postiz starts.

## Checks and remaining gaps

The local check covers: exact service/image sets, zero published ports, application port, commit image, named-volume separation, required secret interpolation, copied rehearsal Temporal configuration, unmounted production Temporal defaults, four denied startup selections, nginx/schema failure propagation, one allowed selection using stubs, direct PM2 process ownership, Compose defaults/profiles, and rejection of missing required settings. In a temporary synthetic checkout, it also injects Coolify-style `env_file: .env` into every service in both files, proves normalized root image/rehearsal-bind/env-file paths and successful environment loading, and reproduces missing nested `.env` failures with the wrong project directory. The focused continuous integration check also covers four allowed Compose shapes and 19 rejected configuration/network/guard cases. The built-image check parses the wrapper/ecosystem and all three application entrypoints offline, checks PM2 runtime availability and excludes runtime `.env` files; it does not launch applications. No real checkout `.env` is created or read. Configuration rendering does not execute an image.

Open target-side checks: unused volume names; available memory/disk; exact Coolify YAML/profile/proxy/build-time environment behavior after this path correction; Temporal and application readiness; PostgreSQL 14.5 compatibility with the existing data and current schema; database restore and queue inspection; OAuth callback settings; first-account bootstrap and live application behavior. Temporal's dependency is service-started rather than API-ready, matching a candidate rather than proven healthy startup. No full-stack run or build-green claim is made for this patch. The operator owns root documentation, Git operations, and Coolify; the scoped workflow correction only adds explicit root flags.

Lesson: a production profile alone does not block an explicitly targeted service. The runtime command guard closes that accidental path; rehearsal omits the application entirely.

Coolify working-directory correction: all paths use the **repository root** as their coordinate base. The production application is pulled from GitHub Container Registry. Rehearsal retains `./deploy/arkx/dynamicconfig:/etc/temporal/config/dynamicconfig:ro`; production Temporal uses its image's built-in `config/dynamicconfig/docker.yaml` without a host bind. A helper checkout does not guarantee that a relative bind source exists on the Docker host. From the checkout root, the deployment uses these custom commands (Coolify supplies the selected compose file and generated environment):

```sh
ARKX_REGISTRY_HELPER_IMAGE=sha256:b0cf60e6d6ecc73696811fe105d7b187b0fa387bd8ec62c647177420daf42bad ARKX_REGISTRY_CONFIG_DIR=/var/lib/arkx-postiz-registry/1alwcyoouzoa5mf1jrnfzm87 bash deploy/arkx/pull-image.sh
bash deploy/arkx/start-stack.sh
```

Do not use `--project-directory ./deploy/arkx`: Coolify's generated per-service `.env` then resolves to the missing `deploy/arkx/.env`, even when its CLI `--env-file` points at the root `.env`. The pull helper verifies the selected full commit and image source/revision labels. The custom start command above does not activate the production profile. The startup profile and exact `ARKX_RUNTIME=production` guard remain in place. The operator owns deliberate activation and target-side confirmation. Local commands must likewise pass `--project-directory .` from the checkout root, or an absolute checkout-root path from elsewhere. Keep generated runtime secrets out of the source build as required above.

Review correction: Elasticsearch uses its standard `/usr/share/elasticsearch/data` persistence path in both candidates. The upstream sample's alternative mount path was not retained. Container-recreation persistence remains a runtime check.

Migration compatibility: the existing signing secret is preserved exactly. The preflight requires a nonblank value rather than a new-secret length policy; changing a working legacy secret to satisfy a template would invalidate preserved authentication/encryption. Exact source-to-candidate equality is checked privately at configuration transfer and must be rechecked before activation.

The path regression check covers both Compose files: repository-root configuration succeeds, while the nested-directory alternative fails before startup. It checks all generated service environment files, the pinned application image, the read-only rehearsal Temporal configuration bind and the absence of production Temporal mounts. Corrected target-side Temporal readiness remains a live acceptance check.

## Commit-image delivery

GitHub Actions builds `Dockerfile.dev` without production credentials and publishes only `ghcr.io/ark-x-ai-lab/postiz-app:sha-<full-commit>`. `SOURCE_COMMIT` must equal the deployment checkout. Coolify uses `bash deploy/arkx/pull-image.sh` with the two explicit nonsecret selectors shown above as its custom build command; it never compiles the source. The package remains private by the owner’s choice. Its dedicated credential grants only `read:packages`; it is not the shared server-wide registry login. The helper uses a separate protected host Docker config through a pinned Docker CLI helper, checks revision/source labels, and fails on authentication or unrecognized errors. Publication and offline engine checks passed for commit `f65ecd28`; effective Coolify private-pull behavior remains an acceptance check.

Prisma delivery patch: `prisma-generate` and startup `prisma-db-push` use the installed, pinned Prisma 6.5.0 dependency rather than downloading another CLI with `pnpm dlx`. The Docker build runs `prisma --version` with build-time networking. Before publication, CI executes the bundled schema engine and loads the client engine with no network, a read-only filesystem and an unprivileged user, requiring the lockfile engine commit. The old source-built image fails this gate as expected; the GitHub-built `f65ecd28` image passed both engine and CLI checks. The restored-data schema run now passes, including all original values across 45 tables and separate backup restoration. Full application recovery remains open.

Registry separation: the dedicated token is retained only as candidate metadata with `is_buildtime=false` and `is_runtime=false`. It must not enter generated service environment files, image arguments or application containers. The operator transfers it directly to a candidate-only, root-owned Docker configuration. Do not replace the existing server-wide GHCR login, which other applications use. The pull helper configuration specifies only the protected host directory and the trusted Docker CLI helper image ID; neither is a credential.

## Service environment ownership

Coolify 4.6.0 adds the root `.env` file to every generated service. `start-stack.sh` runs `prepare-compose.cjs` inside the pinned application image with no network and a read-only filesystem. It removes that injected `env_file` and detaches the recognized shared external Coolify network from all seven infrastructure services. It retains their exact source internal network bindings, publisher routing, labels and explicit environment mappings, and streams the result directly into Compose. The external network must be the sole extra definition, with a 24-character lowercase alphanumeric key matching its declared name. Unexpected network definitions/attachments, environment files, production Temporal mounts or changed publisher guards fail closed. Runtime values remain available for interpolation through `--env-file .env`, but are not inherited wholesale. Seven infrastructure services therefore receive no publisher/provider credentials. Actual target-side network attachments still require inspection after deployment.

Default startup clears inherited `COMPOSE_PROFILES` and creates infrastructure only. Deliberate publisher activation additionally requires `ARKX_RUNTIME=production bash deploy/arkx/start-stack.sh --activate-publisher`; the application’s own exact runtime guard remains independent. Do not activate before restored-data, recovery and single-publisher checks pass. Image selection reads only the public commit field from Coolify’s generated environment file; build and runtime credential values are never sourced into the shell.

Production application restarts use PM2's directly supervised Node processes. The prior upstream package chain supervised pnpm/dotenv wrappers; the main session observed orphaned Node children retaining ports after `pm2 restart`, while replacement children failed with `EADDRINUSE`. Successful responses from those old children did not prove a healthy restart. The Ark-x wrapper retains the installed, guarded schema push before foreground PM2 startup, and service processes inherit the explicitly supplied production environment. Upstream package scripts and the generic Dockerfile command remain unchanged; production Compose selects the Ark-x wrapper. Main's fresh internal-clone test with the existing `1127657` image and two source files copied into the fresh isolated container before startup, with recorded hashes, passed: three online services at exactly one restart each, zero port-collision errors, and the same rescheduled waiting run/timer surviving restart. Deletion terminated both runs with zero active runs and no delivery activities. See the [migration plan](../../plans/coolify-migration.md) for overlay hashes and evidence limits; the next image and production Temporal checks remain open.

`check-temporal.cjs` checks namespace readiness and the two required Text attributes, with an explicit `--register-missing` operator action. `check-backend.cjs` accepts only the API key through stdin and checks the private backend’s allowed/denied API-key cases, integration count and registration setting. Session-token testing is separate because upstream session middleware can write a missing organization API key even for a GET request. Neither helper publishes.


## Current recovered-data read smoke

`rehearse-migration.py --stage app-read` is a separate operator preparation path; without `--app-read-execute` and an exact reviewed plan hash it only prints a local source plan. It does not invoke the historical Post0/schema-push rehearsal. Execution requires separately prepared, fresh derived storage, pinned evidence, an isolated internal network, direct server/backend entrypoints and no publishing worker, cron, provider/mail/AI credentials or published ports. Used engine copies and original/rollback volumes stay retained. Preserved-session input is optional but its absence remains an explicit continuity gap; media-byte readability is not HTTP serving or delivery proof.

The initial concurrent memory admission retains the full peak plus 1 GiB operating reserve. Later transitions use the lower sampled `MemAvailable` plus only measured owned cgroup-v2 `anon + shmem` already charged to it. Exact container/process identity, cgroup path, ownership, limits and stable membership are checked around sampling. Role limits, file cache, kernel/slab, stopped containers and unrelated processes receive no credit; ambiguity refuses. Only aggregate byte counters enter the capacity journal. Disk admission still reserves operating space plus the explicit scratch-growth budget.


Current disposable app-read ceilings are PG14/PG16 160 MiB each, Redis 64 MiB, Elasticsearch 640 MiB, Temporal 256 MiB, backend 384 MiB and one probe 64 MiB: **1,728 MiB total plus the unchanged 1,024 MiB reserve = 2,752 MiB (2,885,681,152 bytes)**. This tightens maximum allocations rather than relaxing the admission assertion. The same caps bind the public plan, container create/inspect and exact cgroup `memory.max`; old or otherwise wrong caps refuse. Current original-service usage samples are not cold-start peaks or compatibility proof. A normal isolated stack must still pass actual startup/readiness/reads with these ceilings. Elasticsearch retains its 256 MiB heap and 128 MiB temp split; PostgreSQL retains 16 MiB shared buffers and 30 connections. Disk/growth, isolation and identity controls are unchanged.


Elasticsearch's post-start readiness budget is 120 seconds, aligned with the existing engine check; PG14/PG16/Redis/backend remain at 30 seconds and CPU stays 0.5. Readiness still requires actual green/yellow health with `timed_out=false`; red, malformed or expired observations never count as ready. Success adds only an `es-ready` journal state and `elapsed_ms`, measured from entry into the readiness wait (excluding earlier create/start command time). This source-budget alignment is not a proven explanation for a past timeout: the next normal fresh isolated run must establish compatibility and actual wait duration.

Inspect the exact pinned images before execution: stock metadata keys are allowed only on their immutable images, and container labels must equal baked image labels plus the unique run labels. Omitted or null empty image labels are supported; malformed metadata and reserved ownership collisions refuse. Failed checks record only bounded function names and line numbers from this helper, never exception messages, arguments or credentials. A failed or used family is retained and is not automatically retried.

The isolated Elasticsearch recipe copies its baked configuration into a private 0700 tmpfs directory, rejects declarative plugin installation, and executes the same binary as a nonroot user. It uses a 256 MiB heap, one JVM processor, private JVM/JNA/error paths, disabled heap dumps and reset JVM logging. Its temporary-memory budget remains 128 MiB: 124 MiB working temp plus 4 MiB logs owned by uid1000/gid0 with mode0700; the root filesystem stays read-only. The Temporal server uses a verified executable and source-derived stores; its dynamic configuration path is absolute (`/etc/temporal/config/dynamicconfig/docker.yaml`) so it does not depend on the app working directory. Namespace/attribute readability must pass before backend bootstrap. These are source preparations, not acceptance of a full recovered application or workflow replay.

## Image capacity

The runtime recipe now uses a separate final stage: all application dependencies and compiled output are retained, while compiler-stage layers and the verified frontend Turbopack build cache stay out of the delivered image. Native runtime libraries, pnpm/PM2 shims, nginx and existing startup behavior remain. The repaired source’s successful build passed the runtime gates and measured a 3,647,598,592-byte image archive within the 5,000,000,000-byte budget. This is image-build proof; live disk capacity and deployment remain separate checks.

Do not use `docker image inspect .Size` alone as a disk-capacity estimate with the current containerd image store: observed content size was about 1.6 GB while actual image disk use was about 6.9 GB. CI now streams `docker image save` into a byte count without saving another archive. The default uncompressed archive budget is 5,000,000,000 bytes; administrators can set the Actions variable `ARKX_MAX_IMAGE_ARCHIVE_BYTES` after reviewing host capacity. A pull still needs extra room for compressed content and healthy database operation. Recheck target disk capacity before deploying.

The removed `.next/cache/turbopack` directory held 572 MB in the previous image; compiled `.next/server` and `.next/static` are preserved. [Next.js documents `.next/cache` as shared build cache](https://nextjs.org/docs/pages/guides/ci-build-caching). No production dependency pruning is performed.

## Dependency cache preparation

The local [cache plan](../../plans/publisher-build-cache.md) separates locked package downloads from source compilation and exports only that dependency stage. `python3 deploy/arkx/test_build_inputs.py` exercises accepted public settings and rejected private/unreviewed configuration. `preflight.py` checks the actual `.npmrc` before a cache-enabled build. Install scripts and all final-image gates remain enabled. Cold and fresh-runner warm checks passed: the dependency-cache step fell from 236 seconds to 3 seconds, but the full source build did not get faster (382 seconds versus 398 seconds). Do not report this as a whole-build speedup.

## Native graphics acceptance

The source approval list adds only `canvas` alongside `bcrypt`; dependency versions remain locked. After frozen offline install, the recipe explicitly runs `pnpm rebuild bcrypt canvas --store-dir=/pnpm/store`: the pinned fixture confirmed that this respects approval and avoids replaying project hooks, unlike relying on install exit0 alone. The final-image `native-runtime.test.cjs` gate runs offline and unprivileged before publication, checking the actual PNG renderer, crypto module, sanitizer and installed DOM parsers. The old D7 image has a confirmed missing canvas binding; it is not an expected passing fixture. Source `46d20f2929679c4db740b1f8b20b20be0bf8173c` passed the repaired-image gate in [run 42301401305](https://github.com/Ark-x-AI-Lab/postiz-app/actions/runs/42301401305):159 tests passed, none failed or skipped. Canvas rendered successfully without adding system libraries. The commit image was published; production remains on D7. Live login/layout and ordinary editorial posting remain separate checks.

### Read-only workflow state

`workflow-state.cjs` runs inside the existing publisher with its configured Temporal client. It lists at most 100 running workflows and describes pending activity types/states without fetching workflow inputs, results or history payloads. Workflow identities are hashed; malformed, truncated or timed-out responses return an unavailable result rather than a partial count. It never starts, signals, cancels or replays a workflow.

Run its offline regression suite with `node --test deploy/arkx/workflow-state.test.cjs`. Before using its output for a restart decision, inspect the corresponding workflow source: zero pending activities does not exclude queued signals, durable timers or later notification sends. Preserve the Temporal volumes and verify recovery separately.

### Current-app cold backup

`backup-current.py` is a host operator tool for the pinned current Postiz deployment; it defaults to a read-only plan. Root execution additionally requires `--execute --approved-plan-hash <fresh-plan-sha256>`. The plan binds helper bytes, all eight container/image identities, six exact volume mappings and private backup paths. Changed identities or shared mounts refuse; do not retarget the retired backup scripts to make them pass.

Acquisition gracefully stops only the six persistent-state writers, including the Temporal server. The stateless UI and admin tools are never signalled or restarted; their running state, absent mounts and unchanged identities are checked. Writers must exit cleanly; there is no exit-2 exemption or force-kill fallback. Six private archives are checked before original writer restart and all-eight readiness checks. Cooperative signal handling preserves bounded recovery attempts. Partial backups remain retained and never become a successful manifest.

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/arkx -p test_backup_current.py` for the 50 offline checks. One current six-volume acquisition and separate checksum/structure readback passed on 7 November; all eight originals were running afterward. `cold-backup-verified` proves acquisition and archive checks, not isolated restoration or permission to deploy. Recovery testing remains separate. Old container IDs or a newer deployment intentionally make this pinned tool refuse until its exact target is reviewed again.
