#!/usr/bin/env bash
set -euo pipefail

# --- GPIO mapa podľa požiadavky ---
# gpio = {
#   "output": (7, 11, 12, 13, 15, 16, 18, 22),
#   "input": (29, 31, 37, 40),
#   "bidirectional": (19, 21),
# }

configure_gpio_runtime() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[warn] python3 nebol nájdený na hoste – preskakujem GPIO init." >&2
    return 0
  fi

  # Pozn.: nastavuje len runtime smer. Ak pinmux nedovolí, uvidíš warning z Jetson.GPIO.
  python3 - <<'PY'
import sys
try:
    import Jetson.GPIO as GPIO
except Exception as e:
    sys.stderr.write(f"[warn] Jetson.GPIO nie je dostupný: {e}\n")
    sys.exit(0)

gpio = {
    "output": (7, 11, 12, 13, 15, 16, 18, 22),
    "input": (29, 31, 37, 40),
    "bidirectional": (19, 21),
}

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)

# Výstupy – inicializuj na LOW
for pin in gpio["output"]:
    try:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        print(f"[cfg] BOARD {pin}: OUTPUT (LOW)")
    except Exception as e:
        print(f"[warn] BOARD {pin}: OUTPUT zlyhalo: {e}")

# Vstupy – PULL-DOWN (neplávajú)
for pin in gpio["input"]:
    try:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        print(f"[cfg] BOARD {pin}: INPUT (PULL-DOWN)")
    except Exception as e:
        print(f"[warn] BOARD {pin}: INPUT zlyhalo: {e}")

# Bidirectional – štart ako INPUT (PULL-DOWN); app ich vie prepnúť na OUT
for pin in gpio["bidirectional"]:
    try:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        print(f"[cfg] BOARD {pin}: BIDIRECTIONAL (start INPUT, PULL-DOWN)")
    except Exception as e:
        print(f"[warn] BOARD {pin}: BIDIR zlyhalo: {e}")

print("[cfg] GPIO runtime konfigurácia dokončená.")
PY
}

# Samostatné spustenie len na GPIO konfiguráciu:
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

# --- Spusti GPIO init na hoste ešte pred kontajnerom ---
configure_gpio_runtime || true

# Spustenie (GUI + UVC)
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
