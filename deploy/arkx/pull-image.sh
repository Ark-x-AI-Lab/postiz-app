#!/usr/bin/env bash
# Coolify custom build command, from checkout root: bash deploy/arkx/pull-image.sh
set +xv
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

selected_commit=${SOURCE_COMMIT:-}
[[ -z "$selected_commit" || "$selected_commit" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT must be a full lowercase 40-character commit SHA.'
checkout_commit=
if command -v git >/dev/null 2>&1; then
  checkout_commit=$(git rev-parse --verify HEAD 2>/dev/null) || checkout_commit=
fi

read_source_commit_file() {
  # Coolify removes .git before custom builds. Read only the public commit field;
  # never source/evaluate generated files or expose their other runtime values.
  local commit_file=$1 line count=0 value parsed
  local field='^[[:space:]]*(export[[:space:]]+)?SOURCE_COMMIT($|[^A-Za-z0-9_])'
  local assignment='^[[:space:]]*(export[[:space:]]+)?SOURCE_COMMIT[[:space:]]*=(.*)$'
  local bare='^[[:space:]]*([0-9a-f]{40})[[:space:]]*$'
  local double='^[[:space:]]*"([0-9a-f]{40})"[[:space:]]*$'
  local single="^[[:space:]]*'([0-9a-f]{40})'[[:space:]]*$"
  [[ -e "$commit_file" || -L "$commit_file" ]] || return 0
  [[ -f "$commit_file" && ! -L "$commit_file" && -r "$commit_file" ]] || fail 'Generated commit metadata must be a readable regular non-symlink file.'
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ $field ]] || continue
    count=$((count + 1))
    ((count == 1)) || fail 'Duplicate SOURCE_COMMIT in generated commit metadata.'
    [[ "$line" =~ $assignment ]] || fail 'Malformed SOURCE_COMMIT assignment in generated commit metadata.'
    value=${BASH_REMATCH[2]}
    if [[ "$value" =~ $bare || "$value" =~ $double || "$value" =~ $single ]]; then
      parsed=${BASH_REMATCH[1]}
    else
      fail 'Generated SOURCE_COMMIT must be a full lowercase 40-character SHA, optionally enclosed in matching quotes.'
    fi
    [[ -z "$selected_commit" || "$selected_commit" == "$parsed" ]] || fail 'Generated SOURCE_COMMIT disagrees with another selected commit.'
    selected_commit=$parsed
  done < "$commit_file"
}

