#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-hdf_vision:dev}"
COMMAND="${1:---help}"
APP_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../app" && pwd)"

case "${COMMAND}" in
  --help|-h|help)
    echo 'Použitie: bash docker/contact.sh set-info'
    exit 0 ;;
  set-info) ;;
  *)
    echo "Neznámy príkaz: ${COMMAND}" >&2
    echo 'Použitie: bash docker/contact.sh set-info' >&2
    exit 2 ;;
esac

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "ERROR: Docker image '${IMAGE_NAME}' nebol nájdený. Spusťte bash docker/build.sh" >&2
  exit 1
fi

exec docker run --rm -it \
  -v /data:/data \
  -v "${APP_PATH}:/workspace/app:ro" \
  -w /workspace \
  "${IMAGE_NAME}" python3 -m app.tools.contact_cli "${COMMAND}"
