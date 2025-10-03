#!/usr/bin/env bash
set -euo pipefail
IMAGE_NAME=hdf_vision:dev

# povolíme X11 (ak chceš SETUP s oknom)
xhost +local:root >/dev/null 2>&1 || true

docker run --rm -it \
  --runtime nvidia \
  --network host \
  --env DISPLAY=$DISPLAY \
  --env QT_X11_NO_MITSHM=1 \
  --env PYTHONFAULTHANDLER=1 \
  --env OPENCV_LOG_LEVEL=INFO \
  --env QT_QPA_PLATFORM=xcb \
  --env QT_DEBUG_PLUGINS=0 \

  --ulimit core=-1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --group-add video \
  -v /dev:/dev \
  -v /data:/data \
  -v $(pwd)/app:/workspace/app \
  -v $(pwd)/data:/workspace/data \
  -w /workspace \
  ${IMAGE_NAME} \
  bash -lc 'echo "[diag] whoami=$(whoami)"; id; ls -l /dev/video* 2>/dev/null || true; v4l2-ctl --list-devices 2>/dev/null || true; python3 -m app.main'
