from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gakumasu_voice import normalize_adv_text, parse_script_events, script_local_name, tsv_text


USER_PLACEHOLDER = "{user}"
RUBY_RE = re.compile(r"<r=([^>\r\n]+)>(.*?)</r>")
TAG_RE = re.compile(r"</?[A-Za-z][^>\r\n]*>")
PLACEHOLDER_RE = re.compile(r"\{[^{}\r\n]+\}")
ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);")


@dataclass(frozen=True)
class SourceLine:
    text: str
    speaker: str


def normalize_text(value: str) -> str:
    return normalize_adv_text(value)


def output_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = {part.lower() for part in path.parts}
        if "scripts" in parts or "acb" in parts:
            continue
        name = path.name.lower()
        if path.suffix.lower() == ".tsv":
            files.append(path)
        elif name.endswith("_01_subtitle.txt") or name == "subtitle.txt":
            files.append(path)
        elif name.endswith("_03_metadata.json") or name in {"metadata.json", "unmatched.json"}:
            files.append(path)
    return files


def normalize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_json_value(item) for key, item in value.items()}
    return value


def iter_json_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_json_strings(item)


def scan_text(value: str, counts: Counter[str]) -> None:
    counts["user_placeholder"] += value.count(USER_PLACEHOLDER)
    counts["markup_tag"] += len(TAG_RE.findall(value))
    counts["ruby_tag"] += len(RUBY_RE.findall(value))
    for placeholder in PLACEHOLDER_RE.findall(value):
        if placeholder != USER_PLACEHOLDER:
            counts[f"placeholder:{placeholder}"] += 1
    for entity in ENTITY_RE.findall(value):
        counts[f"entity:{entity}"] += 1


def scan_file(path: Path, counts: Counter[str]) -> None:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            scan_text(path.read_text(encoding="utf-8", errors="replace"), counts)
            return
        for value in iter_json_strings(data):
            scan_text(value, counts)
        return
    scan_text(path.read_text(encoding="utf-8", errors="replace"), counts)


def normalize_file(path: Path, dry_run: bool) -> bool:
    if path.suffix.lower() == ".json":
        try:
            original = path.read_text(encoding="utf-8")
            data = json.loads(original)
        except json.JSONDecodeError:
            original = path.read_text(encoding="utf-8", errors="replace")
            normalized = normalize_text(original)
        else:
            normalized_data = normalize_json_value(data)
            if normalized_data == data:
                return False
            normalized = json.dumps(normalized_data, ensure_ascii=False, indent=2) + "\n"
        if normalized == original:
            return False
        if not dry_run:
            path.write_text(normalized, encoding="utf-8")
        return True

    original = path.read_text(encoding="utf-8", errors="replace")
    normalized = normalize_text(original)
    if normalized == original:
        return False
    if not dry_run:
        path.write_text(normalized, encoding="utf-8", newline="")
    return True


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def metadata_files(files: list[Path]) -> list[Path]:
    return [
        path
        for path in files
        if path.suffix.lower() == ".json"
        and (path.name.lower().endswith("_03_metadata.json") or path.name.lower() == "metadata.json")
    ]


def tsv_files(files: list[Path]) -> list[Path]:
    return [path for path in files if path.suffix.lower() == ".tsv"]


def infer_subtitle_path(metadata_path: Path) -> Path | None:
    name = metadata_path.name
    if name == "metadata.json":
        return metadata_path.with_name("subtitle.txt")
    if name.endswith("_03_metadata.json"):
        return metadata_path.with_name(name.replace("_03_metadata.json", "_01_subtitle.txt"))
    return None


def read_source_line(
    script_file: Path,
    line_no: Any,
    script_cache: dict[Path, dict[int, SourceLine]],
    stats: Counter[str],
) -> SourceLine | None:
    try:
        line_index = int(line_no)
    except (TypeError, ValueError):
        stats["source_bad_line_no"] += 1
        return None

    try:
        script_key = script_file.resolve()
    except OSError:
        script_key = script_file
    if not script_file.exists():
        stats["source_missing_script_file"] += 1
        return None

    if script_key not in script_cache:
        try:
            script = script_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stats["source_unreadable_script_file"] += 1
            return None
        messages, _voices = parse_script_events(script, str(script_file))
        script_cache[script_key] = {
            message.line_no: SourceLine(text=message.text, speaker=message.speaker) for message in messages
        }
        stats["source_script_files_parsed"] += 1

    source_line = script_cache[script_key].get(line_index)
    if source_line is None:
        stats["source_missing_line_no"] += 1
    return source_line


def write_text_if_changed(path: Path, value: str, dry_run: bool) -> bool:
    original = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if original == value:
        return False
    if not dry_run:
        path.write_text(value, encoding="utf-8", newline="")
    return True


