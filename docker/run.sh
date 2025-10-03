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
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v /dev:/dev \
  --device-cgroup-rule='c 81:* rmw' \
  --device-cgroup-rule='c 189:* rmw' \
  -v /data:/data \
  -v $(pwd)/app:/workspace/app \
  -v $(pwd)/data:/workspace/data \
  hdf_vision:dev
