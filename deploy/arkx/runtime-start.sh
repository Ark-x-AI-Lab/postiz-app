#!/bin/sh
# This is an Ark-x wrapper guard, not a Postiz environment flag.
set -eu
if [ "${ARKX_RUNTIME-}" != production ]; then
  echo 'Refusing Postiz startup: ARKX_RUNTIME must equal production.' >&2
  exit 64
fi
# Complete schema preparation before supervising the application processes directly.
nginx
pnpm run prisma-db-push
exec pm2-runtime start /app/deploy/arkx/ecosystem.config.cjs
