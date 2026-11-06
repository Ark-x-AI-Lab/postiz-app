# Ark-x Coolify candidate — v2.25.3

Status: GitHub-built commit image candidate. The first server source build passed but startup failed on a generated environment-file path; root-path regression checks now pass. A coherent backup and isolated database restore passed. Schema upgrade is blocked until the image bundles its Prisma schema engine. Stack readiness and live migration remain unproven. Source: `a5affe62c74e39c1e7ac960d50d8418b807d532c`, branch `deploy/arkx`.

## Files and choices

- `compose.rehearsal.yaml`: seven infrastructure services, **no Postiz service**, no published ports, both networks internal. No application database changes or publishing workflows can run through this file.
- `compose.production.yaml`: the same infrastructure plus the commit-tagged application image on internal port **5000**, behind the `production` profile. Only the application joins an outbound-capable network. Coolify must route its app domain to `postiz:5000`, never to infrastructure services.
- `runtime-start.sh`: requires the exact runtime value `ARKX_RUNTIME=production` before executing the unchanged upstream command, `nginx && pnpm run pm2`. This custom wrapper variable is not a Postiz feature flag. It also blocks an accidental explicit `up postiz`, which would bypass profile selection.
- `dynamicconfig/development-sql.yaml`: exact upstream configuration, mounted read-only.
- `.env.example`: synthetic values for configuration rendering only. It intentionally sets `ARKX_RUNTIME=rehearsal`, so it cannot start Postiz through this wrapper. Never use it to launch infrastructure either; replace every synthetic value.
- `preflight.py`: Python standard library checks; no installs, no container startup, no image build. The default checks use synthetic values and shell command stubs only.

The compose files use JSON-formatted YAML so the Python standard library can inspect their exact content. Docker Compose accepts this syntax; Coolify's handling still needs target-side confirmation.

