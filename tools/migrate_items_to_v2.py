"""Migrate legacy GakumasuVoice ``items`` directories to the v2 subtitle-named layout.

STATUS
------
This tool was run and verified against the runs present under ``output/`` at
migration time (17 run directories, 1787 wav files).  See ``V2_NOTES.md`` for the
verified counts and for what is still unverified.

WHAT IT DOES
------------
v1 layouts (both generations):

    items\\0001_<voice_cue>_01_subtitle.txt
    items\\0001_<voice_cue>_02_voice.wav
    items\\0001_<voice_cue>_03_metadata.json

    items\\<voice_cue>\\subtitle.txt          (oldest, dir-per-item generation)
    items\\<voice_cue>\\voice.wav
    items\\<voice_cue>\\metadata.json

v2 layout:

    items\\<sanitized subtitle>.wav
    items\\<sanitized subtitle>.json          (same stem as the wav)
    items\\<sanitized subtitle>-2.wav         (only when two different cues share one text)
    items\\<sanitized subtitle>-2.json

    <run>\\<slug>_lines.tsv                   (rewritten; subtitle_file column dropped)

NAMING RULE
-----------
1. ``base = safe_filename(metadata["text"])``
2. Items are grouped by ``base``; groups are ordered by ``casefold(base)``.
3. Inside a group, items are ordered by ``voice_cue`` ascending.
4. The first item takes the bare ``base``; later ones take ``base-2``, ``base-3``,
   ...  Names already used are skipped, compared case-insensitively because NTFS
   is case-insensitive.

Steps 2-4 make the names reproducible: a later re-extraction reproduces the same
file names no matter what order the device returned scripts in.

ITEMS THAT COLLAPSE
-------------------
When one ``voice_cue`` appears more than once in a run (the same cue referenced by
several scripts), v1 exported it once per reference.  Those copies decode to
identical bytes, so v2 keeps a single file and records the extra references in the
surviving metadata as ``also_referenced_by``.  Nothing else is dropped.

USAGE
-----
    python tools\\migrate_items_to_v2.py                    # dry run, prints the plan
    python tools\\migrate_items_to_v2.py --apply            # perform the migration
    python tools\\migrate_items_to_v2.py --apply --verbose  # also list every rename
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gakumasu_voice import safe_filename, tsv_text  # noqa: E402
from v2_naming import assign_file_stems  # noqa: E402

FLAT_WAV_RE = re.compile(r"^(?P<index>\d+)_(?P<cue>.+)_02_voice\.wav$")

TSV_FIELDS_V2 = [
    "index",
    "speaker",
    "text",
    "voice_cue",
    "bank_name",
    "subsong_index",
    "wav_file",
    "metadata_file",
    "script_path",
    "line_no",
    "voice_line_no",
    "acb_path",
]

METADATA_DROP_FIELDS = ("subtitle_file",)


class MigrationError(RuntimeError):
    pass


@dataclass
class ItemRecord:
    """One exported voice item found in a v1 items directory."""

    cue: str
    text: str
    wav_path: Path
    metadata_path: Path
    subtitle_path: Path | None
    metadata: dict
    legacy_dir: Path | None = None
    old_index: int = 0
    sibling_metadata_paths: list[Path] = field(default_factory=list)
    v2_stem: str = ""
    v2_position: int = 0


@dataclass
class RunPlan:
    run_root: Path
    items_dir: Path
    layout: str
    items_found: int = 0
    unique_items: int = 0
    collapsed: int = 0
    survivors: list[ItemRecord] = field(default_factory=list)
    renames: list[tuple[Path, Path]] = field(default_factory=list)
    deletions: list[Path] = field(default_factory=list)
    removed_dirs: list[Path] = field(default_factory=list)
    tsv_path: Path | None = None
    tsv_rows_before: int = 0
    tsv_rows_after: int = 0
    warnings: list[str] = field(default_factory=list)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"not a JSON object: {path}")
    return data


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def subtitle_text(path: Path | None) -> str:
    """Return the subtitle without its single trailing newline."""
    if path is None or not path.exists():
        return ""
    value = path.read_text(encoding="utf-8", errors="replace")
    return value[:-1] if value.endswith("\n") else value


def collect_flat_items(items_dir: Path) -> list[ItemRecord]:
    records: list[ItemRecord] = []
    for wav_path in sorted(items_dir.glob("*_02_voice.wav")):
        match = FLAT_WAV_RE.match(wav_path.name)
        if not match:
            continue
        prefix = f"{match.group('index')}_{match.group('cue')}"
        metadata_path = items_dir / f"{prefix}_03_metadata.json"
        subtitle_path = items_dir / f"{prefix}_01_subtitle.txt"
        if not metadata_path.exists():
            raise MigrationError(f"missing metadata for {wav_path.name}")
        metadata = read_json(metadata_path)
        records.append(
            ItemRecord(
                cue=str(metadata.get("voice_cue") or match.group("cue")),
                text=str(metadata.get("text") or subtitle_text(subtitle_path)),
                wav_path=wav_path,
                metadata_path=metadata_path,
                subtitle_path=subtitle_path if subtitle_path.exists() else None,
                metadata=metadata,
                old_index=int(match.group("index")),
            )
        )
    return records


def collect_legacy_items(items_dir: Path) -> list[ItemRecord]:
    records: list[ItemRecord] = []
    for item_dir in sorted(p for p in items_dir.iterdir() if p.is_dir()):
        wav_path = item_dir / "voice.wav"
        metadata_path = item_dir / "metadata.json"
        if not wav_path.exists() or not metadata_path.exists():
            continue
        subtitle_path = item_dir / "subtitle.txt"
        metadata = read_json(metadata_path)
        records.append(
            ItemRecord(
                cue=str(metadata.get("voice_cue") or item_dir.name),
                text=str(metadata.get("text") or subtitle_text(subtitle_path)),
                wav_path=wav_path,
                metadata_path=metadata_path,
                subtitle_path=subtitle_path if subtitle_path.exists() else None,
                metadata=metadata,
                legacy_dir=item_dir,
            )
        )
    return records


def looks_like_v2(items_dir: Path) -> bool:
    """True when the directory already uses the v2 naming."""
    if any(items_dir.glob("*_02_voice.wav")):
        return False
    if any(p.is_dir() and (p / "voice.wav").exists() for p in items_dir.iterdir()):
        return False
    return any(items_dir.glob("*.wav"))


def read_legacy_tsv(run_root: Path) -> tuple[Path | None, list[dict[str, str]]]:
    candidates = sorted(run_root.glob("*_lines.tsv"))
    if not candidates:
        return None, []
    tsv_path = candidates[0]
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    return tsv_path, rows


def read_remote_acb_map(run_root: Path) -> dict[str, str]:
    """bank_name -> remote ACB path, from coverage.json (v1 or legacy reports/)."""
    for candidate in (run_root / "coverage.json", run_root / "reports" / "coverage.json"):
        if not candidate.exists():
            continue
        try:
            data = read_json(candidate)
        except MigrationError:
            continue
        mapping = data.get("bank_remote_paths")
        if isinstance(mapping, dict):
            return {str(key): str(value) for key, value in mapping.items()}
    return {}


def plan_names(records: list[ItemRecord], items_dir: Path) -> list[str]:
    """Assign one file stem per voice cue using the shared v2 naming rule.

    The rule itself lives in ``v2_naming.assign_file_stems`` so the extractor and
    this migration cannot drift apart.  This function only adds the dry-run guard
    against clobbering a file that already exists.
    """
    warnings: list[str] = []
    for record in records:
        if not safe_filename(record.text):
            warnings.append(f"empty subtitle text for cue {record.cue}; fell back to the cue name")

    stems = assign_file_stems((record.cue, record.text) for record in records)
    for record in records:
        stem = stems[record.cue]
        target_wav = items_dir / f"{stem}.wav"
        if target_wav.exists() and target_wav != record.wav_path:
            raise MigrationError(f"target already exists: {target_wav}")
        record.v2_stem = stem
    return warnings


def build_run_plan(run_root: Path) -> RunPlan | None:
    items_dir = run_root / "items"
    if not items_dir.is_dir() or looks_like_v2(items_dir):
        return None

    flat_records = collect_flat_items(items_dir)
    legacy_records = [] if flat_records else collect_legacy_items(items_dir)
    if flat_records and legacy_records:
        raise MigrationError(f"mixed v1 layouts in {items_dir}")
    records = flat_records or legacy_records
    if not records:
        return None

    plan = RunPlan(
        run_root=run_root,
        items_dir=items_dir,
        layout="flat" if flat_records else "dir-per-item",
        items_found=len(records),
    )

    tsv_path, old_rows = read_legacy_tsv(run_root)
    plan.tsv_path = tsv_path
    plan.tsv_rows_before = len(old_rows)
    order_by_cue = {row.get("voice_cue", ""): index for index, row in enumerate(old_rows)}
    acb_by_cue = {
        row.get("voice_cue", ""): row.get("acb_path", "")
        for row in old_rows
        if row.get("voice_cue") and row.get("acb_path")
    }

    # One surviving file per voice cue; extra references fold into the survivor.
    by_cue: dict[str, ItemRecord] = {}
    for record in records:
        keeper = by_cue.get(record.cue)
        if keeper is None:
            by_cue[record.cue] = record
            continue
        if keeper.text != record.text:
            raise MigrationError(
                f"cue {record.cue} carries two different subtitle texts in {items_dir}; refusing to collapse"
            )
        # Collapsing deletes audio, so prove the copies are really identical first.
        if file_sha256(keeper.wav_path) != file_sha256(record.wav_path):
            raise MigrationError(
                f"cue {record.cue} appears twice with different audio in {items_dir}; refusing to collapse\n"
                f"    {keeper.wav_path}\n    {record.wav_path}"
            )
        keeper.sibling_metadata_paths.append(record.metadata_path)
        # The duplicate's audio is byte-identical to the survivor's, so it goes away
        # along with its subtitle and metadata.
        plan.deletions.append(record.wav_path)
        if record.subtitle_path:
            plan.deletions.append(record.subtitle_path)
        plan.deletions.append(record.metadata_path)
        if record.legacy_dir is not None:
            plan.removed_dirs.append(record.legacy_dir)
        plan.collapsed += 1

    survivors = list(by_cue.values())
    for record in survivors:
        record.old_index = order_by_cue.get(record.cue, record.old_index or 10**9)
    survivors.sort(key=lambda record: (record.old_index, record.cue))
    for position, record in enumerate(survivors, 1):
        record.v2_position = position
    plan.survivors = survivors
    plan.unique_items = len(survivors)

    plan.warnings.extend(plan_names(survivors, items_dir))

    remote_acb = read_remote_acb_map(run_root)
    for record in survivors:
        bank = str(record.metadata.get("bank_name") or "")
        record.metadata["_v2_acb_path"] = remote_acb.get(bank) or acb_by_cue.get(record.cue, "")
        plan.renames.append((record.wav_path, items_dir / f"{record.v2_stem}.wav"))
        if record.subtitle_path:
            plan.deletions.append(record.subtitle_path)
        if record.legacy_dir is not None:
            plan.removed_dirs.append(record.legacy_dir)

    plan.tsv_rows_after = len(survivors)
    validate_plan_coverage(plan, records)
    return plan


def validate_plan_coverage(plan: RunPlan, records: list[ItemRecord]) -> None:
    """Every input item must be either renamed or explicitly deleted.  Checked in dry runs too."""
    renamed = {source for source, _target in plan.renames}
    deleted = set(plan.deletions)
    uncovered = [record.wav_path for record in records if record.wav_path not in renamed and record.wav_path not in deleted]
    if uncovered:
        raise MigrationError(f"{plan.items_dir} would leave {len(uncovered)} item(s) behind: {uncovered[:5]}")


def render_metadata(record: ItemRecord, items_dir: Path) -> str:
    data = dict(record.metadata)
    data.pop("_v2_acb_path", None)
    for key in METADATA_DROP_FIELDS:
        data.pop(key, None)

    data["output_index"] = record.v2_position
    data["wav_file"] = str(items_dir / f"{record.v2_stem}.wav")
    data["metadata_file"] = str(items_dir / f"{record.v2_stem}.json")

    if record.sibling_metadata_paths:
        extra = []
        for path in record.sibling_metadata_paths:
            try:
                sibling = read_json(path)
            except MigrationError:
                continue
            extra.append(
                {
                    "script_path": sibling.get("script_path", ""),
                    "line_no": sibling.get("line_no"),
                    "voice_line_no": sibling.get("voice_line_no"),
                }
            )
        if extra:
            data["also_referenced_by"] = extra
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def render_tsv(plan: RunPlan) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TSV_FIELDS_V2, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for position, record in enumerate(plan.survivors, 1):
        data = record.metadata
        writer.writerow(
            {
                "index": position,
                "speaker": data.get("speaker", ""),
                "text": tsv_text(record.text),
                "voice_cue": record.cue,
                "bank_name": data.get("bank_name", ""),
                "subsong_index": data.get("subsong_index", ""),
                "wav_file": str(plan.items_dir / f"{record.v2_stem}.wav"),
                "metadata_file": str(plan.items_dir / f"{record.v2_stem}.json"),
                "script_path": data.get("script_path", ""),
                "line_no": data.get("line_no", ""),
                "voice_line_no": data.get("voice_line_no", ""),
                "acb_path": data.get("_v2_acb_path", ""),
            }
        )
    return buffer.getvalue()


def apply_plan(plan: RunPlan) -> None:
    items_dir = plan.items_dir

    # Rename wavs first, so a failure never leaves a wav without its metadata.
    for source, target in plan.renames:
        if source == target:
            continue
        if target.exists():
            raise MigrationError(f"refusing to overwrite {target}")
        source.replace(target)

    # Metadata is rendered before deletions, because siblings are read from their old paths.
    for record in plan.survivors:
        target = items_dir / f"{record.v2_stem}.json"
        target.write_text(render_metadata(record, items_dir), encoding="utf-8")
        if record.metadata_path != target and record.metadata_path.exists():
            record.metadata_path.unlink()

    for path in plan.deletions:
        if path.exists():
            path.unlink()

    for directory in sorted(set(plan.removed_dirs), key=lambda value: -len(value.parts)):
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()

    if plan.tsv_path is not None:
        plan.tsv_path.write_text(render_tsv(plan), encoding="utf-8", newline="")

    assert_no_leftovers(plan)


def assert_no_leftovers(plan: RunPlan) -> None:
    """Fail loudly if any v1 item survived, so a partial migration is never silent."""
    leftovers = sorted(p.name for p in plan.items_dir.glob("*_02_voice.wav"))
    leftovers += sorted(
        str(p.relative_to(plan.items_dir)) for p in plan.items_dir.iterdir() if p.is_dir() and (p / "voice.wav").exists()
    )
    if leftovers:
        raise MigrationError(
            f"{plan.items_dir} still holds {len(leftovers)} v1 item(s) after migration: {leftovers[:5]}"
        )


def iter_run_roots(root: Path) -> list[Path]:
    return sorted({items_dir.parent for items_dir in root.rglob("items") if items_dir.is_dir()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate v1 items directories to the v2 subtitle-named layout.")
    parser.add_argument("--root", default="output")
    parser.add_argument("--apply", action="store_true", help="Perform the migration; without it only prints the plan.")
    parser.add_argument("--report", default="", help="Write a JSON report to this path.")
    parser.add_argument("--verbose", action="store_true", help="List every rename.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 1

    plans: list[RunPlan] = []
    skipped: list[Path] = []
    for run_root in iter_run_roots(root):
        plan = build_run_plan(run_root)
        if plan is None:
            if (run_root / "items").is_dir():
                skipped.append(run_root)
            continue
        plans.append(plan)

    if not plans:
        print("nothing to migrate (every items directory is already v2, or empty)")
        return 0

    total_in = sum(plan.items_found for plan in plans)
    total_out = sum(plan.unique_items for plan in plans)
    total_collapsed = sum(plan.collapsed for plan in plans)

    print(f"mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"items directories to migrate: {len(plans)}")
    print(f"items found: {total_in}")
    print(f"files after collapse: {total_out}   (collapsed duplicates: {total_collapsed})")
    print()
    print(f"{'run':<44} {'layout':<13} {'in':>5} {'out':>5} {'folded':>7} {'tsv':>9}")
    for plan in plans:
        try:
            name = str(plan.run_root.relative_to(root))
        except ValueError:
            name = str(plan.run_root)
        tsv_state = f"{plan.tsv_rows_before}->{plan.tsv_rows_after}" if plan.tsv_path else "none"
        print(f"{name:<44} {plan.layout:<13} {plan.items_found:>5} {plan.unique_items:>5} {plan.collapsed:>7} {tsv_state:>9}")

    warnings = [(plan, warning) for plan in plans for warning in plan.warnings]
    if warnings:
        print()
        for plan, warning in warnings:
            print(f"WARN [{plan.run_root.name}]: {warning}")

    if args.verbose:
        print()
        for plan in plans:
            print(f"--- {plan.run_root} ---")
            for source, target in plan.renames:
                print(f"    {source.name}")
                print(f"      -> {target.name}")

    if skipped:
        print()
        print(f"skipped (already v2 or empty items dir): {len(skipped)}")
        for run_root in skipped:
            print(f"    {run_root}")

    if args.apply:
        print()
        for plan in plans:
            apply_plan(plan)
            print(f"migrated: {plan.run_root}")
    else:
        print()
        print("dry run only; re-run with --apply to migrate")

    if args.report:
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "applied": bool(args.apply),
            "root": str(root),
            "run_count": len(plans),
            "items_found": total_in,
            "files_after_collapse": total_out,
            "collapsed_duplicates": total_collapsed,
            "runs": [
                {
                    "run_root": str(plan.run_root),
                    "layout": plan.layout,
                    "items_found": plan.items_found,
                    "unique_items": plan.unique_items,
                    "collapsed": plan.collapsed,
                    "tsv_path": str(plan.tsv_path) if plan.tsv_path else "",
                    "tsv_rows_before": plan.tsv_rows_before,
                    "tsv_rows_after": plan.tsv_rows_after,
                    "renames": [{"from": str(a), "to": str(b)} for a, b in plan.renames],
                    "warnings": plan.warnings,
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
