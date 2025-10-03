# XU stub pre See3CAM_CU55M – Linux V4L2 XU (alebo vendor .so)
# Podľa manuálu:
#  - Stream Mode: 0x00 Master, 0x01 Trigger
#  - Flash: 0x00 OFF, 0x01 Strobe, 0x02 Torch
#  TODO: doplniť GUID Extension Unit + selector IDs podľa SDK (Linux ekvivalent).
#  Pozri: See3CAM_CU55M Extension Unit SDK API Manual (SetStreamModeCU55_MH, SetFlashCU55_MH, RestoreDefaultCU55_MH…)

import os

class XUControls:
    def __init__(self, video_dev="/dev/video0"):
        self.video_dev = video_dev
        # TODO: načítanie GUID/selectorov

    def set_stream_mode(self, mode:int):
        # mode: 0=Master, 1=Trigger
        # TODO: implementovať cez V4L2 UVC XU (ioctl) alebo vendor .so
        print(f"[XU-STUB] SetStreamMode -> {mode} (TODO)")

    def get_stream_mode(self) -> int:
        # TODO: vrátiť reálne z XU
        return 0

    def set_flash_mode(self, val:int):
        # 0=OFF, 1=Strobe, 2=Torch
        print(f"[XU-STUB] SetFlash -> {val} (TODO)")

    def restore_defaults(self):
        print("[XU-STUB] RestoreDefault (TODO)")

    def set_manual_exposure_us(self, exposure_us:int):
        # Pozn.: v Trigger Mode musí byť expo >= trigger period; pre 1080p@60 je frame ~16.67 ms. :contentReference[oaicite:4]{index=4}
        print(f"[XU-STUB] Set Manual Exposure (us) -> {exposure_us} (TODO)")

    def set_gain_db(self, gain_db:int):
        print(f"[XU-STUB] Set Gain (dB) -> {gain_db} (TODO)")
