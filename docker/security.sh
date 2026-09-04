#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-hdf_vision:dev}"
COMMAND="${1:---help}"

case "${COMMAND}" in
  --help|-h|help)
    cat <<'EOF'
Použitie:
  bash docker/security.sh set-password
  bash docker/security.sh change-password
  bash docker/security.sh verify
  bash docker/security.sh status
  bash docker/security.sh remove-password
  sudo bash docker/security.sh reset-password
EOF
    exit 0 ;;
  set-password|change-password|verify|status|remove-password) ;;
  reset-password)
    if [[ "$(id -u)" -ne 0 ]]; then
      echo "ERROR: reset-password requires root privileges." >&2
      echo "Run: sudo bash docker/security.sh reset-password" >&2
      exit 1
    fi ;;
  *)
    echo "Neznámy príkaz: ${COMMAND}" >&2
    "$0" --help >&2
    exit 2 ;;
esac

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "ERROR: Docker image '${IMAGE_NAME}' nebol nájdený. Spusťte bash docker/build.sh" >&2
  exit 1
fi

exec docker run --rm -it \
  -v /data:/data \
  -v "$(pwd)/app:/workspace/app:ro" \
  -w /workspace \
  "${IMAGE_NAME}" python3 -m app.tools.security_cli "${COMMAND}"
