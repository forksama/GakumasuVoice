from __future__ import annotations

import csv
import json
from pathlib import Path


def main() -> int:
    root = Path("output/rinha")
    rows = list(csv.DictReader((root / "rinha_lines.tsv").open(encoding="utf-8"), delimiter="\t"))
    missing = []
    for row in rows:
        wav = Path(row["wav_file"])
        item_dir = wav.parent
        if (
            not wav.exists()
            or wav.stat().st_size == 0
            or not (item_dir / "subtitle.txt").exists()
            or not (item_dir / "metadata.json").exists()
        ):
            missing.append(row["voice_cue"])

    coverage = json.load((root / "reports" / "coverage.json").open(encoding="utf-8"))
    print(f"rows={len(rows)}")
    print(f"missing={len(missing)}")
    print(f"first={rows[0]['voice_cue'] if rows else ''}")
    print(f"last={rows[-1]['voice_cue'] if rows else ''}")
    print(f"krnh_scripts={len(coverage['scripts_with_character_voice'])}")
    print(f"name_scripts={len(coverage['scripts_with_character_name'])}")
    print(f"speaker_scripts={len(coverage['scripts_with_character_speaker_image'])}")
    print(f"full_name_scripts={len(coverage['scripts_with_full_name_text'])}")
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
