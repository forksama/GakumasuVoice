"""Second v2 migration: split each run's items into duration buckets.

STATUS
------
Run and verified against the migrated runs present under ``output/``.

WHAT IT DOES
------------
Turns this::

    <run>\\items\\<subtitle>.wav
    <run>\\items\\<subtitle>.json

into this::

    <run>\\items\\keep\\<subtitle>.wav      duration within [min, max] seconds
    <run>\\items\\reject\\<subtitle>.wav    duration outside that range
    <run>\\items\\keep\\<subtitle>.json
    <run>\\items\\reject\\<subtitle>.json

Each wav's duration is read from its own WAV header.  The metadata gains
``duration_seconds`` and ``bucket``, and its ``wav_file`` / ``metadata_file`` are
repointed into the bucket directory.  The run's ``*_lines.tsv`` is rewritten with two
extra columns, ``duration_seconds`` and ``bucket``.

Bounds are inclusive at both ends: ``min <= seconds <= max`` is ``keep``.  The
defaults come from :mod:`v2_audio`.

RE-RUNNABLE
-----------
The tool discovers items wherever they currently live -- loose in ``items\\`` or
already inside a bucket directory -- and recomputes each bucket from the measured
duration.  So:

* re-running with the same bounds repairs stale metadata and moves nothing;
* re-running with different bounds **re-buckets** the existing files;
* a run that is fully consistent is reported as "nothing to split".

Rows in the TSV keep their original order (the extraction order) so the manifest
stays readable as a story; the ``bucket`` column carries the classification, which
means rows of one bucket are not contiguous.

Only files move.  Nothing is re-decoded, and no metadata is dropped.

USAGE
-----
    python tools\\split_items_by_duration.py                     # dry run
    python tools\\split_items_by_duration.py --apply
    python tools\\split_items_by_duration.py --apply --min-seconds 2 --max-seconds 8
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2_audio import (  # noqa: E402
    BUCKET_KEEP,
    BUCKET_REJECT,
    BUCKETS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MIN_SECONDS,
    WavFormatError,
    bucket_dir,
    bucket_for_duration,
    read_wav_info,
)

TSV_FIELDS_V3 = [
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


class SplitError(RuntimeError):
    pass


@dataclass
class Item:
    items_dir: Path
    stem: str
    wav_path: Path
    metadata_path: Path
    metadata: dict
    seconds: float
    bucket: str

    @property
    def target_wav(self) -> Path:
        return bucket_dir(self.items_dir, self.bucket) / f"{self.stem}.wav"

    @property
    def target_metadata(self) -> Path:
        return bucket_dir(self.items_dir, self.bucket) / f"{self.stem}.json"

    @property
    def needs_move(self) -> bool:
        return self.wav_path != self.target_wav

    @property
    def needs_metadata_rewrite(self) -> bool:
        # Compare against the exact value render_metadata writes.  A tolerance equal
        # to the rounding step would flag every item whose duration lands exactly on
        # a millisecond boundary (0.4565 rounds to 0.457, a gap of exactly 0.0005),
        # so the tool would never report itself finished.
        recorded = self.metadata.get("duration_seconds")
        if not isinstance(recorded, (int, float)) or recorded != round(self.seconds, 3):
            return True
        if self.metadata.get("bucket") != self.bucket:
            return True
        if self.metadata.get("wav_file") != str(self.target_wav):
            return True
        if self.metadata.get("metadata_file") != str(self.target_metadata):
            return True
        return False


@dataclass
class RunPlan:
    run_root: Path
    items_dir: Path
    items: list[Item] = field(default_factory=list)
    tsv_path: Path | None = None
    tsv_rows_before: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def keep_count(self) -> int:
        return sum(1 for item in self.items if item.bucket == BUCKET_KEEP)

    @property
    def reject_count(self) -> int:
        return sum(1 for item in self.items if item.bucket == BUCKET_REJECT)

    @property
    def moves(self) -> list[Item]:
        return [item for item in self.items if item.needs_move]

    @property
    def metadata_rewrites(self) -> list[Item]:
        return [item for item in self.items if item.needs_metadata_rewrite]

    @property
    def needs_work(self) -> bool:
        return bool(self.moves or self.metadata_rewrites)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SplitError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SplitError(f"not a JSON object: {path}")
    return data


def discover_item_pairs(items_dir: Path) -> list[tuple[Path, Path]]:
    """Find (wav, metadata) pairs wherever they live under ``items_dir``."""
    pairs: list[tuple[Path, Path]] = []
    wavs = list(items_dir.glob("*.wav"))
    for bucket in BUCKETS:
        directory = bucket_dir(items_dir, bucket)
        if directory.is_dir():
            wavs.extend(directory.glob("*.wav"))
    for wav_path in sorted(wavs):
        metadata_path = wav_path.with_suffix(".json")
        if not metadata_path.exists():
            raise SplitError(f"{wav_path.name} has no matching metadata")
        pairs.append((wav_path, metadata_path))
    return pairs


def read_existing_tsv(run_root: Path) -> tuple[Path | None, list[dict[str, str]]]:
    candidates = sorted(run_root.glob("*_lines.tsv"))
    if not candidates:
        return None, []
    tsv_path = candidates[0]
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        return tsv_path, list(csv.DictReader(fh, delimiter="\t"))


def build_run_plan(
    run_root: Path,
    *,
    min_seconds: float,
    max_seconds: float,
) -> RunPlan | None:
    items_dir = run_root / "items"
    if not items_dir.is_dir():
        return None
    pairs = discover_item_pairs(items_dir)
    if not pairs:
        return None

    plan = RunPlan(run_root=run_root, items_dir=items_dir)
    for wav_path, metadata_path in pairs:
        try:
            info = read_wav_info(wav_path)
        except WavFormatError as exc:
            raise SplitError(f"cannot measure {wav_path.name}: {exc}") from exc
        plan.items.append(
            Item(
                items_dir=items_dir,
                stem=wav_path.stem,
                wav_path=wav_path,
                metadata_path=metadata_path,
                metadata=read_json(metadata_path),
                seconds=info.seconds,
                bucket=bucket_for_duration(info.seconds, min_seconds, max_seconds),
            )
        )

    # Report declared-vs-measured drift, which would mean a bound sits on a knife edge.
    for item in plan.items:
        declared = item.metadata.get("voice_duration")
        if isinstance(declared, (int, float)):
            declared_bucket = bucket_for_duration(float(declared), min_seconds, max_seconds)
            if declared_bucket != item.bucket:
                plan.warnings.append(
                    f"{item.stem}: measured {item.seconds:.3f}s ({item.bucket}) but the script declared "
                    f"{float(declared):.3f}s ({declared_bucket})"
                )

    if not plan.needs_work:
        return None

    plan.tsv_path, rows = read_existing_tsv(run_root)
    plan.tsv_rows_before = len(rows)
    return plan


def render_metadata(item: Item) -> str:
    data = dict(item.metadata)
    data["duration_seconds"] = round(item.seconds, 3)
    data["bucket"] = item.bucket
    data["wav_file"] = str(item.target_wav)
    data["metadata_file"] = str(item.target_metadata)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def render_tsv(plan: RunPlan) -> str:
    by_cue = {str(item.metadata.get("voice_cue", "")): item for item in plan.items}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TSV_FIELDS_V3, delimiter="\t", lineterminator="\n")
    writer.writeheader()

    rows: list[dict[str, str]] = []
    if plan.tsv_path is not None:
        with plan.tsv_path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))

    def emit(row: dict, item: Item) -> None:
        writer.writerow(
            {
                "index": row.get("index", ""),
                "speaker": row.get("speaker", item.metadata.get("speaker", "")),
                "text": row.get("text", item.metadata.get("text", "")),
                "voice_cue": row.get("voice_cue", item.metadata.get("voice_cue", "")),
                "bank_name": row.get("bank_name", item.metadata.get("bank_name", "")),
                "subsong_index": row.get("subsong_index", item.metadata.get("subsong_index", "")),
                "duration_seconds": f"{item.seconds:.3f}",
                "bucket": item.bucket,
                "wav_file": str(item.target_wav),
                "metadata_file": str(item.target_metadata),
                "script_path": row.get("script_path", item.metadata.get("script_path", "")),
                "line_no": row.get("line_no", item.metadata.get("line_no", "")),
                "voice_line_no": row.get("voice_line_no", item.metadata.get("voice_line_no", "")),
                "acb_path": row.get("acb_path", ""),
            }
        )

    # Keep the original row order so the manifest still reads as a story.
    written: set[str] = set()
    for row in rows:
        cue = row.get("voice_cue", "")
        item = by_cue.get(cue)
        if item is None or cue in written:
            continue
        written.add(cue)
        emit(row, item)

    # Any item the old manifest did not list still has to appear.
    for cue, item in by_cue.items():
        if cue in written:
            continue
        plan.warnings.append(f"{item.stem}: not present in the old TSV; appended")
        emit({}, item)
    return buffer.getvalue()


def apply_plan(plan: RunPlan) -> None:
    items_dir = plan.items_dir
    for bucket in BUCKETS:
        bucket_dir(items_dir, bucket).mkdir(parents=True, exist_ok=True)

    # Move wav first, then rewrite metadata at its destination.
    for item in plan.moves:
        if item.target_wav.exists():
            raise SplitError(f"refusing to overwrite {item.target_wav}")
        item.wav_path.replace(item.target_wav)

    for item in plan.items:
        item.target_metadata.write_text(render_metadata(item), encoding="utf-8")
        if item.metadata_path.exists() and item.metadata_path != item.target_metadata:
            item.metadata_path.unlink()

    if plan.tsv_path is not None:
        plan.tsv_path.write_text(render_tsv(plan), encoding="utf-8", newline="")

    assert_split_is_complete(plan)


def assert_split_is_complete(plan: RunPlan) -> None:
    """Fail loudly rather than leaving a half-split items directory."""
    stray = sorted(p.name for p in plan.items_dir.glob("*.wav"))
    stray += sorted(p.name for p in plan.items_dir.glob("*.json"))
    if stray:
        raise SplitError(f"{plan.items_dir} still holds {len(stray)} unsplit item(s): {stray[:5]}")

    moved = 0
    for bucket in BUCKETS:
        directory = bucket_dir(plan.items_dir, bucket)
        if not directory.is_dir():
            continue
        wavs = {p.stem for p in directory.glob("*.wav")}
        jsons = {p.stem for p in directory.glob("*.json")}
        if wavs != jsons:
            raise SplitError(f"{directory} has unpaired items")
        expected = {item.stem for item in plan.items if item.bucket == bucket}
        if wavs != expected:
            raise SplitError(f"{directory} holds {len(wavs)} items but the plan expected {len(expected)}")
        moved += len(wavs)
    if moved != len(plan.items):
        raise SplitError(f"placed {moved} items but the plan had {len(plan.items)}")


def iter_run_roots(root: Path) -> list[Path]:
    return sorted({items_dir.parent for items_dir in root.rglob("items") if items_dir.is_dir()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Split v2 items into duration buckets (keep / reject).")
    parser.add_argument("--root", default="output")
    parser.add_argument("--apply", action="store_true", help="Perform the split; without it only prints the plan.")
    parser.add_argument("--min-seconds", type=float, default=DEFAULT_MIN_SECONDS)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--report", default="", help="Write a JSON report to this path.")
    args = parser.parse_args(argv)

    if args.min_seconds > args.max_seconds:
        print("ERROR: --min-seconds must not exceed --max-seconds", file=sys.stderr)
        return 1

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 1

    plans: list[RunPlan] = []
    skipped: list[Path] = []
    for run_root in iter_run_roots(root):
        plan = build_run_plan(run_root, min_seconds=args.min_seconds, max_seconds=args.max_seconds)
        if plan is None:
            if (run_root / "items").is_dir():
                skipped.append(run_root)
            continue
        plans.append(plan)

    if not plans:
        print(f"nothing to split (every items directory is already consistent for {args.min_seconds}-{args.max_seconds}s)")
        return 0

    total = sum(len(plan.items) for plan in plans)
    keep = sum(plan.keep_count for plan in plans)
    reject = sum(plan.reject_count for plan in plans)
    moves = sum(len(plan.moves) for plan in plans)
    rewrites = sum(len(plan.metadata_rewrites) for plan in plans)
    warnings = [(plan, warning) for plan in plans for warning in plan.warnings]

    print(f"mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"range: {args.min_seconds} <= seconds <= {args.max_seconds} (inclusive)")
    print(f"items directories touched: {len(plans)}")
    print(f"items: {total}   keep: {keep}   reject: {reject}")
    print(f"files to move: {moves}   metadata to rewrite: {rewrites}")
    print()
    print(f"{'run':<44} {'items':>6} {'keep':>6} {'reject':>7} {'move':>6} {'meta':>6}")
    for plan in plans:
        try:
            name = str(plan.run_root.relative_to(root))
        except ValueError:
            name = str(plan.run_root)
        print(
            f"{name:<44} {len(plan.items):>6} {plan.keep_count:>6} {plan.reject_count:>7} "
            f"{len(plan.moves):>6} {len(plan.metadata_rewrites):>6}"
        )

    if warnings:
        print()
        for plan, warning in warnings[:20]:
            print(f"WARN [{plan.run_root.name}]: {warning}")
        if len(warnings) > 20:
            print(f"... {len(warnings) - 20} more warnings")

    if skipped:
        print()
        print(f"already consistent (skipped): {len(skipped)}")
        for run_root in skipped:
            print(f"    {run_root}")

    if args.apply:
        print()
        for plan in plans:
            apply_plan(plan)
            print(f"split: {plan.run_root}  keep={plan.keep_count} reject={plan.reject_count}")
    else:
        print()
        print("dry run only; re-run with --apply to split")

    if args.report:
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "applied": bool(args.apply),
            "root": str(root),
            "min_seconds": args.min_seconds,
            "max_seconds": args.max_seconds,
            "items": total,
            "keep": keep,
            "reject": reject,
            "files_moved": moves,
            "metadata_rewritten": rewrites,
            "runs": [
                {
                    "run_root": str(plan.run_root),
                    "items": len(plan.items),
                    "keep": plan.keep_count,
                    "reject": plan.reject_count,
                    "files_moved": len(plan.moves),
                    "metadata_rewritten": len(plan.metadata_rewrites),
                    "tsv_path": str(plan.tsv_path) if plan.tsv_path else "",
                    "warnings": plan.warnings,
                    "items_detail": [
                        {"stem": item.stem, "seconds": round(item.seconds, 3), "bucket": item.bucket}
                        for item in plan.items
                    ],
                }
                for plan in plans
            ],
            "skipped": [str(path) for path in skipped],
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
