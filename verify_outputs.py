from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def find_latest_run_root(path: Path) -> Path:
    if (path / "coverage.json").exists() or (path / "reports" / "coverage.json").exists():
        return path

    candidates = [coverage.parent for coverage in path.glob("*/coverage.json")]
    candidates.extend(coverage.parent for coverage in path.glob("*/*/coverage.json"))
    if not candidates:
        raise FileNotFoundError(f"no coverage.json found under {path}")
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def coverage_path(root: Path) -> Path:
    current = root / "coverage.json"
    if current.exists():
        return current
    legacy = root / "reports" / "coverage.json"
    if legacy.exists():
        return legacy
    raise FileNotFoundError(f"no coverage.json found under {root}")


def default_lines_path(root: Path, coverage: dict) -> Path:
    slug = coverage.get("character_slug") or root.parent.name or root.name
    preferred = root / f"{slug}_lines.tsv"
    if preferred.exists():
        return preferred
    matches = sorted(root.glob("*_lines.tsv"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"no *_lines.tsv found under {root}")


def row_path(row: dict[str, str], key: str) -> Path | None:
    value = row.get(key, "")
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify exported Gakumasu voice items.")
    parser.add_argument("--output", default="output", help="Run directory, character directory, or output base directory.")
    parser.add_argument("--lines", default="", help="Override the summary TSV path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = find_latest_run_root(Path(args.output))
    coverage = json.load(coverage_path(root).open(encoding="utf-8"))
    lines_path = Path(args.lines) if args.lines else default_lines_path(root, coverage)
    rows = list(csv.DictReader(lines_path.open(encoding="utf-8"), delimiter="\t"))

    missing = []
    for row in rows:
        wav = row_path(row, "wav_file")
        subtitle = row_path(row, "subtitle_file")
        metadata = row_path(row, "metadata_file")

        if wav and (not subtitle or not metadata):
            legacy_item_dir = wav.parent
            subtitle = subtitle or legacy_item_dir / "subtitle.txt"
            metadata = metadata or legacy_item_dir / "metadata.json"

        if (
            not wav
            or not wav.exists()
            or wav.stat().st_size == 0
            or not subtitle
            or not subtitle.exists()
            or not metadata
            or not metadata.exists()
        ):
            missing.append(row.get("voice_cue", ""))

    print(f"output={root}")
    print(f"rows={len(rows)}")
    print(f"missing={len(missing)}")
    print(f"first={rows[0]['voice_cue'] if rows else ''}")
    print(f"last={rows[-1]['voice_cue'] if rows else ''}")
    print(f"character_slug={coverage.get('character_slug', '')}")
    print(f"character_code={coverage.get('character_code', '')}")
    print(f"voice_scripts={len(coverage.get('scripts_with_character_voice', []))}")
    print(f"name_scripts={len(coverage.get('scripts_with_character_name', []))}")
    print(f"speaker_scripts={len(coverage.get('scripts_with_character_speaker_image', []))}")
    print(f"full_name_scripts={len(coverage.get('scripts_with_full_name_text', []))}")
    print(f"voice_line_count={coverage['voice_line_count']}")
    print(f"exported_count={coverage['exported_count']}")
    print(f"unmatched_count={coverage['unmatched_count']}")
    print(f"bank_count={coverage['bank_count']}")
    if rows:
        print(f"sample_text={rows[0]['text']}")
        print(f"sample_text_escape={rows[0]['text'].encode('unicode_escape').decode('ascii')}")
        print(f"last_text_escape={rows[-1]['text'].encode('unicode_escape').decode('ascii')}")
    return 0 if not missing and len(rows) == coverage["exported_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
