from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="127.0.0.1:16384")
    parser.add_argument("--paths", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--batch", type=int, default=25)
    args = parser.parse_args()

    paths = [
        line.strip()
        for line in Path(args.paths).read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.strip().endswith("/.meta")
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(0, len(paths), args.batch):
            batch = paths[index : index + args.batch]
            remote = "stat -c '%s %n' " + " ".join(batch)
            cmd = ["adb", "-s", args.device, "shell", f"su 0 sh -c \"{remote}\""]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if proc.stdout:
                for line in proc.stdout.splitlines():
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        handle.write(f"{parts[0]}\t{parts[1]}\n")
                        written += 1
            if proc.stderr:
                print(proc.stderr.strip())
            if index % (args.batch * 25) == 0:
                print(f"{index}/{len(paths)} paths, wrote {written}")

    print(f"done: {written} entries -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
