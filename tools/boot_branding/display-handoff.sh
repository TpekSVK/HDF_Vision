#!/bin/sh
# Arm a one-boot fallback before touching the graphical handoff.
/usr/bin/systemd-run --quiet --collect --unit=hdf-display-guard --on-active=25s --timer-property=AccuracySec=1s /usr/bin/python3 /usr/local/libexec/hdf-vision-display-guard || true
/usr/bin/timeout 8 /usr/bin/plymouth quit --retain-splash || true
# A quit acknowledgement can precede final renderer cleanup. Kill only the
# known Plymouth service's remaining processes, without a blocking stop job
# inside GDM's ExecStartPre ordering dependency.
/usr/bin/systemctl kill --kill-who=all --signal=TERM plymouth-start.service 2>/dev/null || true
attempt=0
while /usr/bin/grep -q '^nvidia_drm ' /proc/modules; do
    if /usr/bin/timeout 1 /sbin/modprobe -r nvidia_drm 2>/dev/null; then
        /usr/bin/logger -t hdf-display-handoff 'Plymouth display released before GDM'
        exit 0
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 10 ]; then
        /usr/bin/logger -t hdf-display-handoff 'Display still in use; recovery guard armed; no forced unload attempted'
        exit 0
    fi
    /usr/bin/sleep 0.3
done
exit 0
