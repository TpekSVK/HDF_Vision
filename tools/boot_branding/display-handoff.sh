#!/bin/sh
# Called by GDM before starting X. The NVIDIA DRM module used for the boot
# cover must release modesetting ownership, as verified on this Orin Nano.
/usr/bin/timeout 8 /usr/bin/plymouth quit --retain-splash || true
if /usr/bin/grep -q '^nvidia_drm ' /proc/modules; then
    if ! /usr/bin/timeout 8 /sbin/modprobe -r nvidia_drm; then
        /usr/bin/logger -t hdf-display-handoff 'nvidia_drm could not be released before GDM; no forced unload attempted'
    fi
fi
exit 0