def rehydrate_metadata_file(
    path: Path,
    *,
    dry_run: bool,
    script_cache: dict[Path, dict[int, SourceLine]],
    metadata_source: dict[Path, SourceLine],
    stats: Counter[str],
) -> set[Path]:
    changed_paths: set[Path] = set()
    try:
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
    except (OSError, json.JSONDecodeError):
        stats["metadata_unreadable_or_invalid"] += 1
        return changed_paths
    if not isinstance(data, dict):
        stats["metadata_not_object"] += 1
        return changed_paths

    script_file_value = data.get("script_file")
    line_no = data.get("line_no")
    if not script_file_value or line_no is None:
        stats["metadata_missing_script_source"] += 1
        return changed_paths

    source_line = read_source_line(resolve_path(str(script_file_value), path.parent), line_no, script_cache, stats)
    if source_line is None:
        return changed_paths

    try:
        metadata_source[path.resolve()] = source_line
    except OSError:
        metadata_source[path] = source_line

    updated = dict(data)
    updated["text"] = source_line.text
    updated["speaker"] = source_line.speaker
    if updated != data:
        normalized = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
        if not dry_run:
            path.write_text(normalized, encoding="utf-8")
        changed_paths.add(path)
        stats["metadata_files_rehydrated"] += 1

    subtitle_path = None
    if data.get("subtitle_file"):
        subtitle_path = resolve_path(str(data["subtitle_file"]), path.parent)
    if subtitle_path is None:
        subtitle_path = infer_subtitle_path(path)
    if subtitle_path and subtitle_path.exists():
        if write_text_if_changed(subtitle_path, source_line.text + "\n", dry_run):
            changed_paths.add(subtitle_path)
            stats["subtitle_files_rehydrated"] += 1
    elif subtitle_path:
        stats["subtitle_missing_file"] += 1

    return changed_paths


def source_line_from_tsv_row(
    row: dict[str, str],
    tsv_path: Path,
    metadata_source: dict[Path, SourceLine],
    script_cache: dict[Path, dict[int, SourceLine]],
    stats: Counter[str],
) -> SourceLine | None:
    metadata_value = row.get("metadata_file", "")
    if metadata_value:
        metadata_path = resolve_path(metadata_value, tsv_path.parent)
        try:
            source_line = metadata_source.get(metadata_path.resolve())
        except OSError:
            source_line = metadata_source.get(metadata_path)
        if source_line is not None:
            return source_line

    script_path_value = row.get("script_path", "")
    line_no = row.get("line_no")
    if script_path_value and line_no:
        script_file = tsv_path.parent / "scripts" / script_local_name(script_path_value)
        return read_source_line(script_file, line_no, script_cache, stats)

    stats["tsv_row_missing_source"] += 1
    return None


def rehydrate_tsv_file(
    path: Path,
    *,
    dry_run: bool,
    metadata_source: dict[Path, SourceLine],
    script_cache: dict[Path, dict[int, SourceLine]],
    stats: Counter[str],
) -> set[Path]:
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        stats["tsv_unreadable"] += 1
        return set()

    reader = csv.DictReader(io.StringIO(original), delimiter="\t")
    if not reader.fieldnames or "text" not in reader.fieldnames:
        stats["tsv_unsupported_shape"] += 1
        return set()

    rows = list(reader)
    changed = False
    for row in rows:
        source_line = source_line_from_tsv_row(row, path, metadata_source, script_cache, stats)
        if source_line is None:
            continue
        if "speaker" in row and row["speaker"] != source_line.speaker:
            row["speaker"] = source_line.speaker
            changed = True
        next_text = tsv_text(source_line.text)
        if row["text"] != next_text:
            row["text"] = next_text
            changed = True

    if not changed:
        return set()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    normalized = output.getvalue()
    if normalized == original:
        return set()
    if not dry_run:
        path.write_text(normalized, encoding="utf-8", newline="")
    stats["tsv_files_rehydrated"] += 1
    return {path}


def rehydrate_from_scripts(files: list[Path], dry_run: bool) -> tuple[set[Path], Counter[str]]:
    changed_paths: set[Path] = set()
    stats: Counter[str] = Counter()
    script_cache: dict[Path, dict[int, SourceLine]] = {}
    metadata_source: dict[Path, SourceLine] = {}

    for path in metadata_files(files):
        changed_paths.update(
            rehydrate_metadata_file(
                path,
                dry_run=dry_run,
                script_cache=script_cache,
                metadata_source=metadata_source,
                stats=stats,
            )
        )

    for path in tsv_files(files):
        changed_paths.update(
            rehydrate_tsv_file(
                path,
                dry_run=dry_run,
                metadata_source=metadata_source,
                script_cache=script_cache,
                stats=stats,
            )
        )

    return changed_paths, stats


def scan_raw_scripts(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in root.rglob("*.txt"):
        if "scripts" not in {part.lower() for part in path.parts}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts["user_placeholder"] += text.count(USER_PLACEHOLDER)
        counts["markup_tag"] += len(TAG_RE.findall(text))
        counts["ruby_tag"] += len(RUBY_RE.findall(text))
    return counts


def print_counts(title: str, counts: Counter[str]) -> None:
    print(title)
    if not counts:
        print("  none")
        return
    for key, count in counts.most_common():
        print(f"  {key}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize generated subtitle/list text under output.")
    parser.add_argument("--root", default="output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rehydrate-from-scripts",
        action="store_true",
        help="Refresh metadata, subtitle text, and TSV rows from cached raw scripts when source line info exists.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    files = output_text_files(root)
    before: Counter[str] = Counter()
    for path in files:
        scan_file(path, before)

    changed_paths: set[Path] = set()
    for path in files:
        if normalize_file(path, args.dry_run):
            changed_paths.add(path)

    rehydrate_stats: Counter[str] = Counter()
    if args.rehydrate_from_scripts:
        rehydrated_paths, rehydrate_stats = rehydrate_from_scripts(files, args.dry_run)
        changed_paths.update(rehydrated_paths)

    after: Counter[str] = Counter()
    for path in files:
        scan_file(path, after)

    raw_script_counts = scan_raw_scripts(root)

    print(f"target files scanned: {len(files)}")
    print(f"target files changed: {len(changed_paths)}{' (dry run)' if args.dry_run else ''}")
    print_counts("before target issues:", before)
    print_counts("after target issues:", after)
    if args.rehydrate_from_scripts:
        print_counts("rehydrate from scripts:", rehydrate_stats)
    print_counts("raw script cache still contains:", raw_script_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