# Actual Coolify build metadata precedes the build; checkout-root .env may only
# appear afterward. Every available selected value must agree, without merging
# any other field into this process or the application environment.
read_source_commit_file /artifacts/build-time.env
read_source_commit_file .env
[[ -n "$selected_commit" ]] || selected_commit=$checkout_commit
[[ "$selected_commit" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT, generated commit metadata or checkout HEAD must supply a full lowercase 40-character commit SHA.'
if [[ -n "$checkout_commit" && "$checkout_commit" != "$selected_commit" ]]; then
  fail 'SOURCE_COMMIT differs from checkout HEAD; refusing to pull another revision.'
fi
command -v docker >/dev/null 2>&1 || fail 'Docker CLI is required.'
command -v timeout >/dev/null 2>&1 || fail 'timeout with forced termination is required to enforce the ten-minute pull deadline.'

registry_helper=${ARKX_REGISTRY_HELPER_IMAGE:-}
registry_config=${ARKX_REGISTRY_CONFIG_DIR:-}
[[ "$registry_helper" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'ARKX_REGISTRY_HELPER_IMAGE must be a full local immutable image ID.'
[[ "$registry_helper" == sha256:b0cf60e6d6ecc73696811fe105d7b187b0fa387bd8ec62c647177420daf42bad ]] || fail 'Registry helper image is not the reviewed Coolify helper.'
[[ "$registry_config" == /var/lib/arkx-postiz-registry/1alwcyoouzoa5mf1jrnfzm87 ]] || fail 'ARKX_REGISTRY_CONFIG_DIR must be the dedicated candidate registry directory.'

image="ghcr.io/ark-x-ai-lab/postiz-app:sha-$selected_commit"
source_url=https://github.com/Ark-x-AI-Lab/postiz-app
# The caller never reads host credentials or the global Docker login. Main owns
# the dedicated root-0700 host directory and root-0600 GHCR-only config.json.
# These two nonsecret settings must be supplied explicitly by the build command.
umask 077
work_dir=$(mktemp -d)
cleanup() {
  # A timed-out/interrupted docker run can leave its helper alive. Remove only
  # the exact ID Docker wrote for this invocation; never touch the pulled app.
  # Cleanup gets at most two separate 10s checks/actions plus 2s kill grace each
  # (24s total allowance), outside the pull/label deadline. Never retry cleanup.
  if [[ -f "$work_dir/helper.cid" ]]; then
    helper_id=$(cat "$work_dir/helper.cid" 2>/dev/null) || helper_id=
    if [[ "$helper_id" =~ ^[0-9a-f]{64}$ ]]; then
      # --rm normally removed an exited helper already. An empty successful
      # listing proves absence; a failed listing never authorizes removal.
      if ! present=$(timeout -k 2s 10s docker --config "$work_dir/docker" --host unix:///var/run/docker.sock \
        container ls --all --no-trunc --filter "id=$helper_id" --format '{{.ID}}' 2>/dev/null); then
        printf 'Helper presence check failed for %s; inspect privately before retrying.\n' "$helper_id" >&2
      elif [[ -z "$present" ]]; then
        :
      elif [[ "$present" != "$helper_id" ]]; then
        printf 'Helper cleanup refused an unexpected ID; inspect privately before retrying.\n' >&2
      elif ! timeout -k 2s 10s docker --config "$work_dir/docker" --host unix:///var/run/docker.sock \
        container rm --force "$helper_id" >/dev/null 2>&1; then
        printf 'Helper cleanup unconfirmed for %s; inspect privately before retrying.\n' "$helper_id" >&2
      fi
    else
      printf 'Helper cleanup refused an invalid ID; inspect privately before retrying.\n' >&2
    fi
  fi
  rm -rf "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$work_dir/docker"
deadline=$((SECONDS + 600))

while :; do
  remaining=$((deadline - SECONDS))
  ((remaining > 0)) || fail 'Commit image is not published after ten minutes; check the GitHub build and private package readiness.'
  printf 'Pulling %s\n' "$image"
  # Only this pinned helper can read the dedicated host config. Its network is
  # disabled; registry traffic is performed by the host Docker daemon via the
  # socket. --mount refuses a missing source instead of creating a directory.
  if timeout -k 2s "${remaining}s" docker --config "$work_dir/docker" --host unix:///var/run/docker.sock \
    run --rm --pull=never --network none --read-only --cap-drop=ALL \
    --security-opt no-new-privileges --user 0:0 --no-healthcheck --log-driver none \
    --cidfile "$work_dir/helper.cid" \
    --mount "type=bind,src=$registry_config,dst=/registry,readonly" \
    --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock,readonly \
    --entrypoint docker "$registry_helper" --config /registry --host unix:///var/run/docker.sock \
    pull --platform linux/amd64 "$image" >"$work_dir/pull.log" 2>&1; then
    rm -f "$work_dir/helper.cid"
    break
  else
    status=$?
  fi
  ((status != 124 && status != 137)) || fail 'Pull deadline reached (including forced termination); no alternate credentials or source-build fallback was attempted.'
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
      ((remaining > 0)) || fail 'Commit image is not published after ten minutes; check the GitHub build and private package readiness.'
      rm -f "$work_dir/helper.cid"
      delay=10
      ((remaining >= delay)) || delay=$remaining
      printf 'Commit image manifest is not published yet; retrying in %s seconds.\n' "$delay"
      sleep "$delay"
      ;;
    *)
      fail 'Private image pull failed; registry details withheld. Check the dedicated read-only GHCR login, helper/config availability and connectivity. No alternate credentials or source-build fallback is allowed.'
      ;;
  esac
done

remaining=$((deadline - SECONDS))
((remaining > 0)) || fail 'Image pull completed too late to verify its labels within ten minutes.'
labels=$(timeout -k 2s "${remaining}s" docker --config "$work_dir/docker" --host unix:///var/run/docker.sock image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}|{{index .Config.Labels "org.opencontainers.image.source"}}' \
  "$image" 2>"$work_dir/inspect.log") || fail 'Cannot inspect the selected image labels; details withheld.'
[[ "$labels" == "$selected_commit|$source_url" ]] || fail 'Image revision/source labels do not match the selected commit and fork.'
printf 'PASS: private commit image pulled through the dedicated registry config; revision and source labels match %s.\n' "$selected_commit"
