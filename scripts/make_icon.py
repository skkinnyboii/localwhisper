"""Build AppIcon.icns from the source logo.

The source render (assets/icon-source.png) is a squircle on an opaque
white card. macOS icons need transparent surroundings, so this: finds the
squircle, crops to it, masks it to the Apple rounded-square shape, adds a
soft baked shadow, and emits a full .iconset -> .icns.

Usage:  uv run --with pillow python scripts/make_icon.py SRC OUT.icns
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

CANVAS = 1024
GLYPH = 832  # squircle size on the canvas, per Apple's icon grid
RADIUS_RATIO = 0.225  # Apple squircle corner radius / size

SIZES = [16, 32, 128, 256, 512]  # each also gets an @2x


def find_squircle_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of the non-background artwork, ignoring the soft shadow."""
    gray = image.convert("L")
    background = gray.getpixel((3, 3))
    # >12 levels away from the background counts as artwork; the shadow is
    # a much subtler gradient and stays below that
    mask = gray.point(lambda p: 255 if abs(p - background) > 12 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        raise SystemExit("could not find artwork in the source image")
    return bbox


def build_glyph(source: Path) -> Image.Image:
    image = Image.open(source).convert("RGB")
    left, top, right, bottom = find_squircle_bbox(image)
    side = max(right - left, bottom - top)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    box = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
    square = image.crop(box).resize((GLYPH, GLYPH), Image.LANCZOS)

    radius = int(GLYPH * RADIUS_RATIO)
    mask = Image.new("L", (GLYPH, GLYPH), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, GLYPH - 1, GLYPH - 1), radius=radius, fill=255
    )

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - GLYPH) // 2

    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    shadow_tile = Image.new("RGBA", (GLYPH, GLYPH), (0, 0, 0, 70))
    shadow.paste(shadow_tile, (offset, offset + 14), mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.alpha_composite(canvas, shadow)

    canvas.paste(square, (offset, offset), mask)
    return canvas


def main() -> None:
    source, out_icns = Path(sys.argv[1]), Path(sys.argv[2])
    glyph = build_glyph(source)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in SIZES:
            for scale in (1, 2):
                pixels = size * scale
                suffix = "" if scale == 1 else "@2x"
                glyph.resize((pixels, pixels), Image.LANCZOS).save(
                    iconset / f"icon_{size}x{size}{suffix}.png"
                )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)],
            check=True,
        )
    print(f"wrote {out_icns}")


if __name__ == "__main__":
    main()
