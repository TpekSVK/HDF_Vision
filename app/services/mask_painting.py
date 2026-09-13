"""Raster mask operations shared by standalone and combined canvases."""
import cv2


def brush_point(mask, x, y, radius, *, erase=False):
    if mask is not None:
        cv2.circle(mask, (round(x), round(y)), max(1, int(radius)), 0 if erase else 255, -1)
