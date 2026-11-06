# Publisher dependency-download cache

Status: local preparation; no cold/warm build or speedup established.

The source image currently installs dependencies after every included source change. The next recipe separates locked package downloads from application source, then keeps normal install scripts, Prisma generation and compilation after the source copy. Node22.23.4, pnpm10.6.1, Linux/amd64 and dependency build approvals remain unchanged.

Only a dependency-stage cache is exported to GitHub Actions. It includes build tools and public package inputs; the full application build is not exported as a second large cache. A fixed platform/package-manager scope permits reuse between builds of the same branch. GitHub's branch access rules still apply. The cache is an optimization, never the source selector: full commit image tags and source labels remain mandatory.

`preflight.py` admits only five reviewed public `.npmrc` settings and rejects unknown keys/values, duplicates, comment payloads, missing settings, symlinks and oversized files without echoing input values. A future registry credential requires a separately reviewed secret-mount design. The package store stays outside `/app` and must not enter the final runtime image. Cache hits must not skip the existing privacy, runtime, source, size, analytics or registration checks. Image publication remains after those checks.

## Acceptance

1. Actual pinned cold source build succeeds; frozen offline install leaves the lock unchanged. A deliberately missing package must fail offline rather than use a registry fallback.
2. A later source-only commit on the same branch imports the cache and reports the dependency fetch as cached. Record timings before claiming a benefit. Lock/config changes intentionally invalidate downloads; manifest/lock disagreement must fail.
3. Prisma generation/CLI engines, bcrypt, canvas, sharp and Temporal checks pass offline in the final image. Hooks remain enabled during installation; caching must not silently approve dependency scripts.
4. Public-configuration and existing context-exclusion checks pass before export. Final image lacks `/pnpm/store`; dependencies and compiled frontend output remain usable.
5. Runtime source/image selection, schema behavior and live credentials remain unchanged. No production deploy or cache deletion is included in local preparation.

References: [pnpm10 fetch](https://pnpm.io/10.x/cli/fetch), [Docker GitHub Actions cache](https://docs.docker.com/build/cache/backends/gha/). Cache confidentiality, quotas/eviction and the actual cold/warm results remain part of acceptance; no guaranteed build-time or cost reduction is claimed.

Cheap pinned checks passed under Node22.23.4/pnpm10.6.1: fetch accepts the chosen options and skips the synthetic project hook; offline install runs that hook; an empty-store package install fails specifically with the offline-package-missing error. This is option/hook/refusal proof, not cold/warm performance. A D7 baseline probe found bcrypt/sharp load but canvas lacks its native binding; that separate existing compatibility gap remains open before final native acceptance.
