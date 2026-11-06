# Publisher architecture

Studio and Postiz are separate applications and repositories. Studio calls Postiz's server-side public API. Postiz holds workspace/channel state and performs scheduled delivery.

Current live deployment: upstream Postiz v2.10.1, PostgreSQL 14.5 and Redis 7.2 in a Coolify service. Four persistent volumes contain configuration, uploaded media, PostgreSQL state and Redis state.

Target: Postiz v2.25.3 built in GitHub Actions and pulled as a commit-tagged GitHub Container Registry image from deploy/arkx, managed by the existing Ark-x-AI-Lab Coolify GitHub App. This version additionally requires Temporal for scheduling. Its database and workflow state must be backed up consistently. A second active publisher must never run against production state during rehearsal.

Secrets remain in Coolify runtime environment variables. Git holds source, build/deployment definitions and operational instructions only. Postiz upstream is retained as the upstream Git remote. The existing live domain and Studio's publisher API contract are retained at cutover.

Database-schema upgrades occur during the upstream startup sequence. Reverting only the image is not a proven rollback: retain a coherent pre-upgrade database/media/configuration recovery point and stop the replacement before restoring the old stack.

The candidate is connected through the existing GitHub App; automatic deployment is disabled. A coherent backup and isolated PostgreSQL restore have passed. The candidate schema update has not passed: the first source-built image lacks the Prisma schema engine and attempts a runtime download into a read-only directory. Runtime topology/readiness, the complete schema rehearsal, application recovery and cutover remain open. Server source builds are stopped because disk space became constrained; the replacement pipeline builds off-host.

The fork retains the legacy `User.marketplace Boolean @default(true)` schema field from v2.10.1. Upstream v2.25.3 removed it; retaining it avoids intentionally dropping the column during guarded schema synchronization. This is a compatibility preservation change, not a new product option. The real database delta and restored-data startup still require rehearsal.

All deployment paths resolve from the repository root. The application image uses `ghcr.io/ark-x-ai-lab/postiz-app:sha-${SOURCE_COMMIT}`. Temporal's bind source is `./deploy/arkx/dynamicconfig`, and custom commands explicitly use `--project-directory .`. This also matches Coolify's generated root `.env`; using the nested deployment directory caused the first infrastructure startup to fail before any candidate container started.
