"""Verify v2 GakumasuVoice output runs (subtitle-named items, split by duration).

STATUS
------
Run against the migrated runs under ``output/``.  See ``V2_NOTES.md`` for the
verified counts.  Nothing here needs the emulator.

A v2 run looks like this::

    <run>\\<slug>_lines.tsv
    <run>\\coverage.json
    <run>\\items\\keep\\<subtitle>.wav      duration inside [min, max] seconds
    <run>\\items\\keep\\<subtitle>.json
    <run>\\items\\reject\\<subtitle>.wav    duration outside that range
    <run>\\items\\reject\\<subtitle>.json
    <run>\\acb\\...
    <run>\\scripts\\...

WHAT IT CHECKS
--------------
per item
    * the wav is a structurally valid, non-truncated RIFF/WAVE file, and its real
      duration is read from the header
    * **the file sits in the bucket its measured duration belongs to** -- the core
      check for this layout, and the one that would catch a bad split
    * a metadata file exists with exactly the same stem
    * the metadata ``duration_seconds`` agrees with the measured duration, and its
      ``bucket`` agrees with the directory
    * the file stem equals ``safe_filename(metadata text)``, or that plus a ``-N``
      disambiguation suffix
pairing
    * every wav has a json and every json has a wav, in every bucket
    * no items are left loose in ``items\\`` itself
    * no two items in a run resolved to the same name
manifest
    * the TSV has ``duration_seconds`` and ``bucket`` and no v1 ``subtitle_file``
    * the TSV lists every item exactly once, with the duration and bucket that match
      where the file actually is

USAGE
-----
    python verify_outputs_v2.py                    # every run under output\\
    python verify_outputs_v2.py --output output\\temari
    python verify_outputs_v2.py --output output\\temari\\20260714_223748_limit100
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2_audio import (  # noqa: E402
    BUCKETS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MIN_SECONDS,
    bucket_for_duration,
    read_wav_info,
    wav_problem,
)
from v2_naming import stem_matches_text  # noqa: E402

DURATION_TOLERANCE = 0.002


class CheckResult:
    def __init__(self, run_root: Path):
        self.run_root = run_root
        self.items = 0
        self.keep = 0
        self.reject = 0
        self.problems: list[str] = []
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems


def find_run_roots(target: Path) -> list[Path]:
    if (target / "items").is_dir():
        return [target]
    roots = {items_dir.parent for items_dir in target.rglob("items") if items_dir.is_dir()}
    return sorted(roots)


def check_run(run_root: Path, *, deep: bool) -> CheckResult:
    result = CheckResult(run_root)
    items_dir = run_root / "items"

    loose = sorted(p.name for p in items_dir.glob("*.wav")) + sorted(p.name for p in items_dir.glob("*.json"))
    for name in loose:
        result.problems.append(f"items\\{name} is loose in items\\ instead of a bucket directory")

    measured: dict[str, float] = {}
    buckets: dict[str, str] = {}
    texts: dict[str, str] = {}
    stems_by_casefold: dict[str, str] = {}

    for bucket in BUCKETS:
        directory = items_dir / bucket
        if not directory.is_dir():
            result.warnings.append(f"items\\{bucket}\\ does not exist")
            continue
        wavs = {p.stem: p for p in directory.glob("*.wav")}
        jsons = {p.stem: p for p in directory.glob("*.json")}

        for stem in sorted(set(wavs) - set(jsons)):
            result.problems.append(f"items\\{bucket}\\{stem}.wav has no matching {stem}.json")
        for stem in sorted(set(jsons) - set(wavs)):
            result.problems.append(f"items\\{bucket}\\{stem}.json has no matching {stem}.wav")

        for stem, wav_path in sorted(wavs.items()):
            result.items += 1
            if bucket == "keep":
                result.keep += 1
            else:
                result.reject += 1

            folded = stem.casefold()
            if folded in stems_by_casefold:
                result.problems.append(
                    f"case-insensitive name clash: {stems_by_casefold[folded]!r} vs {stem!r}"
                )
            stems_by_casefold[folded] = stem

            if wav_path.stat().st_size == 0:
                result.problems.append(f"{bucket}\\{wav_path.name} is empty")
                continue
            if deep:
                reason = wav_problem(wav_path)
                if reason:
                    result.problems.append(f"{bucket}\\{wav_path.name}: {reason}")
                    continue
            try:
                info = read_wav_info(wav_path)
            except Exception as exc:  # noqa: BLE001 - surfaced as a problem
                result.problems.append(f"{bucket}\\{wav_path.name}: {exc}")
                continue
            measured[stem] = info.seconds
            buckets[stem] = bucket

            metadata_path = jsons.get(stem)
            if metadata_path is None:
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                result.problems.append(f"{bucket}\\{metadata_path.name}: unreadable ({exc})")
                continue

            text = str(metadata.get("text", ""))
            texts[stem] = text
            if not stem_matches_text(stem, text, str(metadata.get("voice_cue", ""))):
                result.problems.append(f"{bucket}\\{wav_path.name}: name does not match its subtitle text")

            recorded = metadata.get("duration_seconds")
            if not isinstance(recorded, (int, float)):
                result.problems.append(f"{bucket}\\{metadata_path.name}: missing duration_seconds")
            elif abs(float(recorded) - info.seconds) > DURATION_TOLERANCE:
                result.problems.append(
                    f"{bucket}\\{metadata_path.name}: duration_seconds {recorded} != measured {info.seconds:.3f}"
                )

            recorded_bucket = metadata.get("bucket")
            if recorded_bucket != bucket:
                result.problems.append(
                    f"{bucket}\\{metadata_path.name}: metadata says bucket={recorded_bucket!r}"
                )

            for key in ("voice_cue", "bank_name", "subsong_index"):
                if not metadata.get(key) and metadata.get(key) != 0:
                    result.warnings.append(f"{stem}.json is missing {key}")

    # group suffix numbering must be contiguous from the bare name
    groups: dict[str, list[int]] = {}
    for stem in measured:
        head, _sep, tail = stem.rpartition("-")
        if head and tail.isdigit() and int(tail) >= 2:
            groups.setdefault(head, []).append(int(tail))
        else:
            groups.setdefault(stem, []).append(1)
    for base, numbers in sorted(groups.items()):
        if len(numbers) == 1:
            continue
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            result.warnings.append(f"{base!r}: disambiguation numbers {sorted(numbers)} are not contiguous")

    tsv_paths = sorted(run_root.glob("*_lines.tsv"))
    if not tsv_paths:
        result.warnings.append("no *_lines.tsv manifest found")
        return result
    tsv_path = tsv_paths[0]
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "subtitle_file" in fieldnames:
        result.problems.append(f"{tsv_path.name} still has the v1 subtitle_file column")
    for required in ("wav_file", "metadata_file", "text", "voice_cue", "duration_seconds", "bucket"):
        if required not in fieldnames:
            result.problems.append(f"{tsv_path.name} is missing the {required} column")
    if len(rows) != result.items:
        result.problems.append(f"{tsv_path.name} has {len(rows)} rows but items\\ holds {result.items} wav files")

    listed: set[str] = set()
    for row in rows:
        wav_value = row.get("wav_file", "")
        if not wav_value:
            result.problems.append("a TSV row has an empty wav_file")
            continue
        wav_path = Path(wav_value)
        stem = wav_path.stem
        listed.add(stem)
        if not wav_path.exists():
            result.problems.append(f"TSV points at a missing wav: {wav_path}")
            continue
        if Path(row.get("metadata_file", "")).stem != stem:
            result.problems.append(f"TSV metadata_file does not share the stem of {wav_path.name}")
        actual_bucket = wav_path.parent.name
        if actual_bucket != row.get("bucket"):
            result.problems.append(f"{wav_path.name}: TSV bucket {row.get('bucket')!r} != directory {actual_bucket!r}")
        if stem in measured:
            recorded = row.get("duration_seconds", "")
            try:
                if abs(float(recorded) - measured[stem]) > DURATION_TOLERANCE:
                    result.problems.append(
                        f"{wav_path.name}: TSV duration {recorded} != measured {measured[stem]:.3f}"
                    )
            except ValueError:
                result.problems.append(f"{wav_path.name}: TSV duration_seconds {recorded!r} is not a number")
        actual_text = texts.get(stem)
        expected_text = row.get("text", "").replace("\\n", "\n")
        if actual_text is not None and actual_text != expected_text:
            result.problems.append(f"{wav_path.name}: TSV text disagrees with metadata text")

    for stem in sorted(set(measured) - listed):
        result.problems.append(f"items item {stem!r} is not listed in {tsv_path.name}")

    return result


def resolve_bounds(run_root: Path, default: tuple[float, float]) -> tuple[float, float]:
    """The keep bounds this run was built with.

    Read from the run's ``coverage.json``.  When that is absent the caller's default
    is used and a warning is raised, so a missing record never silently disables the
    bucket check.
    """
    coverage = run_root / "coverage.json"
    if not coverage.exists():
        return default
    try:
        data = json.loads(coverage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    low = data.get("min_seconds")
    high = data.get("max_seconds")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return float(low), float(high)
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify v2 GakumasuVoice output runs.")
    parser.add_argument("--output", default="output", help="Run directory, character directory, or output base.")
    parser.add_argument("--no-deep", action="store_true", help="Skip the structural WAV check.")
    parser.add_argument("--min-seconds", type=float, default=None, help="Override the keep lower bound.")
    parser.add_argument("--max-seconds", type=float, default=None, help="Override the keep upper bound.")
    args = parser.parse_args(argv)

    target = Path(args.output).resolve()
    if not target.exists():
        print(f"ERROR: {target} does not exist", file=sys.stderr)
        return 2

    run_roots = find_run_roots(target)
    if not run_roots:
        print(f"ERROR: no items directory found under {target}", file=sys.stderr)
        return 2

    results = [check_run(run_root, deep=not args.no_deep) for run_root in run_roots]

    # Bucket placement is re-checked with the bounds recorded by the run itself.
    assumed_bounds = (DEFAULT_MIN_SECONDS, DEFAULT_MAX_SECONDS)
    for result in results:
        bounds = resolve_bounds(result.run_root, assumed_bounds)
        if args.min_seconds is not None:
            bounds = (args.min_seconds, bounds[1])
        if args.max_seconds is not None:
            bounds = (bounds[0], args.max_seconds)
        low, high = bounds
        if not (result.run_root / "coverage.json").exists():
            result.warnings.append(
                f"no coverage.json: bucket placement checked against the assumed range {low}-{high}s"
            )
        items_dir = result.run_root / "items"
        for bucket in BUCKETS:
            directory = items_dir / bucket
            if not directory.is_dir():
                continue
            for wav_path in sorted(directory.glob("*.wav")):
                try:
                    seconds = read_wav_info(wav_path).seconds
                except Exception:  # noqa: BLE001 - already reported by check_run
                    continue
                expected = bucket_for_duration(seconds, low, high)
                if expected != bucket:
                    result.problems.append(
                        f"{bucket}\\{wav_path.name}: {seconds:.3f}s belongs in {expected}\\ (bounds {low}-{high})"
                    )

    total_items = sum(result.items for result in results)
    total_keep = sum(result.keep for result in results)
    total_reject = sum(result.reject for result in results)
    total_problems = sum(len(result.problems) for result in results)
    total_warnings = sum(len(result.warnings) for result in results)

    for result in results:
        try:
            name = str(result.run_root.relative_to(target))
        except ValueError:
            name = str(result.run_root)
        state = "OK" if result.ok else "FAIL"
        print(f"[{state}] {name or '.'}  items={result.items} keep={result.keep} reject={result.reject}")
        for problem in result.problems[:20]:
            print(f"         PROBLEM: {problem}")
        if len(result.problems) > 20:
            print(f"         ... {len(result.problems) - 20} more problems")
        for warning in result.warnings[:5]:
            print(f"         warn: {warning}")

    print()
    print(
        f"runs={len(results)} items={total_items} keep={total_keep} reject={total_reject} "
        f"problems={total_problems} warnings={total_warnings}"
    )
    return 0 if total_problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
