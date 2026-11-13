# Sourced by /etc/X11/Xsession before GNOME starts. Never exit the session.
if [ -n "${DISPLAY:-}" ] && [ -r /usr/share/backgrounds/hdf-vision.png ]; then
    if /usr/bin/timeout 2 /usr/bin/xsetbg -quiet -border '#14181d' -center /usr/share/backgrounds/hdf-vision.png >/dev/null 2>&1; then
        /usr/bin/timeout 1 /usr/bin/logger -t hdf-x-background 'Xsession root logo painted' || true
    else
        /usr/bin/timeout 2 /usr/bin/xsetroot -solid '#14181d' >/dev/null 2>&1 || true
        /usr/bin/timeout 1 /usr/bin/logger -t hdf-x-background 'Xsession logo failed; solid background attempted' || true
    fi
fi
