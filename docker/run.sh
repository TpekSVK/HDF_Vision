#!/usr/bin/env bash
set -euo pipefail

GPIO_OUTPUT_PINS=(7 11 12 13 15 16 18 22)
GPIO_INPUT_PINS=(29 31 37 40)
GPIO_BIDIRECTIONAL_PINS=(19 21)

configure_gpio_runtime() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[err] python3 nebol nájdený. Konfiguráciu pinov preskakujem." >&2
    return 1
  fi

  python3 - <<'PY'
import sys

try:
    import Jetson.GPIO as GPIO
except ModuleNotFoundError:  # pragma: no cover - iba na Jetson zariadení
    sys.stderr.write("[err] Modul Jetson.GPIO nie je dostupný.\n")
    sys.stderr.write("[hint] Spusti konfiguráciu priamo na Jetson Orin Nano.\n")
    sys.exit(1)

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)

outputs = (7, 11, 12, 13, 15, 16, 18, 22)
inputs = (29, 31, 37, 40)
bidirectional = (19, 21)

for pin in outputs:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    print(f"[cfg] Pin {pin}: nastavený ako výstup (LOW)")

for pin in inputs:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print(f"[cfg] Pin {pin}: nastavený ako vstup s pulldown")

for pin in bidirectional:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print(f"[cfg] Pin {pin}: pripravený ako obojsmerný (štart ako vstup)")

print("[cfg] GPIO konfigurácia dokončená prostredníctvom Jetson.GPIO.")
PY
}

if [[ $# -gt 0 && $1 == "configure-gpio" ]]; then
  configure_gpio_runtime
  exit $?
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

# Vyber device (ak chceš fixne video1, exportuj CAM_DEV=/dev/video1)
CAM_DEV="${CAM_DEV:-/dev/video0}"

# Spustenie (povolené V4L2 ioctl bez full privileged)
exec docker run --rm -it \
  --privileged \
  --runtime nvidia \
  --network host \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  --cap-add SYS_ADMIN \
  --device ${CAM_DEV}:${CAM_DEV} \
  --device-cgroup-rule='c 81:* rmw' \
  --env CAM_DEV="${CAM_DEV}" \
  --env DISPLAY="${DISPLAY:-:0}" \
  --env QT_X11_NO_MITSHM=1 \
  --env QT_QPA_PLATFORM=xcb \
  --env PYTHONFAULTHANDLER=1 \
  --env OPENCV_LOG_LEVEL=INFO \
  --ulimit core=-1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --group-add video \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /data:/data \
  -v "$(pwd)/app":/workspace/app \
  -v "$(pwd)/data":/workspace/data \
  -w /workspace \
  "${IMAGE_NAME}" \
  bash -lc 'echo "[diag] whoami=$(whoami)"; id; ls -l /dev/video* 2>/dev/null || true; python3 -m app.main'
