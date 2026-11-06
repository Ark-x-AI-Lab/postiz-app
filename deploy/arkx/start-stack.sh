#!/usr/bin/env bash
# Coolify custom start command. No production profile is activated here.
set +xv
set -euo pipefail
compose_action=(up -d)
if (($#)); then
  [[ $# == 1 && "$1" == --activate-publisher ]] || { echo 'Unsupported startup selection.' >&2; exit 1; }
  [[ "${ARKX_RUNTIME:-}" == production ]] || { echo 'Explicit production runtime is required for activation.' >&2; exit 1; }
  compose_action=(--profile production up -d)
fi
commit=${SOURCE_COMMIT:-}
if [[ -z "$commit" && -f .env ]]; then
  count=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == SOURCE_COMMIT=* ]]; then
      count=$((count + 1))
      commit=${line#SOURCE_COMMIT=}
      if [[ "$commit" == \"*\" || "$commit" == \'*\' ]]; then commit=${commit:1:${#commit}-2}; fi
    fi
  done < .env
  [[ "$count" == 1 ]] || { echo 'Missing or duplicate deployment commit.' >&2; exit 1; }
fi
[[ "$commit" =~ ^[a-f0-9]{40}$ ]] || { echo 'Missing full deployment commit.' >&2; exit 1; }
image="ghcr.io/ark-x-ai-lab/postiz-app:sha-$commit"
labels=$(timeout --kill-after=2s 15s docker --host unix:///var/run/docker.sock image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}|{{index .Config.Labels "org.opencontainers.image.source"}}' "$image" 2>/dev/null)
[[ "$labels" == "$commit|https://github.com/Ark-x-AI-Lab/postiz-app" ]] || { echo 'Deployment image identity mismatch.' >&2; exit 1; }
[[ -f deploy/arkx/compose.production.yaml && -f deploy/arkx/prepare-compose.cjs && -f .env ]] || { echo 'Deployment configuration missing.' >&2; exit 1; }
umask 077
scratch=$(mktemp -d)
cleanup() {
  if [[ -f "$scratch/prepare.cid" ]]; then
    local id
    id=$(cat "$scratch/prepare.cid")
    if [[ "$id" =~ ^[a-f0-9]{64}$ ]]; then
      local present
      if present=$(timeout --kill-after=2s 10s docker --host unix:///var/run/docker.sock container ls -aq --no-trunc --filter "id=$id" 2>/dev/null); then
        if [[ "$present" == "$id" ]]; then
          timeout --kill-after=2s 10s docker --host unix:///var/run/docker.sock container rm --force "$id" >/dev/null 2>&1 || echo 'Compose preparation cleanup unconfirmed; inspect before retry.' >&2
        fi
      else
        echo 'Compose preparation state unknown; inspect before retry.' >&2
      fi
    fi
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
program=$(cat deploy/arkx/prepare-compose.cjs)
# Prepared JSON streams directly into Compose; no credential-bearing output or new file.
timeout --kill-after=5s 60s docker --host unix:///var/run/docker.sock run --rm -i --pull=never --network none \
  --read-only --cap-drop=ALL --security-opt no-new-privileges --user 65534:65534 \
  --no-healthcheck --log-driver none --cidfile "$scratch/prepare.cid" --entrypoint node "$image" \
  -e "$program" -- --arkx-compose-stdin < deploy/arkx/compose.production.yaml |
  COMPOSE_PROFILES= timeout --kill-after=5s 600s docker --host unix:///var/run/docker.sock compose --project-directory . --env-file .env -f - "${compose_action[@]}"
