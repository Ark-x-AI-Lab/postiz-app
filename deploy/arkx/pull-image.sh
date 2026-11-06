#!/usr/bin/env bash
# Coolify custom build command, from checkout root: bash deploy/arkx/pull-image.sh
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

selected_commit=${SOURCE_COMMIT:-}
checkout_commit=
if command -v git >/dev/null 2>&1; then
  checkout_commit=$(git rev-parse --verify HEAD 2>/dev/null) || checkout_commit=
fi
[[ -n "$selected_commit" ]] || selected_commit=$checkout_commit
[[ "$selected_commit" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT or checkout HEAD must be a full lowercase 40-character commit SHA.'
if [[ -n "$checkout_commit" && "$checkout_commit" != "$selected_commit" ]]; then
  fail 'SOURCE_COMMIT differs from checkout HEAD; refusing to pull another revision.'
fi
command -v docker >/dev/null 2>&1 || fail 'Docker CLI is required.'
command -v timeout >/dev/null 2>&1 || fail 'GNU timeout is required to enforce the ten-minute pull deadline.'

image="ghcr.io/ark-x-ai-lab/postiz-app:sha-$selected_commit"
source_url=https://github.com/Ark-x-AI-Lab/postiz-app
# An empty temporary config enforces public access without reading saved logins.
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
mkdir "$work_dir/docker"
deadline=$((SECONDS + 600))

while :; do
  remaining=$((deadline - SECONDS))
  ((remaining > 0)) || fail 'Image is not ready after ten minutes. Check the GitHub build and package public visibility; a private package cannot be pulled here.'
  printf 'Pulling %s\n' "$image"
  if timeout "${remaining}s" docker --config "$work_dir/docker" pull --platform linux/amd64 "$image" >"$work_dir/pull.log" 2>&1; then
    break
  else
    status=$?
  fi
  ((status != 124)) || fail 'Pull exceeded the ten-minute deadline; check the registry or network. No build or authenticated fallback was attempted.'
  # Retry only known manifest-not-found responses. Authentication, TLS, network,
  # and unrecognized errors are terminal, even if they also mention a manifest.
  message=$(cat "$work_dir/pull.log")
  message=${message#Error response from daemon: }
  case "$message" in
    'manifest unknown'|'manifest unknown: manifest unknown'|\
    "manifest for $image not found: manifest unknown"|\
    "manifest for $image not found: manifest unknown: manifest unknown"|\
    "failed to resolve reference \"$image\": $image: not found")
      remaining=$((deadline - SECONDS))
      ((remaining > 0)) || fail 'Image is not ready after ten minutes. Check the GitHub build and package public visibility; a private package cannot be pulled here.'
      delay=10
      ((remaining >= delay)) || delay=$remaining
      printf 'Commit image manifest is not published yet; retrying in %s seconds.\n' "$delay"
      sleep "$delay"
      ;;
    *)
      cat "$work_dir/pull.log" >&2
      fail 'Public image pull failed. Check package readiness/public visibility and registry connectivity. A private package is not ready for this pipeline; no login or source-build fallback is allowed.'
      ;;
  esac
done

remaining=$((deadline - SECONDS))
((remaining > 0)) || fail 'Image pull completed too late to verify its labels within ten minutes.'
labels=$(timeout "${remaining}s" docker --config "$work_dir/docker" image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}|{{index .Config.Labels "org.opencontainers.image.source"}}' \
  "$image") || fail 'Cannot inspect the selected image labels.'
[[ "$labels" == "$selected_commit|$source_url" ]] || fail 'Image revision/source labels do not match the selected commit and fork.'
printf 'PASS: public commit image pulled; revision and source labels match %s.\n' "$selected_commit"
