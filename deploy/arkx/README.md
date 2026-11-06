# Ark-x Coolify candidate — v2.25.3

Status: deployment candidate under review; no image build or stack startup has occurred. Source: `a5affe62c74e39c1e7ac960d50d8418b807d532c`, branch `deploy/arkx`.

## Files and choices

- `compose.rehearsal.yaml`: seven infrastructure services, **no Postiz service**, no published ports, both networks internal. No application database changes or publishing workflows can run through this file.
- `compose.production.yaml`: the same infrastructure plus the source-built application on internal port **5000**, behind the `production` profile. Only the application joins an outbound-capable network. Coolify must route its app domain to `postiz:5000`, never to infrastructure services.
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
docker compose --env-file deploy/arkx/.env.example -f deploy/arkx/compose.rehearsal.yaml config --quiet
docker compose --env-file deploy/arkx/.env.example -f deploy/arkx/compose.production.yaml config --quiet
sh -n deploy/arkx/runtime-start.sh
```

For a future authorized first creation, with actual values already supplied securely in the target process environment (do not copy them into the repository):

```sh
python3 deploy/arkx/preflight.py --mode rehearsal --check-new-volumes
docker compose --env-file /dev/null -f deploy/arkx/compose.rehearsal.yaml up -d
```

This creates infrastructure only. It is **not a Postiz schema rehearsal**, restored-data rehearsal, browser rehearsal, or proof that imported schedules will not publish. Do not merge this file with the production file to rehearse. No claimed application outbound isolation has been tested. A future actual application rehearsal needs separately proven outbound controls before any backend/orchestrator process starts.

Future production activation is a separate explicit choice with real sends possible (social posting, queued workflows, provider calls, email when configured). Upstream startup also runs `prisma-db-push`, which can change the selected application database schema. Main must review restored queues, account bootstrap, database backup/restore compatibility, and schema safety before activation:

```sh
python3 deploy/arkx/preflight.py --mode production --check-new-volumes
docker compose --env-file /dev/null -f deploy/arkx/compose.production.yaml --profile production up -d --build
```

That command requires runtime `ARKX_RUNTIME=production`. In Coolify, the selected production runtime must explicitly apply **`--profile production`** to the Compose startup invocation, plus provide that runtime environment value. The candidate does not assume Coolify activates profiles automatically. Leave the GitHub-connected resource paused until main confirms its actual command/profile handling. Selecting the production file without the profile leaves the publisher stopped. Giving the wrong runtime value exits the application with code 64 before nginx or Postiz starts.

## Checks and remaining gaps

The local check covers: exact service/image sets, zero published ports, application port, build arguments, named-volume separation, required secret interpolation, copied Temporal configuration, four denied startup selections, one allowed selection using stubs, Compose defaults/profiles, and rejection of missing required settings. Configuration rendering does not execute an image.

Open target-side checks: unused volume names; available memory/disk; exact Coolify YAML/profile/proxy/build-time environment behavior; actual source image build; Temporal and application readiness; PostgreSQL 14.5 compatibility with the existing data and current schema; database restore and queue inspection; OAuth callback settings; first-account bootstrap and live application behavior. Temporal's dependency is service-started rather than API-ready, matching a candidate rather than proven healthy startup. No full-stack run or build-green claim is made. Main owns review, root documentation, workflow, Git operations, and Coolify.

Lesson: a production profile alone does not block an explicitly targeted service. The runtime command guard closes that accidental path; rehearsal omits the application entirely.

Coolify working-directory note: the source build context and configuration bind mount are relative to this directory. The prepared Coolify custom build/start commands must set `--project-directory ./deploy/arkx` explicitly from the repository checkout root, rather than accepting Coolify's automatic project-directory override. Target-side resolution remains an acceptance check before any activation.

Review correction: Elasticsearch uses its standard `/usr/share/elasticsearch/data` persistence path in both candidates. The upstream sample's alternative mount path was not retained. Container-recreation persistence remains a runtime check.
