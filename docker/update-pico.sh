#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${IMAGE_NAME:-hdf_vision:dev}"
case "${1:-}" in
  --help|-h)
    cat <<'EOF'
Použitie: bash docker/update-pico.sh [--check] [--port /dev/ttyACM0]

Porovná Pico s firmware/pico/main.py v lokálnom projekte. Starší HDF firmvér
zálohuje a aktualizuje; rovnakú alebo novšiu verziu nemení.
--check: iba kontrola, bez nahrávania a reštartu.
--port: výber konkrétneho Pico; pri viacerých zariadeniach je povinný.

Pred spustením zastavte výrobu a zatvorte HDF Vision aj sériové terminály.
Zálohy: artifacts/pico-backups/ (mimo Gitu).
Aktualizuje HDF main.py, nie MicroPython/UF2. Vyžaduje existujúci Docker image.
EOF
    exit 0 ;;
esac
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "Chýba obraz $IMAGE_NAME. Spustite bash docker/build.sh." >&2
  exit 1
fi
mkdir -p "$ROOT_DIR/artifacts/pico-backups"
# Host PID visibility allows checking non-cooperating serial clients as well.
exec docker run --rm --network none --privileged --pid=host \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUNBUFFERED=1 \
  -v /dev:/dev \
  -v "$ROOT_DIR:/workspace:ro" \
  -v "$ROOT_DIR/artifacts/pico-backups:/backups:rw" \
  -w /workspace --entrypoint python3 "$IMAGE_NAME" tools/update_pico.py "$@"
