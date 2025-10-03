# Jetson Orin Nano (L4T) – JetPack 6.2
FROM nvcr.io/nvidia/l4t-jetpack:r36.4.0

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Základ + GStreamer + V4L2 + OpenCV + Qt/X11/XCB runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-opencv \
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    v4l-utils libglib2.0-0 libatlas-base-dev \
    libjpeg-dev libwebp-dev libsqlite3-0 \
    # Qt/X11/XCB runtime
    libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 \
    libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 \
    libxcb-xinerama0 libxkbcommon0 libxkbcommon-x11-0 libxext6 libsm6 \
    libxrender1 libxi6 libxfixes3 libfontconfig1 libfreetype6 libdbus-1-3 \
    libv4l-0 v4l-utils \
    # OpenGL (Qt často potrebuje)
    libgl1 libglx-mesa0 libglu1-mesa libopengl0 \
    fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# PIP (PySide6, numpy, atď.)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# App
WORKDIR /workspace
COPY app /workspace/app
COPY data /workspace/data
COPY README.md /workspace/README.md

# Datový mount (na Jetson pripojíme host /data do /data)
VOLUME ["/data"]

# Predvolené env pre X11 (zabraňuje MIT-SHM chybám v Docker/X11)
ENV QT_X11_NO_MITSHM=1

# Spúšťaj ako modul, aby fungovali package importy
CMD ["python3", "-m", "app.main"]
