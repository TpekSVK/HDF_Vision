#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${1:-hdf_vision:dev}"

# Diagnostika
echo "[diag] IMAGE_NAME=${IMAGE_NAME}"

# Over, že image existuje
if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "[err] Docker image '${IMAGE_NAME}' nenájdený. Spusť najprv: bash docker/build.sh"
  exit 1
fi

# X11 pre GUI (bezpečne ignoruj chybu)
xhost +local:root >/dev/null 2>&1 || true

# Overenia: DISPLAY a /dev/video*
echo "[diag] DISPLAY=${DISPLAY:-<unset>}"
echo "[diag] /dev/video* na hostovi:"
ls -l /dev/video* 2>/dev/null || true

# Spustenie kontajnera
exec docker run --rm -it \
  --runtime nvidia \
  --network host \
  --env DISPLAY="${DISPLAY:-:0}" \
  --env QT_X11_NO_MITSHM=1 \
  --env QT_QPA_PLATFORM=xcb \
  --env QT_DEBUG_PLUGINS=1 \

  --env PYTHONFAULTHANDLER=1 \
  --env OPENCV_LOG_LEVEL=INFO \
  --ulimit core=-1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --group-add video \
  -v /dev:/dev \
  -v /data:/data \
  -v "$(pwd)/app":/workspace/app \
  -v "$(pwd)/data":/workspace/data \
  -w /workspace \
  "${IMAGE_NAME}" \
  bash -lc 'echo "[diag] whoami=$(whoami)"; id; ls -l /dev/video* 2>/dev/null || true; python3 -m app.main'
