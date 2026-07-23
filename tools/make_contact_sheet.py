from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--cols", type=int, default=5)
    parser.add_argument("--thumb-w", type=int, default=180)
    parser.add_argument("--thumb-h", type=int, default=120)
    args = parser.parse_args()

    files = list(Path(args.image_dir).glob("*.png"))
    files.sort(key=lambda p: Image.open(p).size[0] * Image.open(p).size[1], reverse=True)
    files = files[: args.count]

    tile_w = args.thumb_w
    tile_h = args.thumb_h + 25
    rows = max(1, math.ceil(len(files) / args.cols))
    sheet = Image.new("RGB", (args.cols * tile_w, rows * tile_h), "white")
    draw = ImageDraw.Draw(sheet)

    for index, path in enumerate(files):
        image = Image.open(path).convert("RGB")
        image.thumbnail((args.thumb_w, args.thumb_h))
        x = (index % args.cols) * tile_w
        y = (index // args.cols) * tile_h
        sheet.paste(image, (x + (tile_w - image.width) // 2, y))
        draw.text((x + 2, y + args.thumb_h + 4), path.stem, fill=(0, 0, 0))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