The baseline follows the tagged [official v2.25.3 compose](https://github.com/gitroomhq/postiz-app/blob/v2.25.3/docker-compose.yaml): Redis 7.2, Temporal auto-setup 1.28.1, its separate PostgreSQL 16 database, Elasticsearch 7.17.27, UI 2.34.0, and the matching admin tools. **App PostgreSQL stays at 14.5 by explicit request**; no major database upgrade is bundled. Optional Spotlight monitoring is omitted. Temporal SQL-only visibility is not proposed here; Elasticsearch remains in the candidate. No capacity claim is made for the nominal 8 GB / 4 CPU host.

## Environment and storage

All operator-supplied values use required `${NAME:?...}` interpolation; missing values fail Compose rendering. Selected Facebook, LinkedIn, and Google credentials exist only under the application runtime environment, never build arguments. This source uses `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` for Google sign-in and YouTube; separate `GOOGLE_CLIENT_*` flags are not invented.

Set all values listed in `.env.example` in **Coolify runtime settings only**, with build-time availability disabled. Set a fresh `ARKX_CANDIDATE_ID` (8–40 lowercase letters/digits/hyphens, starting with a letter). Use independent random database passwords of at least 32 URL-safe characters (`A-Z`, `a-z`, digits, `_`, `-`); the application database URL interpolates the password without URL encoding. For migration, preserve the exact existing JWT secret; do not generate a replacement. A completely new disposable installation may use a fresh random secret. `ARKX_PUBLIC_URL` must be the HTTPS origin with no trailing slash.

There are four named infrastructure volumes in rehearsal and six total in production. Names begin `arkx-postiz-<candidate-id>-rehearsal-` or `arkx-postiz-<candidate-id>-production-`. Neither file references an external volume or production bind mount. Rehearsal and production cannot share their named data volumes. **Unused names on the real host are unproven until the first-create preflight runs there.** Never point this candidate at existing production mounts. Subsequent deliberate restarts reuse the new candidate's own volumes; `--check-new-volumes` is specifically a first-create check.

The source [Dockerfile.dev](https://github.com/gitroomhq/postiz-app/blob/v2.25.3/Dockerfile.dev) is unchanged and builds from the repository root. Its only supplied build argument is the public version. It copies the source tree, and this fork extends `.dockerignore` to exclude runtime `.env` files and private operation files. Keep real secrets outside the checkout anyway. `preflight.py --mode ...` rejects non-example `.env` files. Coolify-generated environment files and secret/build-time settings need separate review by main before building. The source build fetches public packages; no paid provider or outbound integration is configured for the build.

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

The local check covers: exact service/image sets, zero published ports, application port, commit image, named-volume separation, required secret interpolation, copied Temporal configuration, four denied startup selections, one allowed selection using stubs, Compose defaults/profiles, and rejection of missing required settings. In a temporary synthetic checkout, it also injects Coolify-style `env_file: .env` into every service in both files, proves normalized root image/bind/env-file paths and successful environment loading, and reproduces missing nested `.env` failures with the wrong project directory. No real checkout `.env` is created or read. Configuration rendering does not execute an image.

Open target-side checks: unused volume names; available memory/disk; exact Coolify YAML/profile/proxy/build-time environment behavior after this path correction; Temporal and application readiness; PostgreSQL 14.5 compatibility with the existing data and current schema; database restore and queue inspection; OAuth callback settings; first-account bootstrap and live application behavior. Temporal's dependency is service-started rather than API-ready, matching a candidate rather than proven healthy startup. No full-stack run or build-green claim is made for this patch. The operator owns root documentation, Git operations, and Coolify; the scoped workflow correction only adds explicit root flags.

Lesson: a production profile alone does not block an explicitly targeted service. The runtime command guard closes that accidental path; rehearsal omits the application entirely.

Coolify working-directory correction: all paths use the **repository root** as their coordinate base. The production application is pulled from GitHub Container Registry; both Temporal binds are `./deploy/arkx/dynamicconfig:/etc/temporal/config/dynamicconfig:ro`. From the checkout root, the deployment uses these custom commands (Coolify supplies the selected compose file and generated environment):

```sh
bash deploy/arkx/pull-image.sh
docker compose --project-directory . up -d
```

Do not use `--project-directory ./deploy/arkx`: Coolify's generated per-service `.env` then resolves to the missing `deploy/arkx/.env`, even when its CLI `--env-file` points at the root `.env`. The pull helper verifies the selected full commit and image source/revision labels. The custom start command above does not activate the production profile. The startup profile and exact `ARKX_RUNTIME=production` guard remain in place. The operator owns deliberate activation and target-side confirmation. Local commands must likewise pass `--project-directory .` from the checkout root, or an absolute checkout-root path from elsewhere. Keep generated runtime secrets out of the source build as required above.

Review correction: Elasticsearch uses its standard `/usr/share/elasticsearch/data` persistence path in both candidates. The upstream sample's alternative mount path was not retained. Container-recreation persistence remains a runtime check.

Migration compatibility: the existing signing secret is preserved exactly. The preflight requires a nonblank value rather than a new-secret length policy; changing a working legacy secret to satisfy a template would invalidate preserved authentication/encryption. Exact source-to-candidate equality is checked privately at configuration transfer and must be rechecked before activation.

The path regression check covers both Compose files: repository-root configuration succeeds, while the nested-directory alternative fails before startup. It checks all generated service environment files, the pinned application image and the read-only Temporal configuration bind. Effective Coolify startup remains a live acceptance check.

## Commit-image delivery

GitHub Actions builds `Dockerfile.dev` without production credentials and publishes only `ghcr.io/ark-x-ai-lab/postiz-app:sha-<full-commit>`. `SOURCE_COMMIT` must equal the deployment checkout. Coolify uses `bash deploy/arkx/pull-image.sh` as its custom build command; it never compiles the source. The helper pulls anonymously, checks revision/source labels, and fails on authentication or unrecognized errors. The package must permit anonymous pulls before the first deployment. Actual publication, registry visibility and Coolify pull behavior are acceptance checks, not yet proven.

Prisma delivery patch: `prisma-generate` and startup `prisma-db-push` use the installed, pinned Prisma 6.5.0 dependency rather than downloading another CLI with `pnpm dlx`. The Docker build runs `prisma --version` with build-time networking. Before publication, CI executes the bundled schema engine and loads the client engine with no network, a read-only filesystem and an unprivileged user, requiring the lockfile engine commit. The old source-built image fails this gate as expected; a new image passing it remains required.
