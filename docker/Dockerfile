# Jetson Orin Nano (L4T) – JetPack 6.2 základ
FROM nvcr.io/nvidia/l4t-jetpack:r36.2.0

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Základné nástroje + GStreamer + V4L2 + OpenCV (APT balík pre aarch64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-opencv \
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    v4l-utils libglib2.0-0 libatlas-base-dev \
    libjpeg-dev libwebp-dev libsqlite3-0 \
    qtbase5-dev qtchooser qt5-qmake qtbase5-dev-tools \
    && rm -rf /var/lib/apt/lists/*

# PIP (PySide6, numpy atď.)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# App
WORKDIR /workspace
COPY app /workspace/app
COPY data /workspace/data
COPY README.md /workspace/README.md

# Datový mount (na Jetson pripojíme host /data do /data)
VOLUME ["/data"]

# Predvolené env pre X11 (voliteľné)
ENV QT_X11_NO_MITSHM=1

CMD ["python3", "-m", "app.main"]
