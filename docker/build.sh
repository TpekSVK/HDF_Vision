#!/usr/bin/env bash
set -euo pipefail
IMAGE_NAME=hdf_vision:dev
DOCKER_BUILDKIT=1 docker build -f docker/Dockerfile -t ${IMAGE_NAME} .
echo "Built ${IMAGE_NAME}"
