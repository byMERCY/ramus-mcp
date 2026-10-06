"""Draw the extension's icon: an IDEF0 box with its four arrows - input, control, mechanism,
output - on a rounded square. Writes icon.png (512 x 512) in the project folder."""

import os

from PIL import Image, ImageDraw

SIZE, SCALE = 512, 4  # drawn four times larger and scaled down, for smooth edges
BACK, INK, ACCENT = (31, 78, 121), (255, 255, 255), (255, 196, 61)


def main() -> None:
    s = SIZE * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = s / 32  # a grid of 32 units
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=6 * u, fill=BACK)
    line = int(1.1 * u)

    def arrow(x0, y0, x1, y1, color):
        """A line from (x0, y0) whose head's tip touches (x1, y1); the line stops inside the
        head, so nothing shows past its tip."""
        head = 2.6 * u
        if x0 == x1:  # vertical
            sign = 1 if y1 > y0 else -1
            d.line((x0, y0, x1, y1 - sign * head * 0.8), fill=color, width=line)
            d.polygon([(x1, y1), (x1 - head * 0.65, y1 - sign * head),
                       (x1 + head * 0.65, y1 - sign * head)], fill=color)
        else:
            sign = 1 if x1 > x0 else -1
            d.line((x0, y0, x1 - sign * head * 0.8, y1), fill=color, width=line)
            d.polygon([(x1, y1), (x1 - sign * head, y1 - head * 0.65),
                       (x1 - sign * head, y1 + head * 0.65)], fill=color)

    box = (10 * u, 11 * u, 22 * u, 21 * u)
    half = line / 2  # tips touch the box's outline, not its middle
    d.rectangle(box, fill=BACK, outline=INK, width=line)
    arrow(3 * u, 16 * u, box[0] - half, 16 * u, INK)            # input
    arrow(16 * u, 3.5 * u, 16 * u, box[1] - half, INK)          # control
    arrow(16 * u, 28.5 * u, 16 * u, box[3] + half, INK)         # mechanism
    arrow(box[2] + half, 16 * u, 29 * u, 16 * u, ACCENT)        # output
    d.text((box[2] - 1.6 * u, box[3] - 1.6 * u), "A0", fill=INK, font_size=int(2.6 * u),
           anchor="rs")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon.png")
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, optimize=True)
    print(out)


if __name__ == "__main__":
    main()
