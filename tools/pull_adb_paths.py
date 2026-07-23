from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def local_name(remote: str) -> str:
    parts = remote.strip("/").split("/")
    if len(parts) >= 2:
        asset_id = bytes.fromhex(parts[-2]).decode("ascii", errors="ignore")
        return f"{asset_id}_{parts[-1]}.bundle"
    return Path(remote).name + ".bundle"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="127.0.0.1:16384")
    parser.add_argument("--list", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    paths = [
        line.strip().split("\t")[-1]
        for line in Path(args.list).read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.strip().endswith("/.meta")
    ]
    if args.limit:
        paths = paths[: args.limit]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, remote in enumerate(paths, 1):
        target = out_dir / local_name(remote)
        if target.exists() and target.stat().st_size > 0:
            print(f"SKIP exists {target}")
            continue
        print(f"[{index}/{len(paths)}] {remote} -> {target}")
        with target.open("wb") as handle:
            proc = subprocess.run(
                ["adb", "-s", args.device, "exec-out", "su", "0", "cat", remote],
                stdout=handle,
                stderr=subprocess.PIPE,
            )
        if proc.returncode != 0:
            print(proc.stderr.decode("utf-8", errors="ignore").strip())
            target.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
