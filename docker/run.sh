#!/usr/bin/env bash
set -euo pipefail

ADMIN_MODE=0
if [[ "${1:-}" == "--admin" ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: --admin requires root privileges." >&2
    echo "Run: sudo bash docker/run.sh --admin" >&2
    exit 1
  fi
  ADMIN_MODE=1
  shift
fi
if [[ $# -gt 0 ]]; then
  echo "ERROR: Unknown argument: $1" >&2
  exit 2
fi

IMAGE_NAME="${IMAGE_NAME:-hdf_vision:dev}"
echo "[diag] IMAGE_NAME=${IMAGE_NAME}"

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "[err] Docker image '${IMAGE_NAME}' nenájdený. Spusť najprv: bash docker/build.sh"
  exit 1
fi

# X11 (GUI)
xhost +local:root >/dev/null 2>&1 || true
echo "[diag] DISPLAY=${DISPLAY:-<unset>}"
echo "[diag] /dev/video* na hostovi:"; ls -l /dev/video* 2>/dev/null || true
echo "[diag] /dev/hidraw* na hostovi:"; ls -l /dev/hidraw* 2>/dev/null || true

# Zariadenia sa priraďujú v aplikácii podľa USB serial, nie podľa videoN.

# Spustenie (GUI + UVC)
set +e
docker run --rm -it \
  --privileged \
  --runtime nvidia \
  --network host \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  --cap-add SYS_ADMIN \
  --device-cgroup-rule='c 81:* rmw' \
  --device-cgroup-rule='c 238:* rmw' \
  --env DISPLAY="${DISPLAY:-:0}" \
  --env QT_X11_NO_MITSHM=1 \
  --env QT_QPA_PLATFORM=xcb \
  --env PYTHONFAULTHANDLER=1 \
  --env OPENCV_LOG_LEVEL=INFO \
  --env HDF_ADMIN_MODE="${ADMIN_MODE}" \
  --ulimit core=-1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --group-add video \
  -v /usr/bin/tegrastats:/usr/bin/tegrastats:ro \
  -v /dev:/dev \
  -v /sys:/sys:ro \
  -v /data:/data \
  -v "$(pwd)/app":/workspace/app \
  -v "$(pwd)/data":/workspace/data \
  -w /workspace \
  "${IMAGE_NAME}" \
  bash -lc 'echo "[diag] whoami=$(whoami)"; id; ls -l /dev/video* 2>/dev/null || true; ls -l /dev/hidraw* 2>/dev/null || true; python3 -m app.main'
APP_RC=$?
set -e

case "${APP_RC}" in
  10)
    echo "[host] App requested host shutdown."
    shutdown -h now
    ;;
  11)
    echo "[host] App requested host reboot."
    shutdown -r now
    ;;
  *)
    exit "${APP_RC}"
    ;;
esac
