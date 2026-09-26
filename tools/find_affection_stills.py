from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import UnityPy
from PIL import Image, ImageDraw

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.0.77f1"


DEFAULT_DEVICE = "127.0.0.1:16384"
DEFAULT_OCTO_ROOT = "/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400"
DEFAULT_SCRIPT_BUCKETS = "0,1,2,3,4,5,6,7,8,9"
DEFAULT_STILL_SCRIPT_BUCKETS = "0,1,2,3,4,5,6,7,8,9"

CHARACTER_ALIASES: dict[str, tuple[str, str]] = {
    "tsubame": ("tsubame", "atbm"),
    "atbm": ("tsubame", "atbm"),
    "sena": ("sena", "jsna"),
    "jsna": ("sena", "jsna"),
    "ume": ("ume", "hume"),
    "hume": ("ume", "hume"),
    "lilja": ("lilja", "kllj"),
    "lilija": ("lilja", "kllj"),
    "kllj": ("lilja", "kllj"),
    "china": ("china", "kcna"),
    "kuramoto_china": ("china", "kcna"),
    "kcna": ("china", "kcna"),
}


@dataclass(frozen=True)
class Character:
    slug: str
    code: str


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def decode_remote_asset_id(remote: str) -> str:
    parts = remote.strip("/").split("/")
    if len(parts) < 2:
        return "unknown"
    try:
        return bytes.fromhex(parts[-2]).decode("ascii", errors="ignore") or "unknown"
    except ValueError:
        return "unknown"


def safe_name(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value)
    value = value.strip(" ._")
    return value or "unknown"


def local_remote_name(remote: str, suffix: str) -> str:
    asset_id = decode_remote_asset_id(remote)
    hash_name = Path(remote).name
    return f"{safe_name(asset_id)}_{safe_name(hash_name)}{suffix}"


def adb_run(args: list[str], timeout: int, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args],
        capture_output=capture,
        text=capture,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )


def adb_connect(device: str) -> None:
    proc = adb_run(["connect", device], timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "adb connect failed")


def remote_grep_many(
    *,
    device: str,
    roots: list[str],
    patterns: list[str],
    timeout: int,
    size_filter: str = "",
    path_filter: str = "",
) -> list[str]:
    if not patterns:
        return []

    roots_part = " ".join(shlex.quote(root) for root in roots)
    pattern_part = " ".join(f"-e {shlex.quote(pattern)}" for pattern in patterns)
    filters = ["-type f"]
    if size_filter:
        filters.append(size_filter)
    if path_filter:
        filters.append(path_filter)
    find_part = " ".join(filters)
    remote = f"find {roots_part} {find_part} -print0 | xargs -0 grep -a -l {pattern_part}"
    proc = adb_run(["-s", device, "shell", "su 0 sh -c " + shlex.quote(remote)], timeout=timeout)
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("/")]
    if proc.returncode not in (0, 1, 123) and not lines:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"remote grep failed: {proc.returncode}")
    return sorted(dict.fromkeys(lines))


def pull_remote_file(device: str, remote: str, target: Path, timeout: int = 120) -> Path:
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["adb", "-s", device, "exec-out", "su", "0", "cat", remote],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="ignore").strip() or f"failed to pull {remote}")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(proc.stdout)
    tmp.replace(target)
    return target


def string_to_mask_bytes(mask_string: str, mask_string_length: int, bytes_length: int) -> bytes:
    mask_bytes = bytearray(bytes_length)
    if mask_string != 0:
        if mask_string_length >= 1:
            i = 0
            j = 0
            k = bytes_length - 1
            while mask_string_length != j:
                char_j = int.from_bytes(mask_string[j].encode("ascii"), byteorder="little", signed=False)
                j += 1
                mask_bytes[i] = char_j
                i += 2
                char_j = ~char_j & 0xFF
                mask_bytes[k] = char_j
                k -= 2
        if bytes_length >= 1:
            remaining = bytes_length
            seed = 0x9B
            pointer = 0
            while remaining:
                value = mask_bytes[pointer]
                pointer += 1
                remaining -= 1
                seed = (((seed & 1) << 7) | (seed >> 1)) ^ value
            for index in range(bytes_length):
                mask_bytes[index] ^= seed
    return bytes(mask_bytes)


