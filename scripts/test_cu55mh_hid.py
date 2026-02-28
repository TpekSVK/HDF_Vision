#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.xu_controls_hid_cu55mh import CU55MH_HID, CU55MHProtocol


def main() -> int:
    hidraw = os.getenv("HDF_HIDRAW", "/dev/hidraw0")
    cam = None
    try:
        cam = CU55MH_HID(video_dev=os.getenv("CAM_DEV", "/dev/video0"), hidraw_path=hidraw)

        mode0 = cam.get_stream_mode()
        print(f"Current stream mode: {mode0}")

        cam.set_stream_mode(CU55MHProtocol.MODE_TRIGGER)
        mode1 = cam.get_stream_mode()
        print(f"After trigger set: {mode1}")

        cam.set_stream_mode(CU55MHProtocol.MODE_MASTER)
        print("Restored stream mode to master")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if cam is not None:
            cam.close()


if __name__ == "__main__":
    raise SystemExit(main())
