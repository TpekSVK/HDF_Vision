# Jetson Orin Nano (L4T) – JetPack 6.2
FROM nvcr.io/nvidia/l4t-jetpack:r36.4.0

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ZÁKLAD: Python, GStreamer, Qt/X11, V4L2, atď. (bez python3-opencv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    v4l-utils libglib2.0-0 python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    libx11-6 libxext6 libxrender1 libxfixes3 libxi6 libxrandr2 \
    libgl1 libegl1 libgbm1 \
    libxkbcommon0 libxkbcommon-x11-0 \
    libx11-xcb1 libsm6 libice6 \
    libxcb1 libxcb-shm0 libxcb-randr0 libxcb-shape0 libxcb-xkb1 libxcb-sync1 \
    libxcb-render0 libxcb-render-util0 libxcb-glx0 libxcb-util1 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-xfixes0 libxcb-xinerama0 libxcb-xinput0 \
    libfontconfig1 libfreetype6 libdbus-1-3 \
    libv4l-0 v4l-utils \
    libjpeg-dev libwebp-dev libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && pip3 uninstall -y opencv-python opencv-python-headless opencv-contrib-python || true
# ^ tu bol problém: chýbala medzera alebo nový riadok pred komentárom

WORKDIR /workspace

# --- OpenCV (CUDA) build on Jetson ---
ARG OPENCV_VER=4.10.0
ARG OPENCV_CONTRIB=ON
ARG CUDA_ARCH_BIN=8.7

# (voliteľné) menej pamäťovo náročné knižnice
ENV CMAKE_BUILD_PARALLEL_LEVEL=2

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config \
    libgtk2.0-dev libcanberra-gtk3-module libgtk-3-dev \
    libavcodec-dev libavformat-dev libswscale-dev libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libjpeg-dev libpng-dev libtiff-dev libwebp-dev libopenexr-dev \
    python3-dev python3-numpy \
    libtbb-dev libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/opencv-build
RUN git clone --branch ${OPENCV_VER} --depth 1 https://github.com/opencv/opencv.git
RUN if [ "${OPENCV_CONTRIB}" = "ON" ]; then \
      git clone --branch ${OPENCV_VER} --depth 1 https://github.com/opencv/opencv_contrib.git; \
    fi

RUN mkdir -p build && cd build && cmake \
  -D CMAKE_BUILD_TYPE=Release \
  -D CMAKE_INSTALL_PREFIX=/usr/local \
  -D WITH_CUDA=ON \
  -D CUDA_ARCH_BIN=${CUDA_ARCH_BIN} \
  -D WITH_CUBLAS=ON \
  -D WITH_CUDNN=ON \
  -D OPENCV_DNN_CUDA=OFF \
  -D ENABLE_FAST_MATH=ON \
  -D CUDA_FAST_MATH=ON \
  -D WITH_GSTREAMER=ON \
  -D WITH_V4L=ON \
  -D BUILD_opencv_world=OFF \
  -D BUILD_TESTS=OFF \
  -D BUILD_PERF_TESTS=OFF \
  -D BUILD_EXAMPLES=OFF \
  -D BUILD_opencv_python2=OFF \
  -D BUILD_opencv_python3=ON \
  -D BUILD_LIST=core,imgproc,imgcodecs,highgui,videoio,video,features2d,calib3d,python3,cudev,cudaarithm,cudaimgproc,cudafilters,cudawarping \
  -D CMAKE_CUDA_ARCHITECTURES=87 \
  -D OPENCV_GENERATE_PKGCONFIG=ON \
  -D WITH_OPENCL=OFF \
 $(if [ "${OPENCV_CONTRIB}" = "ON" ]; then echo "-D OPENCV_EXTRA_MODULES_PATH=/tmp/opencv-build/opencv_contrib/modules"; fi) \
  ../opencv && \
  make -j2 && make install && ldconfig && \
  cd / && rm -rf /tmp/opencv-build

COPY app /workspace/app
COPY data /workspace/data
COPY README.md /workspace/README.md

VOLUME ["/data"]
ENV QT_X11_NO_MITSHM=1
CMD ["python3", "-m", "app.main"]
