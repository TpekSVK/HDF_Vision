#!/usr/bin/env python3
"""Render the existing desktop logo for the system splash (requires Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BG = (20, 24, 29)


def main():
    icon = Image.open(ROOT / 'app/assets/hdf_vision.png').convert('RGBA')
    icon = icon.crop((1, 0, icon.width, icon.height)).resize((420, 420), Image.LANCZOS)
    brand = Image.new('RGB', (420, 480), BG)
    brand.paste(icon, (0, 0), icon)
    draw = ImageDraw.Draw(brand)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 25)
    label, spacing = 'HDF VISION', 3
    width = sum(draw.textlength(c, font=font) for c in label) + spacing * (len(label) - 1)
    x = (420 - width) / 2
    for char in label:
        draw.text((x, 438), char, font=font, fill=(230, 232, 235))
        x += draw.textlength(char, font=font) + spacing
    brand.save(OUT / 'brand.png', optimize=True)
    wall = Image.new('RGB', (1920, 1080), BG)
    wall.paste(brand, ((1920 - 420) // 2, (1080 - 550) // 2))
    wall.save(OUT / 'wallpaper.png', optimize=True)


if __name__ == '__main__':
    main()
