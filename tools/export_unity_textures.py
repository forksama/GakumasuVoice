from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.77f1"


def safe_name(value: str, fallback: str) -> str:
    value = value or fallback
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value)
    value = value.strip(" ._")
    return value[:160] or fallback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--filter", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-area", type=int, default=0)
    parser.add_argument("--non-square-ratio", type=float, default=0.0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(args.filter, re.I) if args.filter else None

    exported = 0
    scanned = 0
    bundle_paths: list[Path] = []
    for raw in args.bundle:
        matches = sorted(Path().glob(raw)) if any(ch in raw for ch in "*?[") else []
        bundle_paths.extend(matches or [Path(raw)])

    for bundle in bundle_paths:
        try:
            env = UnityPy.load(str(bundle))
        except Exception as exc:
            print(f"SKIP load {bundle}: {exc}", file=sys.stderr)
            continue

        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue
            scanned += 1
            try:
                data = obj.read()
                name = getattr(data, "name", "") or f"{bundle.stem}_{obj.path_id}"
                if pattern and not pattern.search(name):
                    continue
                width = int(getattr(data, "m_Width", 0) or 0)
                height = int(getattr(data, "m_Height", 0) or 0)
                if args.min_area and width * height < args.min_area:
                    continue
                if args.non_square_ratio:
                    long_side = max(width, height)
                    short_side = max(1, min(width, height))
                    if long_side / short_side < args.non_square_ratio:
                        continue
                image = data.image
                if image is None:
                    print(f"SKIP no image {bundle}: {name}", file=sys.stderr)
                    continue
                file_name = safe_name(name, f"{bundle.stem}_{obj.path_id}")
                target = out_dir / f"{file_name}.png"
                suffix = 1
                while target.exists():
                    target = out_dir / f"{file_name}_{suffix}.png"
                    suffix += 1
                image.save(target)
                exported += 1
                print(f"{target}\t{name}\t{image.width}x{image.height}\t{bundle}")
                if args.limit and exported >= args.limit:
                    print(f"scanned={scanned} exported={exported}")
                    return 0
            except Exception as exc:
                print(f"SKIP texture {bundle}: {exc}", file=sys.stderr)

    print(f"scanned={scanned} exported={exported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
