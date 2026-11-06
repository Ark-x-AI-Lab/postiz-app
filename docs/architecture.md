# Publisher architecture

Social Studio and Postiz are separate applications and repositories. Studio creates and approves content and calls Postiz's server-side public API. Postiz holds workspace/channel state and performs scheduled delivery.

## Current deployment

The GitHub-built Postiz v2.25.3 replacement serves `postiz.arkx.group`, with checked analytics source `d7a1911508da31fc20ea42a51c1bb2f4f38a7f31`. Coolify manages the replacement application and its PostgreSQL, Redis and Temporal infrastructure. The old v2.10.1 service is retired; its four original configuration/media/database/Redis volumes and coherent recovery backup remain. This is retained recovery data, not a second active publisher.

The repository's deployment branch is `deploy/arkx`. GitHub Actions builds and checks an immutable `ghcr.io/ark-x-ai-lab/postiz-app:sha-${SOURCE_COMMIT}` image off-host; Coolify pulls the exact image through the existing GitHub App. Automatic deployment is disabled. Feature branches do not deploy. Check the actual image/source receipt rather than a branch name before release acceptance.

## Runtime boundaries

Provider credentials and signing/encryption configuration remain runtime-only. Git holds source, build definitions and operational instructions. A dedicated read-only registry credential has its own protected Docker configuration; it is excluded from image builds, app environments and the shared server login.

Compose commands resolve from the repository root with `--project-directory .`, matching Coolify's generated root environment file. Preparation removes recognized global environment-file injection and Coolify's shared external network from infrastructure services while preserving their internal networks and publisher routing. Unknown network shapes fail closed. Production Temporal uses the pinned image's built-in `config/dynamicconfig/docker.yaml`, with no host dynamic-configuration mount.

The guarded startup wrapper starts nginx, runs the installed Prisma schema command, then replaces itself with foreground PM2 supervising three direct Node entrypoints: backend, orchestrator and frontend. It avoids orphaned pnpm/dotenv child chains. Restart checks must inspect process ownership and stable restart counts as well as HTTP health.

## Data and recovery

Cutover preserved original values across 45 tables; the upgraded schema has 88 tables. Redis 7 keys and 3 media files matched. The fork retains the legacy `User.marketplace Boolean @default(true)` field to avoid dropping the old column during guarded synchronization; it is not a new product feature.

Prisma client generation alone does not prove the CLI engine is bundled. The built image must pass offline, unprivileged schema/client engine checks. Schema upgrades run through the normal startup command. Reverting only the image is not a proven rollback: stop the replacement and restore a coherent pre-upgrade database/media/configuration point. Independent old-image API recovery passed; full old-version UI recovery remains unproven.

## Private sign-in

Public registration stays disabled. The local [private-registration correction](../plans/private-registration.md) removes bootstrap/generic-provider exceptions and rejects unknown provider callbacks before returning registration tokens. Existing linked provider accounts and activated local accounts retain their current login methods. No email-based linking or new staff allowlist is introduced. This correction is not deployed yet; existing runtime configuration and account authorization still require separate release checks.

## Next release

The Facebook summary-counter image passed its source build and offline checks but is not deployed. Private-registration changes are under local development on a separate branch. Limited host capacity currently holds new publisher pulls and Studio builds. Docker size fields do not guarantee reclaimable space; only an approved exact cleanup followed by a measured readback can establish recovered headroom. No database, media, backup or shared-volume deletion is part of these patches.
