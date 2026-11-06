#!/bin/sh
# This is an Ark-x wrapper guard, not a Postiz environment flag.
set -eu
if [ "${ARKX_RUNTIME-}" != production ]; then
  echo 'Refusing Postiz startup: ARKX_RUNTIME must equal production.' >&2
  exit 64
fi
# Preserve Dockerfile.dev's command only after the guard passes.
exec sh -c 'nginx && pnpm run pm2'