def decrypt_asset_bundle(data: bytes, key: str, header_length: int = 256) -> bytes:
    if data.startswith(b"UnityFS"):
        return data
    mask = string_to_mask_bytes(key, len(key), len(key) << 1)
    decoded = bytearray(data)
    for index in range(min(header_length, len(decoded))):
        decoded[index] ^= mask[index % len(mask)]
    return bytes(decoded)


def parse_final_still_group(script_text: str) -> list[str]:
    stills: list[str] = []
    still_re = re.compile(r"\[still\b[^\]]*\bsrc=([^\s\]]+)")
    for line in script_text.splitlines():
        match = still_re.search(line)
        if match:
            src = match.group(1)
            if src not in stills:
                stills.append(src)
    if not stills:
        return []
    final_prefix = re.sub(r"-\d+$", "", stills[-1])
    return [src for src in stills if re.sub(r"-\d+$", "", src) == final_prefix]


def export_texture_png(bundle_bytes: bytes, logical_name: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    env = UnityPy.load(bundle_bytes)
    best: tuple[int, Image.Image] | None = None
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        data = obj.read()
        image = data.image
        if image is None:
            continue
        area = image.width * image.height
        if best is None or area > best[0]:
            best = (area, image)
    if best is None:
        raise RuntimeError(f"no Texture2D image in {logical_name}")
    best[1].save(target)
    return target


def make_contact_sheet(paths: list[Path], target: Path) -> None:
    if not paths:
        return
    thumb_w = 260
    thumb_h = 520
    label_h = 34
    sheet = Image.new("RGB", (thumb_w * len(paths), thumb_h + label_h), "white")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        image = Image.open(path).convert("RGB")
        image.thumbnail((thumb_w, thumb_h))
        x = index * thumb_w + (thumb_w - image.width) // 2
        y = (thumb_h - image.height) // 2
        sheet.paste(image, (x, y))
        draw.text((index * thumb_w + 6, thumb_h + 8), path.stem[-28:], fill=(0, 0, 0))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)


def parse_characters(raw: str) -> list[Character]:
    characters: list[Character] = []
    for item in re.split(r"[,;]\s*", raw.strip()):
        if not item:
            continue
        if ":" in item:
            slug, code = item.split(":", 1)
            characters.append(Character(safe_name(slug.lower()), safe_name(code.lower())))
            continue
        key = item.lower()
        if key not in CHARACTER_ALIASES:
            raise ValueError(f"unknown character alias: {item}; use slug:code for custom characters")
        slug, code = CHARACTER_ALIASES[key]
        characters.append(Character(slug, code))
    deduped: dict[str, Character] = {}
    for character in characters:
        deduped[character.slug] = character
    return list(deduped.values())


def script_roots(octo_root: str, buckets: str) -> list[str]:
    roots: list[str] = []
    for bucket in re.split(r"[,;]\s*", buckets.strip()):
        if not bucket:
            continue
        roots.append(bucket if bucket.startswith("/") else f"{octo_root.rstrip('/')}/{bucket}")
    return roots


def find_scripts(
    *,
    device: str,
    characters: list[Character],
    story: str,
    roots: list[str],
    cache_dir: Path,
    refresh: bool,
    timeout: int,
) -> dict[str, tuple[str, Path]]:
    cache_path = cache_dir / "script_hits.json"
    cache: dict[str, list[str]] = {} if refresh else load_json(cache_path, {})
    patterns = {character.slug: f"sud_vo_adv_dear_{character.code}_{story}" for character in characters}
    missing_patterns = sorted({pattern for pattern in patterns.values() if pattern not in cache})

    if missing_patterns:
        hits = remote_grep_many(
            device=device,
            roots=roots,
            patterns=missing_patterns,
            timeout=timeout,
            size_filter="-size -500k",
        )
        for hit in hits:
            local = pull_remote_file(device, hit, cache_dir / "scripts" / local_remote_name(hit, ".txt"))
            text = local.read_text(encoding="utf-8", errors="ignore")
            for pattern in missing_patterns:
                if pattern in text:
                    cache.setdefault(pattern, [])
                    if hit not in cache[pattern]:
                        cache[pattern].append(hit)
        for pattern in missing_patterns:
            cache.setdefault(pattern, [])
        save_json(cache_path, cache)

    resolved: dict[str, tuple[str, Path]] = {}
    for character in characters:
        pattern = patterns[character.slug]
        hits = cache.get(pattern, [])
        if not hits:
            continue
        selected: tuple[str, Path] | None = None
        fallback: tuple[str, Path] | None = None
        for remote in hits:
            local = pull_remote_file(device, remote, cache_dir / "scripts" / local_remote_name(remote, ".txt"))
            fallback = fallback or (remote, local)
            if parse_final_still_group(local.read_text(encoding="utf-8", errors="ignore")):
                selected = (remote, local)
                break
        if selected is None:
            selected = fallback
        if selected is None:
            continue
        remote, local = selected
        resolved[character.slug] = (remote, local)
    return resolved


