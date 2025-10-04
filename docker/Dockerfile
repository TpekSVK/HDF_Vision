# Jetson Orin Nano (L4T) – JetPack 6.2
FROM nvcr.io/nvidia/l4t-jetpack:r36.4.4

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-opencv \
    # GStreamer + Python väzby
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    v4l-utils libglib2.0-0 python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    # Qt/X11/xcb/GL runtime deps pre PySide6 (Qt6)
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
    # ostatné
    libjpeg-dev libwebp-dev libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && pip3 uninstall -y opencv-python opencv-python-headless opencv-contrib-python || true# App
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
