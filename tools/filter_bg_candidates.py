from __future__ import annotations

import argparse
import re
from pathlib import Path


ASSET_ID_RE = re.compile(r"^A(\d+)$")


def decode_asset_id(remote_path: str) -> str:
    for part in reversed(remote_path.strip().split("/")):
        if not part.startswith("41"):
            continue
        try:
            decoded = bytes.fromhex(part).decode("ascii")
        except ValueError:
            continue
        if ASSET_ID_RE.match(decoded):
            return decoded
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter Gakumasu A-cache size TSV into likely 2D ADV background bundle candidates."
    )
    parser.add_argument("--sizes", required=True, help="TSV from tools/stat_adb_paths.py: size<TAB>remote_path")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-size", type=int, default=780000)
    parser.add_argument("--max-size", type=int, default=980000)
    parser.add_argument("--max-asset-id", type=int, default=9000)
    args = parser.parse_args()

    rows: list[tuple[int, str, str]] = []
    for line in Path(args.sizes).read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            raw_size, remote = line.split("\t", 1)
            size = int(raw_size)
        except ValueError:
            continue
        if size < args.min_size or size > args.max_size:
            continue
        asset_id = decode_asset_id(remote)
        match = ASSET_ID_RE.match(asset_id)
        if not match or int(match.group(1)) >= args.max_asset_id:
            continue
        rows.append((size, asset_id, remote))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{size}\t{remote}\n" for size, _, remote in rows), encoding="utf-8")

    total_mb = sum(size for size, _, _ in rows) / 1024 / 1024
    print(f"{len(rows)} candidates, {total_mb:.1f} MB -> {out}")
    if rows[:5]:
        print("sample:")
        for size, asset_id, remote in rows[:5]:
            print(f"  {asset_id}\t{size}\t{remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
