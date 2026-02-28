#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.xu_controls_hid_cu55mh import CU55MH_HID, discover_cu55mh_hidraw


def main() -> int:
    hidraw = discover_cu55mh_hidraw()
    if not hidraw:
        print("ERROR: no suitable hidraw device found")
        return 1

    print(f"Selected HID node: {hidraw}")
    cam = None
    try:
        cam = CU55MH_HID(video_dev="/dev/video0", hidraw_path=hidraw)

        cam.set_stream_mode(0)
        m0 = cam.get_stream_mode()
        print(f"mode after set master: {m0}")

        cam.set_stream_mode(1)
        m1 = cam.get_stream_mode()
        print(f"mode after set trigger: {m1}")

        cam.set_stream_mode(0)
        m2 = cam.get_stream_mode()
        print(f"mode after restore master: {m2}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if cam is not None:
            cam.close()


if __name__ == "__main__":
    raise SystemExit(main())
