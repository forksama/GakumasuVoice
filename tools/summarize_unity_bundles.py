from __future__ import annotations

import argparse
import collections
from pathlib import Path

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.77f1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_dir")
    parser.add_argument("--out")
    args = parser.parse_args()

    lines: list[str] = []
    for bundle in sorted(Path(args.bundle_dir).glob("*.bundle")):
        try:
            env = UnityPy.load(str(bundle))
        except Exception as exc:
            lines.append(f"## {bundle.name}\nLOAD_ERROR {exc}")
            continue
        counts: collections.Counter[str] = collections.Counter(str(obj.type.name) for obj in env.objects)
        textures: list[str] = []
        names: list[str] = []
        for obj in env.objects:
            if obj.type.name not in {"Texture2D", "GameObject", "Material", "Mesh", "AssetBundle"}:
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            name = getattr(data, "name", "") or ""
            if obj.type.name == "Texture2D":
                textures.append(f"{name or obj.path_id}:{getattr(data, 'm_Width', '?')}x{getattr(data, 'm_Height', '?')}")
            elif name and len(names) < 80:
                names.append(f"{obj.type.name}:{name}")
        lines.append(f"## {bundle.name}")
        lines.append("counts " + ", ".join(f"{k}={v}" for k, v in counts.most_common(12)))
        lines.append("textures " + "; ".join(textures[:80]))
        lines.append("names " + "; ".join(names[:80]))

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
