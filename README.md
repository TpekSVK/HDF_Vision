sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

## GPIO trigger (fixed mapping)

- RUN trigger pulse používa fixný Jetson BOARD pin `7`.
- Pulz je fixne `10 ms`.
- GPIO mapping sa nekonfiguruje cez UI ani cez recipe.
