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
  --ulimit core=-1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --group-add video \
  --device /dev/video0:/dev/video0 \
  --device /dev/video1:/dev/video1 \
  -v /data:/data \
  -v $(pwd)/app:/workspace/app \
  -v $(pwd)/data:/workspace/data \
  hdf_vision:dev

