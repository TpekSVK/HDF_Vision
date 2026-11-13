# Sourced by /etc/X11/Xsession before GNOME starts. Never exit the session.
if [ -n "${DISPLAY:-}" ] && [ -r /usr/share/backgrounds/hdf-vision.png ]; then
    /usr/bin/timeout 2 /usr/bin/xsetbg -quiet -border '#14181d' -center /usr/share/backgrounds/hdf-vision.png >/dev/null 2>&1 ||
        /usr/bin/timeout 2 /usr/bin/xsetroot -solid '#14181d' >/dev/null 2>&1 || true
fi
