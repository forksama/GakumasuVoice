from __future__ import annotations

import collections
import sys
from pathlib import Path

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.77f1"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: inspect_unity_bundle.py <bundle>")
        return 2

    bundle = Path(sys.argv[1])
    env = UnityPy.load(str(bundle))
    counts: collections.Counter[str] = collections.Counter()
    names: list[tuple[str, str]] = []

    for obj in env.objects:
        type_name = str(obj.type.name)
        counts[type_name] += 1
        if len(names) >= 80:
            continue
        try:
            data = obj.read()
            name = getattr(data, "name", "") or ""
        except Exception as exc:  # keep scanning even if one object is odd
            name = f"ERR:{exc!s}"[:120]
        names.append((type_name, name))

    print(f"bundle={bundle}")
    print("counts:")
    for key, value in counts.most_common():
        print(f"  {key}: {value}")
    print("sample_names:")
    for type_name, name in names:
        print(f"  {type_name}\t{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
