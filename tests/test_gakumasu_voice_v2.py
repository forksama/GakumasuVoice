"""Tests for the v2 subtitle-named, duration-bucketed layout.

Two things here are worth more than the rest:

* ``test_replays_every_migrated_file_name`` reads the metadata of every voice item in
  ``output/`` and recomputes its file name from scratch -- so the naming rule is
  checked against real data without an emulator.
* ``test_every_migrated_item_sits_in_the_right_bucket`` re-measures every wav and
  checks it is in the bucket its duration belongs to.

Together they pin down the extractor's rules against the migrated output.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from gakumasu_voice import VoiceLine
from gakumasu_voice_v2 import (
    TSV_FIELDS_V2,
    collapse_duplicate_cues,
    counts_toward_limit,
    item_paths,
    place_item,
    write_item_metadata,
    write_tsv,
)
from tools.split_items_by_duration import TSV_FIELDS_V3
from v2_audio import (
    BUCKET_KEEP,
    BUCKET_REJECT,
    bucket_for_duration,
    read_wav_info,
    wav_problem,
)
from v2_naming import assign_file_stems, base_name, stem_matches_text

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "output"


def make_line(cue: str, text: str, **overrides) -> VoiceLine:
    values = {
        "script_path": "/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/3/0000/script",
        "line_no": 1,
        "voice_line_no": 2,
        "text": text,
        "speaker": "テスト",
        "voice_cue": cue,
        "bank_name": "sud_vo_adv_demo_001",
        "start": 1.0,
        "duration": 2.0,
        "voice_start": 1.0,
        "voice_duration": 4.0,
        "output_slug": cue,
    }
    values.update(overrides)
    return VoiceLine(**values)


def write_synthetic_wav(path: Path, seconds: float, *, sample_rate: int = 48000) -> None:
    """A minimal, structurally valid 16-bit mono WAV of the requested length."""
    samples = int(round(seconds * sample_rate))
    data = b"\x00\x00" * samples
    bits, channels = 16, 1
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body)


# ------------------------------------------------------------------ duration rules


def test_bounds_are_inclusive_at_both_ends():
    assert bucket_for_duration(3.0, 3.0, 10.0) == BUCKET_KEEP
    assert bucket_for_duration(10.0, 3.0, 10.0) == BUCKET_KEEP
    assert bucket_for_duration(4.1, 3.0, 10.0) == BUCKET_KEEP


def test_outside_the_bounds_is_rejected():
    assert bucket_for_duration(2.999, 3.0, 10.0) == BUCKET_REJECT
    assert bucket_for_duration(10.001, 3.0, 10.0) == BUCKET_REJECT
    assert bucket_for_duration(0.36, 3.0, 10.0) == BUCKET_REJECT


def test_read_wav_info_reads_the_real_duration(tmp_path: Path):
    path = tmp_path / "x.wav"
    write_synthetic_wav(path, 4.25)
    info = read_wav_info(path)
    assert info.seconds == pytest.approx(4.25, abs=1e-6)
    assert info.sample_rate == 48000
    assert info.channels == 1
    assert info.bits_per_sample == 16


def test_a_truncated_wav_is_rejected(tmp_path: Path):
    path = tmp_path / "truncated.wav"
    write_synthetic_wav(path, 4.0)
    data = path.read_bytes()
    path.write_bytes(data[:-1000])
    assert wav_problem(path) != ""
    with pytest.raises(Exception):
        read_wav_info(path)


def test_a_non_wav_file_is_rejected(tmp_path: Path):
    path = tmp_path / "nope.wav"
    path.write_bytes(b"not a wav at all" * 8)
    assert wav_problem(path) != ""


def test_wav_problem_is_empty_for_a_good_file(tmp_path: Path):
    path = tmp_path / "good.wav"
    write_synthetic_wav(path, 5.0)
    assert wav_problem(path) == ""


# --------------------------------------------------------------------------- naming


def test_plain_names_are_the_subtitle_itself():
    stems = assign_file_stems([("cue-a", "えっ？"), ("cue-b", "はい。")])
    assert stems == {"cue-a": "えっ？", "cue-b": "はい。"}


def test_second_cue_with_the_same_subtitle_gets_a_numbered_suffix():
    stems = assign_file_stems([("cue-b", "えっ？"), ("cue-a", "えっ？")])
    # ordered by voice_cue, so the alphabetically first cue keeps the bare name
    assert stems == {"cue-a": "えっ？", "cue-b": "えっ？-2"}


def test_suffixes_are_assigned_independently_of_input_order():
    entries = [("cue-c", "えっ？"), ("cue-a", "えっ？"), ("cue-b", "えっ？")]
    forward = assign_file_stems(entries)
    backward = assign_file_stems(list(reversed(entries)))
    assert forward == backward == {"cue-a": "えっ？", "cue-b": "えっ？-2", "cue-c": "えっ？-3"}


def test_newlines_become_underscores_not_spaces():
    stems = assign_file_stems([("cue-a", "前半。\n後半。")])
    assert stems == {"cue-a": "前半。_後半。"}


def test_a_literal_dash_two_in_the_subtitle_does_not_steal_a_suffix():
    stems = assign_file_stems([("cue-a", "テスト"), ("cue-b", "テスト-2"), ("cue-c", "テスト")])
    assert sorted(stems.values()) == ["テスト", "テスト-2", "テスト-2-2"]


def test_case_insensitive_clash_is_treated_as_a_clash():
    stems = assign_file_stems([("cue-a", "Test"), ("cue-b", "test")])
    assert len({value.casefold() for value in stems.values()}) == 2


def test_duplicate_keys_are_rejected():
    with pytest.raises(ValueError):
        assign_file_stems([("cue-a", "x"), ("cue-a", "y")])


def test_names_stay_short_and_legal_for_windows():
    stems = assign_file_stems([("cue-a", "a" * 400)])
    assert len(stems["cue-a"]) <= 255
    assert not set(stems["cue-a"]) & set('<>:"/\\|?*')


def test_stem_matches_text_accepts_bare_and_numbered_forms():
    assert stem_matches_text("はい。", "はい。")
    assert stem_matches_text("はい。-2", "はい。")
    assert not stem_matches_text("はい。-1", "はい。")
    assert not stem_matches_text("いいえ。", "はい。")


def test_base_name_falls_back_when_the_text_has_nothing_usable():
    assert base_name("   ", "cue-a") == "item"


# ------------------------------------------------------------------- collapsing


def test_duplicate_cues_collapse_and_keep_their_other_references():
    lines = [
        make_line("cue-a", "はい。", line_no=10),
        make_line("cue-b", "いいえ。", line_no=20),
        make_line("cue-a", "はい。", line_no=30, script_path="/other/script"),
    ]
    unique, extra = collapse_duplicate_cues(lines)
    assert [line.voice_cue for line in unique] == ["cue-a", "cue-b"]
    assert unique[0].line_no == 10
    assert [reference.line_no for reference in extra["cue-a"]] == [30]
    assert extra["cue-a"][0].script_path == "/other/script"


def test_collapsing_is_a_no_op_when_every_cue_is_unique():
    lines = [make_line("cue-a", "はい。"), make_line("cue-b", "いいえ。")]
    unique, extra = collapse_duplicate_cues(lines)
    assert len(unique) == 2
    assert extra == {}


# --------------------------------------------------------------- limit accounting


def test_limit_counts_only_lines_expected_to_land_in_keep():
    inside = make_line("cue-a", "x", voice_duration=4.0)
    too_short = make_line("cue-b", "y", voice_duration=1.0)
    too_long = make_line("cue-c", "z", voice_duration=12.0)
    assert counts_toward_limit(inside, 3.0, 10.0)
    assert not counts_toward_limit(too_short, 3.0, 10.0)
    assert not counts_toward_limit(too_long, 3.0, 10.0)


def test_limit_counts_lines_with_no_declared_duration():
    unknown = make_line("cue-a", "x", voice_duration=None)
    assert counts_toward_limit(unknown, 3.0, 10.0)


# ------------------------------------------------------------ item file rendering


def test_item_paths_live_in_the_bucket_directory(tmp_path: Path):
    keep_wav, keep_json = item_paths(tmp_path, BUCKET_KEEP, "えっ？")
    reject_wav, reject_json = item_paths(tmp_path, BUCKET_REJECT, "えっ？-2")
    assert keep_wav.parent.name == "keep"
    assert reject_wav.parent.name == "reject"
    assert keep_wav.name == "えっ？.wav"
    assert keep_json.name == "えっ？.json"
    assert reject_wav.name == "えっ？-2.wav"
    assert keep_wav.stem == keep_json.stem
    assert reject_wav.stem == reject_json.stem


def test_place_item_moves_the_decoded_file_into_its_bucket(tmp_path: Path):
    items_dir = tmp_path / "items"
    temp_path = items_dir / "_tmp_00001.wav"
    write_synthetic_wav(temp_path, 4.0)
    wav_path = place_item(temp_path=temp_path, items_dir=items_dir, bucket=BUCKET_KEEP, stem="はい。")
    assert wav_path == items_dir / "keep" / "はい。.wav"
    assert wav_path.exists()
    assert not temp_path.exists()


def test_metadata_records_duration_bucket_and_extra_references(tmp_path: Path):
    line = make_line("cue-a", "はい。")
    items_dir = tmp_path / "items"
    metadata_path = write_item_metadata(
        line=line,
        stem="はい。",
        position=1,
        subsong_index=7,
        seconds=4.1234,
        bucket=BUCKET_KEEP,
        items_dir=items_dir,
        acb_path=tmp_path / "acb" / "sud_vo_adv_demo_001.acb",
        scripts_dir=tmp_path / "scripts",
        extra_references=collapse_duplicate_cues([line, make_line("cue-a", "はい。", line_no=30)])[1]["cue-a"],
    )
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata_path.name == "はい。.json"
    assert metadata_path.parent.name == "keep"
    assert "subtitle_file" not in data
    assert data["voice_cue"] == "cue-a"
    assert data["duration_seconds"] == 4.123
    assert data["bucket"] == BUCKET_KEEP
    assert data["output_index"] == 1
    assert data["wav_file"].endswith("keep\\はい。.wav")
    assert data["metadata_file"].endswith("keep\\はい。.json")
    assert data["also_referenced_by"] == [{"script_path": line.script_path, "line_no": 30, "voice_line_no": 2}]


def test_metadata_omits_also_referenced_by_when_there_are_no_duplicates(tmp_path: Path):
    metadata_path = write_item_metadata(
        line=make_line("cue-a", "はい。"),
        stem="はい。",
        position=1,
        subsong_index=1,
        seconds=5.0,
        bucket=BUCKET_REJECT,
        items_dir=tmp_path / "items",
        acb_path=tmp_path / "acb" / "x.acb",
        scripts_dir=tmp_path / "scripts",
        extra_references=[],
    )
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "also_referenced_by" not in data
    assert data["bucket"] == BUCKET_REJECT


def test_tsv_has_duration_and_bucket_and_no_subtitle_file(tmp_path: Path):
    rows = [
        {
            "index": 1,
            "speaker": "テスト",
            "text": "前半。\\n後半。",
            "voice_cue": "cue-a",
            "bank_name": "sud_vo_adv_demo_001",
            "subsong_index": 3,
            "duration_seconds": "4.500",
            "bucket": BUCKET_KEEP,
            "wav_file": str(tmp_path / "items" / "keep" / "前半。_後半。.wav"),
            "metadata_file": str(tmp_path / "items" / "keep" / "前半。_後半。.json"),
            "script_path": "/data/remote/script",
            "line_no": 3,
            "voice_line_no": 4,
            "acb_path": "/data/remote/acb",
        }
    ]
    summary_path = write_tsv(output_root=tmp_path, character_slug="kotone", rows=rows)
    header, *body = summary_path.read_text(encoding="utf-8").splitlines()
    assert header.split("\t") == TSV_FIELDS_V2
    assert "subtitle_file" not in header
    assert summary_path.name == "kotone_lines.tsv"
    assert body[0].split("\t")[6] == "4.500"
    assert body[0].split("\t")[7] == BUCKET_KEEP


def test_extractor_and_splitter_agree_on_the_tsv_columns():
    assert TSV_FIELDS_V2 == TSV_FIELDS_V3


# ------------------------------------------------------- real migrated output


def migrated_items_dirs() -> list[Path]:
    if not OUTPUT.is_dir():
        return []
    return sorted({items for items in OUTPUT.rglob("items") if items.is_dir() and any(items.rglob("*.wav"))})


MIGRATED = migrated_items_dirs()


def item_wavs(items_dir: Path) -> list[Path]:
    found = list(items_dir.glob("*.wav"))
    for bucket in (BUCKET_KEEP, BUCKET_REJECT):
        directory = items_dir / bucket
        if directory.is_dir():
            found.extend(directory.glob("*.wav"))
    return sorted(found)


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_no_items_are_left_loose_in_the_items_directory():
    for items_dir in MIGRATED:
        assert not list(items_dir.glob("*.wav")), f"{items_dir} still has loose wavs"
        assert not list(items_dir.glob("*.json")), f"{items_dir} still has loose metadata"


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_replays_every_migrated_file_name():
    """Recompute every migrated file name from its metadata and compare."""
    checked = 0
    for items_dir in MIGRATED:
        entries: list[tuple[str, str]] = []
        actual: dict[str, str] = {}
        for wav_path in item_wavs(items_dir):
            metadata = json.loads(wav_path.with_suffix(".json").read_text(encoding="utf-8"))
            cue = metadata["voice_cue"]
            assert cue not in actual, f"duplicate voice cue {cue} in {items_dir}"
            entries.append((cue, metadata["text"]))
            actual[cue] = wav_path.stem
        recomputed = assign_file_stems(entries)
        for cue, expected_stem in recomputed.items():
            assert actual[cue] == expected_stem, (
                f"{items_dir.name}: cue {cue} is named {actual[cue]!r} but the rule says {expected_stem!r}"
            )
        checked += len(recomputed)
    assert checked == 1764, f"expected the 1764 migrated items, checked {checked}"


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_every_migrated_item_sits_in_the_right_bucket():
    """Re-measure every wav and check it is filed under the right bucket."""
    seen_keep = seen_reject = 0
    for items_dir in MIGRATED:
        for bucket in (BUCKET_KEEP, BUCKET_REJECT):
            directory = items_dir / bucket
            if not directory.is_dir():
                continue
            for wav_path in directory.glob("*.wav"):
                seconds = read_wav_info(wav_path).seconds
                expected = bucket_for_duration(seconds)
                assert expected == bucket, (
                    f"{wav_path} is {seconds:.3f}s so it belongs in {expected}\\"
                )
                metadata = json.loads(wav_path.with_suffix(".json").read_text(encoding="utf-8"))
                assert metadata["bucket"] == bucket
                assert abs(metadata["duration_seconds"] - seconds) < 0.002
                if bucket == BUCKET_KEEP:
                    seen_keep += 1
                else:
                    seen_reject += 1
    assert (seen_keep, seen_reject) == (1256, 508), f"got {seen_keep} keep / {seen_reject} reject"


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_every_migrated_item_is_paired_and_legal():
    for items_dir in MIGRATED:
        for bucket in (BUCKET_KEEP, BUCKET_REJECT):
            directory = items_dir / bucket
            if not directory.is_dir():
                continue
            wavs = {path.stem for path in directory.glob("*.wav")}
            jsons = {path.stem for path in directory.glob("*.json")}
            assert wavs == jsons, f"{directory} has unpaired items"
            for stem in wavs:
                metadata = json.loads((directory / f"{stem}.json").read_text(encoding="utf-8"))
                assert stem_matches_text(stem, metadata["text"], metadata["voice_cue"])
        assert not list(items_dir.glob("*_02_voice.wav"))
        assert not list(items_dir.rglob("*.txt"))


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_migrated_runs_do_not_left_over_v1_files():
    for items_dir in MIGRATED:
        run_root = items_dir.parent
        assert not list(run_root.rglob("subtitle.txt"))
        assert not list(run_root.rglob("*_01_subtitle.txt"))
        assert not list(run_root.rglob("*_03_metadata.json"))
        assert not list(run_root.rglob("voice.wav"))


@pytest.mark.skipif(not MIGRATED, reason="no migrated output/ present")
def test_migrated_tsv_lists_every_item_with_matching_bucket(tmp_path: Path):
    for items_dir in MIGRATED:
        run_root = items_dir.parent
        tsvs = sorted(run_root.glob("*_lines.tsv"))
        if not tsvs:
            continue
        rows = list(__import__("csv").DictReader(tsvs[0].open(encoding="utf-8"), delimiter="\t"))
        assert rows, f"{tsvs[0]} has no rows"
        placed = {path.stem: path.parent.name for path in item_wavs(items_dir)}
        assert len(rows) == len(placed)
        for row in rows:
            stem = Path(row["wav_file"]).stem
            assert stem in placed, f"{tsvs[0].name} lists {stem} which is not on disk"
            assert row["bucket"] == placed[stem]
            assert Path(row["wav_file"]).exists()
            assert Path(row["metadata_file"]).stem == stem