def find_still_scripts(
    *,
    device: str,
    characters: list[Character],
    roots: list[str],
    cache_dir: Path,
    refresh: bool,
    timeout: int,
) -> dict[str, list[tuple[str, Path]]]:
    cache_path = cache_dir / "still_script_hits.json"
    cache: dict[str, list[str]] = {} if refresh else load_json(cache_path, {})
    patterns = {character.slug: f"img_adv_still_dear_{character.code}" for character in characters}
    missing_patterns = sorted({pattern for pattern in patterns.values() if pattern not in cache})

    if missing_patterns:
        hits = remote_grep_many(
            device=device,
            roots=roots,
            patterns=missing_patterns,
            timeout=timeout,
            size_filter="-size -500k",
        )
        for hit in hits:
            local = pull_remote_file(device, hit, cache_dir / "scripts" / local_remote_name(hit, ".txt"))
            text = local.read_text(encoding="utf-8", errors="ignore")
            for pattern in missing_patterns:
                if pattern in text:
                    cache.setdefault(pattern, [])
                    if hit not in cache[pattern]:
                        cache[pattern].append(hit)
        for pattern in missing_patterns:
            cache.setdefault(pattern, [])
        save_json(cache_path, cache)

    resolved: dict[str, list[tuple[str, Path]]] = {}
    for character in characters:
        pattern = patterns[character.slug]
        for remote in cache.get(pattern, []):
            local = pull_remote_file(device, remote, cache_dir / "scripts" / local_remote_name(remote, ".txt"))
            resolved.setdefault(character.slug, []).append((remote, local))
    return resolved


def find_asset_paths(
    *,
    device: str,
    names: list[str],
    octo_root: str,
    cache_dir: Path,
    refresh: bool,
    timeout: int,
) -> dict[str, str]:
    cache_path = cache_dir / "asset_hits.json"
    cache: dict[str, list[str]] = {} if refresh else load_json(cache_path, {})
    missing = [name for name in names if name not in cache]
    if missing:
        hits = remote_grep_many(
            device=device,
            roots=[octo_root],
            patterns=missing,
            timeout=timeout,
            path_filter="! -name .meta -path '*/41*/*'",
        )
        for hit in hits:
            raw_path = pull_remote_file(device, hit, cache_dir / "bundles" / local_remote_name(hit, ".bundle"))
            raw = raw_path.read_bytes()
            for name in list(missing):
                decoded = decrypt_asset_bundle(raw, name)
                if decoded.startswith(b"UnityFS"):
                    cache.setdefault(name, [])
                    if hit not in cache[name]:
                        cache[name].append(hit)
        for name in missing:
            cache.setdefault(name, [])
        save_json(cache_path, cache)
    return {name: paths[0] for name, paths in cache.items() if name in names and paths}


