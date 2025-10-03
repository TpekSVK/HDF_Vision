# Jetson Orin Nano (L4T) – JetPack 6.2
FROM nvcr.io/nvidia/l4t-jetpack:r36.4.0

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-opencv \
    # GStreamer runtime + Python väzba
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    v4l-utils libglib2.0-0 python3-gi gir1.2-gst-1.0 \
    # Qt XCB závislosti (PySide6 potrebuje)
    libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \
    libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xfixes0 libxcb-xinerama0 libxcb-xinput0 \
    # ostatné
    libatlas-base-dev libjpeg-dev libwebp-dev libsqlite3-0 \
    qtbase5-dev qtchooser qt5-qmake qtbase5-dev-tools \
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
