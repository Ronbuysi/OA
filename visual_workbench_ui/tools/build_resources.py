"""Recreate the small QSS raster resources omitted by the source export."""

from pathlib import Path

from PIL import Image, ImageDraw


OUT = Path(__file__).resolve().parents[1] / "visual_workbench" / "ui" / "resources"
OUT.mkdir(parents=True, exist_ok=True)
SCALE = 4


def stroked(name: str, size: tuple[int, int], points: list[tuple[int, int]], color: str):
    canvas = Image.new("RGBA", (size[0] * SCALE, size[1] * SCALE))
    draw = ImageDraw.Draw(canvas)
    draw.line([(x * SCALE, y * SCALE) for x, y in points], fill=color,
              width=2 * SCALE, joint="curve")
    canvas.resize(size, Image.Resampling.LANCZOS).save(OUT / name)


stroked("arrow_down.png", (16, 12), [(3, 4), (8, 9), (13, 4)], "#66748A")
stroked("arrow_up.png", (16, 12), [(3, 8), (8, 3), (13, 8)], "#66748A")
stroked("chevron_left.png", (14, 20), [(10, 3), (4, 10), (10, 17)], "#66748A")
stroked("chevron_right.png", (14, 20), [(4, 3), (10, 10), (4, 17)], "#66748A")
stroked("check.png", (16, 16), [(2, 8), (6, 12), (14, 3)], "#FFFFFF")

logo = Image.new("RGBA", (64 * SCALE, 64 * SCALE))
draw = ImageDraw.Draw(logo)
draw.rounded_rectangle((0, 0, 64 * SCALE - 1, 64 * SCALE - 1),
                       radius=17 * SCALE, fill="#315BD7")
for x, y, w, h in ((14, 14, 16, 16), (34, 14, 16, 16),
                   (14, 34, 16, 16), (34, 34, 16, 16)):
    draw.rounded_rectangle((x * SCALE, y * SCALE, (x + w) * SCALE,
                            (y + h) * SCALE), radius=4 * SCALE,
                           fill="#FFFFFF" if (x, y) != (34, 14) else "#A8D6FF")
logo.resize((64, 64), Image.Resampling.LANCZOS).save(OUT / "logo.png")
print(f"wrote 6 resources to {OUT}")
