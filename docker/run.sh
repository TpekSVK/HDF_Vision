#!/usr/bin/env bash
set -euo pipefail

GPIO_BASE_ADDR=${GPIO_BASE_ADDR:-0xFE200000}
GPIO_OUTPUT_PINS=(7 11 12 13 15 16 18 22)
GPIO_INPUT_PINS=(29 31 33 37)
GPIO_BIDIRECTIONAL_PINS=(19 21)
declare -A GPIO_BOARD_TO_BCM=(
  [7]=4
  [8]=14
  [10]=15
  [11]=17
  [12]=18
  [13]=27
  [15]=22
  [16]=23
  [18]=24
  [19]=10
  [21]=9
  [22]=25
  [23]=11
  [24]=8
  [26]=7
  [29]=5
  [31]=6
  [32]=12
  [33]=13
  [35]=19
  [36]=16
  [37]=26
  [38]=20
  [40]=21
)
DEVMEM_CMD=()

_configure_gpfsel() {
  local bcm_pin=$1
  local mode=$2
  local label=$3

  local base_dec=$((GPIO_BASE_ADDR))
  local reg_index=$((bcm_pin / 10))
  local shift=$(((bcm_pin % 10) * 3))
  local reg_addr_dec=$((base_dec + reg_index * 4))
  local reg_addr
  printf -v reg_addr "0x%X" "${reg_addr_dec}"

  local current_hex
  current_hex=$("${DEVMEM_CMD[@]}" "${reg_addr}" 32)
  local current_dec=$((current_hex))
  local mask=$((7 << shift))
  local value_bits=$((mode << shift))
  local new_value=$(((current_dec & ~mask) | value_bits))
  local new_hex
  printf -v new_hex "0x%08X" "${new_value}"

  echo "[cfg] ${label}: bcm${bcm_pin} -> mode ${mode} (reg ${reg_addr})"
  "${DEVMEM_CMD[@]}" "${reg_addr}" 32 "${new_hex}"
}

configure_gpio_devmem() {
  if command -v devmem >/dev/null 2>&1; then
    DEVMEM_CMD=(devmem)
  elif command -v busybox >/dev/null 2>&1; then
    DEVMEM_CMD=(busybox devmem)
  else
    echo "[err] Nástroj devmem nebol nájdený." >&2
    echo "[hint] Na Jetson zariadení nainštaluj balík busybox alebo util-linux." >&2
    return 1
  fi

  echo "[cfg] GPIO_BASE_ADDR=${GPIO_BASE_ADDR}"

  local pin
  for pin in "${GPIO_OUTPUT_PINS[@]}"; do
    local bcm=${GPIO_BOARD_TO_BCM[${pin}]}
    if [[ -z "${bcm}" ]]; then
      echo "[warn] Chýba mapovanie pre pin ${pin}, preskakujem." >&2
      continue
    fi
    _configure_gpfsel "${bcm}" 1 "Pin ${pin} (výstup)"
  done

  for pin in "${GPIO_INPUT_PINS[@]}"; do
    local bcm=${GPIO_BOARD_TO_BCM[${pin}]}
    if [[ -z "${bcm}" ]]; then
      echo "[warn] Chýba mapovanie pre pin ${pin}, preskakujem." >&2
      continue
    fi
    _configure_gpfsel "${bcm}" 0 "Pin ${pin} (vstup)"
  done

  for pin in "${GPIO_BIDIRECTIONAL_PINS[@]}"; do
    local bcm=${GPIO_BOARD_TO_BCM[${pin}]}
    if [[ -z "${bcm}" ]]; then
      echo "[warn] Chýba mapovanie pre pin ${pin}, preskakujem." >&2
      continue
    fi
    _configure_gpfsel "${bcm}" 0 "Pin ${pin} (obojsmerný – predvolene vstup)"
  done

  echo "[cfg] Konfigurácia GPIO pomocou devmem dokončená."
}

if [[ $# -gt 0 && $1 == "configure-gpio" ]]; then
  configure_gpio_devmem
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
