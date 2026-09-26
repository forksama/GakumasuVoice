"""GakumasuVoice v2 extractor: subtitle-named voice files, split by duration.

STATUS -- NOT YET VALIDATED
---------------------------
**This module has never been executed end to end.**  It needs MuMu Player running
with the game's octo cache present, which was not available when it was written.
Everything in it that could be tested without the emulator (the naming rule, the
duplicate collapsing, the duration bucketing, the TSV and metadata rendering) is
covered by ``tests/test_gakumasu_voice_v2.py``.  The adb, ACB and vgmstream paths are
inherited unchanged from the validated v1 module.  See ``V2_NOTES.md``.

OUTPUT LAYOUT
-------------
::

    <run>\\items\\keep\\<subtitle>.wav       duration inside [min, max] seconds
    <run>\\items\\keep\\<subtitle>.json      metadata, same stem as the wav
    <run>\\items\\reject\\<subtitle>.wav     duration outside that range
    <run>\\items\\reject\\<subtitle>.json
    <run>\\<slug>_lines.tsv                  manifest, with duration_seconds and bucket

A subtitle shared by two different voice cues gets ``-2``, ``-3``, ... appended, so
the name stays readable while remaining unique (see :mod:`v2_naming`).  Durations
come from the decoded file, and the buckets keep everything: nothing is thrown away,
out-of-range voices just live in ``reject``.

v1 wrote ``items\\NNNN_<cue>_01_subtitle.txt`` / ``_02_voice.wav`` /
``_03_metadata.json`` with no bucketing.  Gone in v2: the separate subtitle.txt, the
numeric prefix, the cue name in the file name, and the flat items directory.

TWO BEHAVIOURS THAT DIFFER FROM v1
----------------------------------
1. **Duplicate cues collapse.**  When one ``voice_cue`` is referenced by several
   scripts, v1 exported it once per reference.  Those exports decode to identical
   bytes, so v2 writes one file and records the other references in the metadata
   under ``also_referenced_by``.  This also skips redundant vgmstream runs.
2. **``--limit N`` counts files expected to land in ``keep``.**  Rejected voices
   found while scanning are still exported into ``reject``, so a limited run yields
   about N keep files plus whatever rejects the scanned scripts contained.

USAGE
-----
    python gakumasu_voice_v2.py extract --character kotone --limit 200
    python gakumasu_voice_v2.py extract --character kotone --min-seconds 3 --max-seconds 10
    python gakumasu_voice_v2.py characters
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gakumasu_voice import (  # noqa: E402
    AndroidClient,
    BankCacheStats,
    BankPathCache,
    Progress,
    ToolError,
    VoiceLine,
    add_extract_arguments,
    build_subsong_map,
    character_help_text,
    collect_character_voice_lines,
    default_run_id,
    ensure_local_acb,
    install_elapsed_printing,
    locate_vgmstream,
    make_run_output_root,
    read_existing_voice_map,
    remote_tail,
    resolve_bank_path,
    resolve_character_selection,
    run_command,
    script_local_name,
    split_remote_paths,
    tsv_text,
)
from v2_audio import (  # noqa: E402
    BUCKET_KEEP,
    BUCKET_REJECT,
    BUCKETS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MIN_SECONDS,
    bucket_dir,
    bucket_for_duration,
    read_wav_info,
)
from v2_naming import assign_file_stems, base_name  # noqa: E402

TSV_FIELDS_V2 = [
    "index",
    "speaker",
    "text",
    "voice_cue",
    "bank_name",
    "subsong_index",
    "duration_seconds",
    "bucket",
    "wav_file",
    "metadata_file",
    "script_path",
    "line_no",
    "voice_line_no",
    "acb_path",
]

TEMP_WAV_PREFIX = "_tmp_"


@dataclass(frozen=True)
class ScriptReference:
    script_path: str
    line_no: int
    voice_line_no: int


def reference_of(line: VoiceLine) -> ScriptReference:
    return ScriptReference(script_path=line.script_path, line_no=line.line_no, voice_line_no=line.voice_line_no)


def collapse_duplicate_cues(
    lines: Iterable[VoiceLine],
) -> tuple[list[VoiceLine], dict[str, list[ScriptReference]]]:
    """Keep the first occurrence of each voice cue and collect the extra references.

    Order is preserved, so the surviving list stays in script/story order.
    """
    unique: dict[str, VoiceLine] = {}
    extra: dict[str, list[ScriptReference]] = {}
    for line in lines:
        if line.voice_cue in unique:
            extra.setdefault(line.voice_cue, []).append(reference_of(line))
            continue
        unique[line.voice_cue] = line
    return list(unique.values()), extra


def counts_toward_limit(line: VoiceLine, min_seconds: float, max_seconds: float) -> bool:
    """Whether ``--limit`` should count this line.

    Uses the duration declared by the script, which is known before decoding.  When
    the script gives no duration the line is counted, and the decoded file decides
    the bucket later.
    """
    declared = line.voice_duration
    if declared is None:
        return True
    return min_seconds <= declared <= max_seconds


def temp_wav_path(items_dir: Path, position: int) -> Path:
    return items_dir / f"{TEMP_WAV_PREFIX}{position:05d}.wav"


def item_paths(items_dir: Path, bucket: str, stem: str) -> tuple[Path, Path]:
    """Paths of a finished item inside its bucket directory."""
    directory = bucket_dir(items_dir, bucket)
    return directory / f"{stem}.wav", directory / f"{stem}.json"


def decode_subsong_to_temp(
    *,
    vgmstream: str,
    acb_path: Path,
    subsong_index: int,
    temp_path: Path,
) -> None:
    """Decode one subsong.

    The decode target is an ASCII temporary name so the vgmstream process never has
    to open a path containing Japanese text -- that keeps the output independent of
    the console code page and of how any given vgmstream build converts its command
    line.  The caller measures the result and then moves it into its bucket.
    """
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    run_command([vgmstream, "-s", str(subsong_index), "-o", str(temp_path), str(acb_path)])


def place_item(*, temp_path: Path, items_dir: Path, bucket: str, stem: str) -> Path:
    wav_path, _metadata_path = item_paths(items_dir, bucket, stem)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_path, wav_path)
    return wav_path


def write_item_metadata(
    *,
    line: VoiceLine,
    stem: str,
    position: int,
    subsong_index: int,
    seconds: float,
    bucket: str,
    items_dir: Path,
    acb_path: Path,
    scripts_dir: Path,
    extra_references: list[ScriptReference],
) -> Path:
    wav_path, metadata_path = item_paths(items_dir, bucket, stem)
    metadata = asdict(line)
    metadata.update(
        {
            "output_index": position,
            "subsong_index": subsong_index,
            "duration_seconds": round(seconds, 3),
            "bucket": bucket,
            "acb_file": str(acb_path),
            "script_file": str(scripts_dir / script_local_name(line.script_path)),
            "wav_file": str(wav_path),
            "metadata_file": str(metadata_path),
        }
    )
    # A v2 item has no subtitle file: the file name carries the subtitle.
    if extra_references:
        metadata["also_referenced_by"] = [asdict(reference) for reference in extra_references]
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def collect_limited_voice_lines_v2(
    *,
    client: AndroidClient,
    script_roots: Iterable[str],
    script_max_kb: int,
    character_code: str,
    line_limit: int,
    min_seconds: float,
    max_seconds: float,
    scripts_dir: Path,
    progress: bool,
) -> tuple[dict[str, str], list[VoiceLine], list[str], int, bool]:
    """Limited scan that counts keep candidates, but keeps every voice it sees."""
    script_texts: dict[str, str] = {}
    unique: dict[str, VoiceLine] = {}
    scripts_with_character_voice: list[str] = []
    seen_paths: set[str] = set()
    scanned_text_files = 0
    stopped_early = False
    pattern = f"{character_code}-"

    def keep_candidates() -> int:
        return sum(1 for line in unique.values() if counts_toward_limit(line, min_seconds, max_seconds))

    for remote_path in client.iter_text_files(script_roots, script_max_kb):
        if remote_path in seen_paths:
            continue
        seen_paths.add(remote_path)
        scanned_text_files += 1
        if progress and scanned_text_files % 100 == 0:
            print(f"limited scan: checked {scanned_text_files} text files, collected {keep_candidates()}/{line_limit}")

        if not client.text_file_contains(remote_path, pattern):
            continue

        scripts_with_character_voice.append(remote_path)
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")

        for line in collect_character_voice_lines(script_text, remote_path, character_code):
            unique.setdefault(line.voice_cue, line)
        if progress:
            print(f"limited scan: {remote_tail(remote_path)} -> {keep_candidates()}/{line_limit}")

        if keep_candidates() >= line_limit:
            stopped_early = True
            break

    return script_texts, list(unique.values()), scripts_with_character_voice, scanned_text_files, stopped_early


def collect_full_voice_lines_v2(
    *,
    client: AndroidClient,
    script_roots: Iterable[str],
    script_max_kb: int,
    character_code: str,
    character_name: str,
    full_name: str,
    scripts_dir: Path,
    progress: bool,
) -> tuple[dict[str, str], list[VoiceLine], list[str], list[str], list[str], list[str]]:
    voice_script_paths = client.grep_text_files(
        f"{character_code}-", script_roots, script_max_kb, label="search voice", progress=progress
    )
    name_script_paths = (
        client.grep_text_files(
            f"name={character_name}", script_roots, script_max_kb, label="search name", progress=progress
        )
        if character_name
        else []
    )
    speaker_script_paths = client.grep_text_files(
        f"img_adv_speaker_{character_code}", script_roots, script_max_kb, label="search speaker", progress=progress
    )
    full_name_script_paths = (
        client.grep_text_files(full_name, script_roots, script_max_kb, label="search full name", progress=progress)
        if full_name
        else []
    )
    all_script_paths = sorted(
        set(voice_script_paths) | set(name_script_paths) | set(speaker_script_paths) | set(full_name_script_paths)
    )

    script_texts: dict[str, str] = {}
    script_progress = Progress("pull scripts", len(all_script_paths), enabled=progress)
    for remote_path in all_script_paths:
        script_progress.show(remote_tail(remote_path))
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")
        script_progress.step(remote_tail(remote_path))
    script_progress.finish()

    collected: list[VoiceLine] = []
    parse_progress = Progress("parse scripts", len(script_texts), enabled=progress)
    for remote_path, script_text in script_texts.items():
        parse_progress.show(remote_tail(remote_path))
        collected.extend(collect_character_voice_lines(script_text, remote_path, character_code))
        parse_progress.step(remote_tail(remote_path))
    parse_progress.finish()

    return (
        script_texts,
        collected,
        voice_script_paths,
        name_script_paths,
        speaker_script_paths,
        full_name_script_paths,
    )


def write_tsv(*, output_root: Path, character_slug: str, rows: list[dict]) -> Path:
    summary_path = output_root / f"{base_name(character_slug)}_lines.tsv"
    with summary_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TSV_FIELDS_V2, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return summary_path


def extract_character_v2(args: argparse.Namespace) -> int:
    script_roots = split_remote_paths(args.script_root) if args.script_root else split_remote_paths(args.script_roots)
    if not script_roots:
        raise ToolError("no script roots configured")
    if args.script_max_kb <= 0:
        raise ToolError("--script-max-kb must be greater than 0")
    if args.limit < 0:
        raise ToolError("--limit must be 0 or greater")
    if args.min_seconds > args.max_seconds:
        raise ToolError("--min-seconds must not exceed --max-seconds")

    min_seconds = float(args.min_seconds)
    max_seconds = float(args.max_seconds)
    character_code, character_name, full_name, character_slug = resolve_character_selection(args)

    base_output = Path(args.output).resolve()
    line_limit = args.limit or 0
    run_id = args.run_id or default_run_id(line_limit)
    output_root = make_run_output_root(base_output, character_slug, run_id)
    bank_cache_path = (
        None if args.no_bank_cache else Path(args.bank_cache or (base_output / "_cache" / "bank_paths.json")).resolve()
    )
    shared_acb_dir = (
        None if args.no_acb_cache else Path(args.acb_cache_dir or (base_output / "_cache" / "acb")).resolve()
    )
    bank_cache = BankPathCache(bank_cache_path) if bank_cache_path else None
    bank_cache_stats = BankCacheStats()

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()
    vgmstream = locate_vgmstream(args.vgmstream)

    scripts_dir = output_root / "scripts"
    acb_dir = output_root / "acb"
    items_dir = output_root / "items"
    for directory in (scripts_dir, acb_dir, items_dir):
        directory.mkdir(parents=True, exist_ok=True)

    print("output layout: v2 (subtitle-named items, split into keep/ and reject/ by duration)")
    print(f"duration range: {min_seconds} <= seconds <= {max_seconds} (inclusive)")
    print(f"character: {character_slug} ({character_code})")
    if character_name or full_name:
        print(f"name filters: display={character_name or '-'} full={full_name or '-'}")
    print(f"output run: {output_root}")
    if bank_cache_path:
        print(f"bank cache: {bank_cache_path}")
    if shared_acb_dir:
        print(f"ACB cache: {shared_acb_dir}")

    limited_search = line_limit > 0
    scanned_text_file_count: int | None = None
    search_complete = True
    name_script_paths: list[str] = []
    speaker_script_paths: list[str] = []
    full_name_script_paths: list[str] = []

    if limited_search:
        print(f"limited extraction: stop after {line_limit} voice files expected in keep/")
        (
            _script_texts,
            collected,
            voice_script_paths,
            scanned_text_file_count,
            stopped_early,
        ) = collect_limited_voice_lines_v2(
            client=client,
            script_roots=script_roots,
            script_max_kb=args.script_max_kb,
            character_code=character_code,
            line_limit=line_limit,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            scripts_dir=scripts_dir,
            progress=args.progress,
        )
        search_complete = not stopped_early
        print(f"script roots: {len(script_roots)}")
        print(f"text files checked before stop: {scanned_text_file_count}")
        print(f"candidate scripts with {character_code}- voice: {len(voice_script_paths)}")
    else:
        (
            _script_texts,
            collected,
            voice_script_paths,
            name_script_paths,
            speaker_script_paths,
            full_name_script_paths,
        ) = collect_full_voice_lines_v2(
            client=client,
            script_roots=script_roots,
            script_max_kb=args.script_max_kb,
            character_code=character_code,
            character_name=character_name,
            full_name=full_name,
            scripts_dir=scripts_dir,
            progress=args.progress,
        )
        print(f"script roots: {len(script_roots)}")
        print(f"scripts with {character_code}- voice: {len(voice_script_paths)}")
        if character_name:
            print(f"scripts with name={character_name}: {len(name_script_paths)}")
        print(f"scripts with speaker image {character_code}: {len(speaker_script_paths)}")
        if full_name:
            print(f"scripts mentioning {full_name}: {len(full_name_script_paths)}")

    lines, extra_references = collapse_duplicate_cues(collected)
    stems = assign_file_stems((line.voice_cue, line.text) for line in lines)
    print(f"voice lines matched: {len(collected)}")
    print(f"unique voice cues: {len(lines)} (collapsed duplicates: {len(collected) - len(lines)})")

    bank_names = sorted({line.bank_name for line in lines})
    known_maps = read_existing_voice_map([Path(args.voice_map)] if args.voice_map else [])
    if not args.voice_map:
        temp_map = Path(os.environ.get("TEMP", "")) / "mumu_probe" / "sud_vo_map_clean.tsv"
        known_maps.update(read_existing_voice_map([temp_map]))

    bank_remote_paths: dict[str, str] = {}
    bank_local_paths: dict[str, Path] = {}
    bank_subsong_maps: dict[str, dict[str, int]] = {}

    for bank_index, bank_name in enumerate(bank_names, 1):
        print(f"bank {bank_index}/{len(bank_names)}: {bank_name}")
        remote_acb = resolve_bank_path(
            client,
            vgmstream,
            bank_name,
            args.octo_root,
            acb_dir,
            known_maps,
            bank_cache=bank_cache,
            stats=bank_cache_stats,
        )
        if bank_cache:
            bank_cache.save()
        bank_remote_paths[bank_name] = remote_acb
        local_acb = ensure_local_acb(
            client=client,
            remote_acb=remote_acb,
            bank_name=bank_name,
            run_acb_dir=acb_dir,
            shared_acb_dir=shared_acb_dir,
            stats=bank_cache_stats,
        )
        bank_local_paths[bank_name] = local_acb
        bank_subsong_maps[bank_name] = build_subsong_map(
            vgmstream,
            local_acb,
            label=f"scan subsongs {bank_index}/{len(bank_names)}",
            progress=args.progress,
        )

    for bucket in BUCKETS:
        bucket_dir(items_dir, bucket).mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []
    unmatched: list[VoiceLine] = []
    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    export_progress = Progress("export wav", len(lines), enabled=args.progress)

    for position, line in enumerate(lines, 1):
        export_progress.show(line.voice_cue)
        subsong_index = bank_subsong_maps.get(line.bank_name, {}).get(line.voice_cue)
        if not subsong_index:
            unmatched.append(line)
            export_progress.step(line.voice_cue)
            continue

        stem = stems[line.voice_cue]
        temp_path = temp_wav_path(items_dir, position)
        try:
            decode_subsong_to_temp(
                vgmstream=vgmstream,
                acb_path=bank_local_paths[line.bank_name],
                subsong_index=subsong_index,
                temp_path=temp_path,
            )
            info = read_wav_info(temp_path)
            bucket = bucket_for_duration(info.seconds, min_seconds, max_seconds)
            wav_path = place_item(temp_path=temp_path, items_dir=items_dir, bucket=bucket, stem=stem)
            metadata_path = write_item_metadata(
                line=line,
                stem=stem,
                position=position,
                subsong_index=subsong_index,
                seconds=info.seconds,
                bucket=bucket,
                items_dir=items_dir,
                acb_path=bank_local_paths[line.bank_name],
                scripts_dir=scripts_dir,
                extra_references=extra_references.get(line.voice_cue, []),
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        bucket_counts[bucket] += 1
        summary_rows.append(
            {
                "index": position,
                "speaker": line.speaker,
                "text": tsv_text(line.text),
                "voice_cue": line.voice_cue,
                "bank_name": line.bank_name,
                "subsong_index": subsong_index,
                "duration_seconds": f"{info.seconds:.3f}",
                "bucket": bucket,
                "wav_file": str(wav_path),
                "metadata_file": str(metadata_path),
                "script_path": line.script_path,
                "line_no": line.line_no,
                "voice_line_no": line.voice_line_no,
                "acb_path": bank_remote_paths.get(line.bank_name, ""),
            }
        )
        export_progress.step(line.voice_cue)
    export_progress.finish()

    summary_path = write_tsv(output_root=output_root, character_slug=character_slug, rows=summary_rows)

    durations = [float(row["duration_seconds"]) for row in summary_rows]
    coverage = {
        "output_layout": "v2",
        "layout_notes": (
            "items are named after the subtitle text, one metadata .json per wav, "
            "and split into items/keep and items/reject by duration"
        ),
        "tsv_fields": TSV_FIELDS_V2,
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "script_root": args.script_root or args.script_roots,
        "script_roots": script_roots,
        "script_max_kb": args.script_max_kb,
        "octo_root": args.octo_root,
        "output_base": str(base_output),
        "output_root": str(output_root),
        "items_dir": str(items_dir),
        "run_id": output_root.name,
        "character_slug": character_slug,
        "character_code": character_code,
        "character_name": character_name,
        "full_name": full_name,
        "line_limit": line_limit or None,
        "limited": limited_search,
        "search_complete": search_complete,
        "scanned_text_file_count": scanned_text_file_count,
        "candidate_script_count": len(voice_script_paths),
        "scripts_with_character_voice": voice_script_paths,
        "scripts_with_character_name": name_script_paths,
        "scripts_with_character_speaker_image": speaker_script_paths,
        "scripts_with_full_name_text": full_name_script_paths,
        "voice_line_count": len(collected),
        "unique_voice_cue_count": len(lines),
        "collapsed_duplicate_count": len(collected) - len(lines),
        "exported_count": len(summary_rows),
        "keep_count": bucket_counts[BUCKET_KEEP],
        "reject_count": bucket_counts[BUCKET_REJECT],
        "duration_seconds_min": min(durations) if durations else None,
        "duration_seconds_max": max(durations) if durations else None,
        "unmatched_count": len(unmatched),
        "bank_count": len(bank_names),
        "bank_remote_paths": bank_remote_paths,
        "bank_cache_path": str(bank_cache_path) if bank_cache_path else "",
        "acb_cache_dir": str(shared_acb_dir) if shared_acb_dir else "",
        "bank_cache_stats": asdict(bank_cache_stats),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (output_root / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    if unmatched:
        (output_root / "unmatched.json").write_text(
            json.dumps([asdict(line) for line in unmatched], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"voice files exported: {len(summary_rows)}")
    print(f"  {BUCKET_KEEP}: {bucket_counts[BUCKET_KEEP]}")
    print(f"  {BUCKET_REJECT}: {bucket_counts[BUCKET_REJECT]}")
    print(f"summary: {summary_path}")
    print(f"items: {items_dir}")
    print(f"bank cache stats: {asdict(bank_cache_stats)}")
    if unmatched:
        print(f"WARN: unmatched lines: {len(unmatched)}")
    return 0 if not unmatched else 2


def print_characters_v2(args: argparse.Namespace) -> int:
    print(character_help_text())
    return 0


def add_duration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=DEFAULT_MIN_SECONDS,
        help=f"Inclusive lower bound of the keep bucket (default {DEFAULT_MIN_SECONDS}).",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_SECONDS,
        help=f"Inclusive upper bound of the keep bucket (default {DEFAULT_MAX_SECONDS}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Gakumasu ADV voice lines as subtitle-named wav files, split by duration (v2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    subparsers = parser.add_subparsers(dest="command")

    extract = subparsers.add_parser(
        "extract",
        help="Extract a character; items are named after their subtitle and split into keep/reject.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    add_extract_arguments(extract)
    add_duration_arguments(extract)
    extract.set_defaults(func=extract_character_v2)

    characters = subparsers.add_parser("characters", help="List built-in character presets.")
    characters.set_defaults(func=print_characters_v2)
    return parser


def main(argv: list[str] | None = None) -> int:
    install_elapsed_printing()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
