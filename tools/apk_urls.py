from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path


URL_RE = re.compile(rb"https?://[^\x00\s\"']+")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: apk_urls.py <apk> [<apk> ...]")
        return 2

    urls: set[str] = set()
    interesting: set[str] = set()
    for raw in sys.argv[1:]:
        apk = Path(raw)
        with zipfile.ZipFile(apk) as archive:
            for name in archive.namelist():
                lower = name.lower()
                if not (
                    lower.endswith((".so", ".txt", ".json", ".xml"))
                    or lower.startswith("assets/")
                    or "catalog" in lower
                    or "octo" in lower
                ):
                    continue
                try:
                    data = archive.read(name)
                except Exception:
                    continue
                for match in URL_RE.finditer(data):
                    urls.add(match.group(0).decode("ascii", "ignore"))
                if any(token in data for token in (b"octo", b"Octo", b"catalog", b"Catalog")):
                    interesting.add(name)

    print("interesting_entries:")
    for name in sorted(interesting)[:300]:
        print(name)
    print("urls:")
    for url in sorted(urls)[:300]:
        print(url)
    print(f"count={len(urls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
