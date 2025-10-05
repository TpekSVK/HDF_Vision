#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${IMAGE_NAME:-hdf_vision:dev}
DOCKERFILE=${DOCKERFILE:-docker/Dockerfile}
CONTEXT_DIR=${CONTEXT_DIR:-.}
CACHE_DIR=${CACHE_DIR:-.docker-cache}
PLATFORM=${PLATFORM:-linux/arm64}   # Jetson = arm64

# flagy: --push, --no-load, --no-cache, --platform=linux/arm64,linux/amd64
PUSH=0
LOAD=1
NO_CACHE=0

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1; LOAD=0 ;;
    --no-load) LOAD=0 ;;
    --no-cache) NO_CACHE=1 ;;
    --platform=*) PLATFORM="${arg#*=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

export DOCKER_BUILDKIT=1

# builder (len raz sa vytvorí, potom sa používa)
if ! docker buildx inspect hdfbuilder >/dev/null 2>&1; then
  docker buildx create --use --name hdfbuilder >/dev/null
else
  docker buildx use hdfbuilder >/dev/null
fi

# lokálny cache adresár
mkdir -p "$CACHE_DIR"

# poskladaj príkaz
cmd=(docker buildx build
  -f "$DOCKERFILE"
  -t "$IMAGE_NAME"
  --platform "$PLATFORM"
  --cache-from=type=local,src="$CACHE_DIR"
  --cache-to=type=local,dest="$CACHE_DIR",mode=max
)

# voľby
if [[ $NO_CACHE -eq 1 ]]; then
  cmd+=(--no-cache)
fi
if [[ $PUSH -eq 1 ]]; then
  cmd+=(--push)
elif [[ $LOAD -eq 1 ]]; then
  cmd+=(--load)   # načíta image do lokálneho docker daemona
fi

# kontext
cmd+=("$CONTEXT_DIR")

echo "+ ${cmd[*]}"
"${cmd[@]}"

echo "Built $IMAGE_NAME"