def process_characters(args: argparse.Namespace) -> list[dict[str, Any]]:
    characters = parse_characters(args.characters)
    story = str(args.story).zfill(3)
    out_dir = Path(args.out)
    cache_dir = out_dir / "cache"

    adb_connect(args.device)
    scripts = find_scripts(
        device=args.device,
        characters=characters,
        story=story,
        roots=script_roots(args.octo_root, args.script_roots),
        cache_dir=cache_dir,
        refresh=args.refresh,
        timeout=args.script_timeout,
    )

    results: list[dict[str, Any]] = []
    all_stills: list[str] = []
    for character in characters:
        script_info = scripts.get(character.slug)
        if script_info is None:
            results.append(
                {
                    "slug": character.slug,
                    "code": character.code,
                    "story": story,
                    "error": "script not found",
                }
            )
            continue
        script_remote, script_local = script_info
        stills = parse_final_still_group(script_local.read_text(encoding="utf-8", errors="ignore"))
        all_stills.extend(stills)
        results.append(
            {
                "slug": character.slug,
                "code": character.code,
                "story": story,
                "script_remote": script_remote,
                "script_local": str(script_local),
                "still_names": stills,
            }
        )

    fallback_characters = [
        character
        for character in characters
        if any(result["slug"] == character.slug and not result.get("still_names") and not result.get("error") for result in results)
    ]
    if fallback_characters:
        still_scripts = find_still_scripts(
            device=args.device,
            characters=fallback_characters,
            roots=script_roots(args.octo_root, args.still_script_roots),
            cache_dir=cache_dir,
            refresh=args.refresh,
            timeout=args.script_timeout,
        )
        for result in results:
            if result.get("still_names") or result.get("error"):
                continue
            for remote, local in still_scripts.get(result["slug"], []):
                stills = parse_final_still_group(local.read_text(encoding="utf-8", errors="ignore"))
                if not stills:
                    continue
                result["script_remote"] = remote
                result["script_local"] = str(local)
                result["still_names"] = stills
                result["source"] = "still_reference_fallback"
                all_stills.extend(stills)
                break

    asset_paths = find_asset_paths(
        device=args.device,
        names=sorted(dict.fromkeys(all_stills)),
        octo_root=args.octo_root,
        cache_dir=cache_dir,
        refresh=args.refresh,
        timeout=args.asset_timeout,
    )

    for result in results:
        if result.get("error"):
            continue
        png_paths: list[str] = []
        bundle_paths: dict[str, str] = {}
        for still_name in result["still_names"]:
            remote = asset_paths.get(still_name)
            if remote is None:
                result.setdefault("missing_assets", []).append(still_name)
                continue
            raw_path = pull_remote_file(args.device, remote, cache_dir / "bundles" / local_remote_name(remote, ".bundle"))
            decoded = decrypt_asset_bundle(raw_path.read_bytes(), still_name)
            if not decoded.startswith(b"UnityFS"):
                result.setdefault("failed_decrypt", []).append(still_name)
                continue
            slug_dir = out_dir / result["slug"]
            decrypted_path = slug_dir / "decrypted" / f"{safe_name(still_name)}.bundle"
            decrypted_path.parent.mkdir(parents=True, exist_ok=True)
            decrypted_path.write_bytes(decoded)
            png_path = slug_dir / "png" / f"{safe_name(still_name)}.png"
            export_texture_png(decoded, still_name, png_path)
            png_paths.append(str(png_path))
            bundle_paths[still_name] = remote
        result["bundle_remote_paths"] = bundle_paths
        result["png_paths"] = png_paths
        make_contact_sheet([Path(path) for path in png_paths], out_dir / result["slug"] / "contact_sheet.jpg")
        save_json(out_dir / result["slug"] / "manifest.json", result)

    save_json(out_dir / "results.json", results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and export final affection story still images from the Gakumasu Octo cache."
    )
    parser.add_argument("--characters", default="tsubame,sena,ume,lilja")
    parser.add_argument("--story", default="010")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--octo-root", default=DEFAULT_OCTO_ROOT)
    parser.add_argument("--script-roots", default=DEFAULT_SCRIPT_BUCKETS)
    parser.add_argument("--still-script-roots", default=DEFAULT_STILL_SCRIPT_BUCKETS)
    parser.add_argument("--out", default="output/affection10")
    parser.add_argument("--refresh", action="store_true", help="Ignore JSON hit caches and rescan remote cache.")
    parser.add_argument("--script-timeout", type=int, default=180)
    parser.add_argument("--asset-timeout", type=int, default=300)
    args = parser.parse_args()

    try:
        results = process_characters(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for result in results:
        status = "OK" if result.get("png_paths") else "MISSING"
        print(f"{status}\t{result['slug']}\t{','.join(result.get('still_names', []))}")
        for path in result.get("png_paths", []):
            print(f"  {path}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("missing_assets"):
            print(f"  missing assets: {','.join(result['missing_assets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
