from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gakumasu_voice import (  # noqa: E402
    AndroidClient,
    DEFAULT_ADB,
    DEFAULT_DEVICE,
    DEFAULT_OCTO_ROOT,
    DEFAULT_SCRIPT_MAX_KB,
    ToolError,
    decode_output,
    install_elapsed_printing,
    locate_vgmstream,
    parse_stream_count,
    run_command,
    safe_filename,
)
from probe_bgm_acb import first_blob_table, parse_utf  # noqa: E402


BGM_CUE_RE = re.compile(r"sud_bgm_[A-Za-z0-9_./-]+")
HASH_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class BgmAcbInfo:
    cue: str
    remote_acb: str
    awb_hash: str
    waveform_rows: list[dict[str, int]]
    block_rows: list[dict[str, int]]


def default_adb() -> str:
    if Path(DEFAULT_ADB).exists():
        return DEFAULT_ADB
    return shutil.which("adb") or DEFAULT_ADB


def remote_sh(client: AndroidClient, script: str, *, check: bool = False) -> str:
    result = run_command(
        [client.adb, "-s", client.device, "shell", "su", "0", "sh", "-c", script],
        check=check,
    )
    return decode_output(result)


def remote_first4(client: AndroidClient, remote_path: str) -> bytes:
    result = run_command(
        [
            client.adb,
            "-s",
            client.device,
            "shell",
            "su",
            "0",
            "dd",
            f"if={remote_path}",
            "bs=4",
            "count=1",
        ],
        check=False,
    )
    return result.stdout[:4]


def split_cue_values(values: Iterable[str]) -> list[str]:
    cues: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in re.split(r"[\s,;]+", value.strip()):
            if not part or part.startswith("#"):
                continue
            match = BGM_CUE_RE.search(part)
            cue = match.group(0) if match else part
            if cue not in seen:
                seen.add(cue)
                cues.append(cue)
    return cues


def read_cue_file(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            values.append(stripped)
    return split_cue_values(values)


def collect_script_bgm_cues(
    client: AndroidClient,
    roots: list[str],
    *,
    script_max_kb: int,
) -> list[str]:
    output = client.root_shell(
        "find",
        *roots,
        "-type",
        "f",
        "-size",
        f"-{script_max_kb}k",
        "-exec",
        "grep",
        "-a",
        "-h",
        "-o",
        "bgm=sud_bgm_[A-Za-z0-9_./-]*",
        "{}",
        "\\;",
        check=False,
    )
    return split_cue_values(line.replace("bgm=", "") for line in output.splitlines())


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_acb_index(path: Path, octo_root: str) -> dict[str, Any]:
    data = load_json(path)
    if data.get("octo_root") != octo_root:
        return {"version": 1, "octo_root": octo_root, "cues": {}}
    data.setdefault("version", 1)
    data.setdefault("cues", {})
    return data


def load_file_index(path: Path, octo_root: str) -> dict[str, Any]:
    data = load_json(path)
    if data.get("octo_root") != octo_root:
        return {"version": 1, "octo_root": octo_root, "files": {}}
    data.setdefault("version", 1)
    data.setdefault("files", {})
    return data


def compact_int_row(row: dict[str, Any], keys: Iterable[str]) -> dict[str, int]:
    compact: dict[str, int] = {}
    for key in keys:
        value = row.get(key)
        if isinstance(value, int):
            compact[key] = value
    return compact


def parse_bgm_acb(local_acb: Path, remote_acb: str = "") -> list[BgmAcbInfo]:
    data = local_acb.read_bytes()
    _, root_rows = parse_utf(data)

    stream_awb_hash = first_blob_table(root_rows, "StreamAwbHash")
    if not stream_awb_hash:
        return []
    _, hash_rows = stream_awb_hash

    waveform_rows: list[dict[str, int]] = []
    waveform_table = first_blob_table(root_rows, "WaveformTable")
    if waveform_table:
        _, rows = waveform_table
        waveform_rows = [
            compact_int_row(row, ("Id", "StreamAwbId", "SamplingRate", "NumSamples", "Streaming"))
            for row in rows
        ]

    block_rows: list[dict[str, int]] = []
    block_table = first_blob_table(root_rows, "BlockTable")
    if block_table:
        _, rows = block_table
        block_rows = [
            compact_int_row(row, ("Name", "Length", "StartPosition", "NumLoops", "PlaybackType", "JumpDestination"))
            for row in rows
        ]

    infos: list[BgmAcbInfo] = []
    for row in hash_rows:
        cue = row.get("Name")
        hash_blob = row.get("Hash")
        if not isinstance(cue, str) or not cue.startswith("sud_bgm_"):
            continue
        if not isinstance(hash_blob, bytes) or len(hash_blob) != 16:
            continue
        infos.append(
            BgmAcbInfo(
                cue=cue,
                remote_acb=remote_acb,
                awb_hash=hash_blob.hex(),
                waveform_rows=waveform_rows,
                block_rows=block_rows,
            )
        )
    return infos


def acb_record(info: BgmAcbInfo) -> dict[str, Any]:
    return {
        "remote_acb": info.remote_acb,
        "awb_hash": info.awb_hash,
        "waveform_rows": info.waveform_rows,
        "block_rows": info.block_rows,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def info_from_record(cue: str, record: dict[str, Any]) -> BgmAcbInfo:
    return BgmAcbInfo(
        cue=cue,
        remote_acb=str(record.get("remote_acb", "")),
        awb_hash=str(record.get("awb_hash", "")),
        waveform_rows=[row for row in record.get("waveform_rows", []) if isinstance(row, dict)],
        block_rows=[row for row in record.get("block_rows", []) if isinstance(row, dict)],
    )


def candidate_acb_paths_for_pattern(client: AndroidClient, octo_root: str, pattern: str) -> list[str]:
    candidates: list[str] = []
    grep_paths = client.grep_files(pattern, octo_root)
    print(f"grep hits for {pattern}: {len(grep_paths)}")
    for remote_path in grep_paths:
        header = remote_first4(client, remote_path)
        if len(grep_paths) <= 5:
            print(f"  hit header {header!r}: {remote_path}")
        if header == b"@UTF":
            candidates.append(remote_path)
    print(f"@UTF ACB candidates for {pattern}: {len(candidates)}")
    return candidates


def pull_cached(client: AndroidClient, remote_path: str, local_path: Path) -> None:
    sidecar = local_path.with_suffix(local_path.suffix + ".json")
    if local_path.exists() and local_path.stat().st_size > 0:
        sidecar_data = load_json(sidecar)
        if sidecar_data.get("remote_path") == remote_path:
            return
    client.pull_private_file(remote_path, local_path)
    save_json(
        sidecar,
        {
            "remote_path": remote_path,
            "pulled_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def build_acb_index(
    client: AndroidClient,
    octo_root: str,
    index_path: Path,
    acb_cache_dir: Path,
) -> dict[str, Any]:
    print("building BGM ACB index: one grep over octo cache")
    candidates = candidate_acb_paths_for_pattern(client, octo_root, "sud_bgm")
    print(f"ACB candidates with sud_bgm: {len(candidates)}")

    index = {"version": 1, "octo_root": octo_root, "built_at": datetime.now().isoformat(timespec="seconds"), "cues": {}}
    for number, remote_acb in enumerate(candidates, 1):
        local_acb = acb_cache_dir / (safe_filename(remote_acb.rsplit("/", 1)[-1]) + ".acb")
        try:
            pull_cached(client, remote_acb, local_acb)
            infos = parse_bgm_acb(local_acb, remote_acb)
        except Exception as exc:
            if number % 10 == 0:
                print(f"index {number}/{len(candidates)} skipped: {exc}")
            continue
        for info in infos:
            record = acb_record(info)
            existing = index["cues"].get(info.cue)
            if existing:
                candidates_list = existing.setdefault("remote_acb_candidates", [existing.get("remote_acb", "")])
                if info.remote_acb not in candidates_list:
                    candidates_list.append(info.remote_acb)
                continue
            index["cues"][info.cue] = record
        if number % 10 == 0:
            print(f"indexed ACB {number}/{len(candidates)}; cues={len(index['cues'])}")

    save_json(index_path, index)
    print(f"BGM ACB index: {index_path}")
    return index


def resolve_acb_info(
    client: AndroidClient,
    cue: str,
    octo_root: str,
    acb_index: dict[str, Any],
    acb_index_path: Path,
    acb_cache_dir: Path,
) -> BgmAcbInfo:
    record = acb_index.get("cues", {}).get(cue)
    if isinstance(record, dict) and record.get("remote_acb") and record.get("awb_hash"):
        return info_from_record(cue, record)

    print(f"targeted ACB lookup: {cue}")
    for remote_acb in candidate_acb_paths_for_pattern(client, octo_root, cue):
        local_acb = acb_cache_dir / f"{safe_filename(cue)}.acb"
        try:
            pull_cached(client, remote_acb, local_acb)
            for info in parse_bgm_acb(local_acb, remote_acb):
                if info.cue == cue:
                    acb_index.setdefault("cues", {})[cue] = acb_record(info)
                    save_json(acb_index_path, acb_index)
                    return info
        except Exception:
            continue

    raise ToolError(f"could not resolve BGM ACB for {cue}")


def build_file_index(client: AndroidClient, octo_root: str, index_path: Path) -> dict[str, Any]:
    print("building octo filename index: one find over octo cache")
    output = client.root_shell("find", octo_root, "-type", "f", check=False)
    files: dict[str, list[str]] = {}
    for line in output.splitlines():
        remote_path = line.strip()
        if not remote_path.startswith("/"):
            continue
        files.setdefault(remote_path.rsplit("/", 1)[-1], []).append(remote_path)
    index = {
        "version": 1,
        "octo_root": octo_root,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }
    save_json(index_path, index)
    print(f"octo filename index: {index_path} ({len(files)} names)")
    return index


def resolve_awb_path(
    client: AndroidClient,
    awb_hash: str,
    octo_root: str,
    file_index: dict[str, Any],
    file_index_path: Path,
) -> str:
    if not HASH_RE.match(awb_hash):
        raise ToolError(f"invalid AWB hash: {awb_hash}")

    paths = file_index.get("files", {}).get(awb_hash, [])
    if paths:
        return paths[0]

    print(f"targeted AWB lookup: {awb_hash}")
    output = client.root_shell("find", octo_root, "-name", awb_hash, "-print", check=False)
    paths = [line.strip() for line in output.splitlines() if line.startswith("/")]
    if not paths:
        raise ToolError(f"could not resolve external AWB {awb_hash}")
    file_index.setdefault("files", {})[awb_hash] = paths
    save_json(file_index_path, file_index)
    return paths[0]


def stream_count(vgmstream: str, awb_path: Path) -> int:
    result = run_command([vgmstream, "-m", str(awb_path)])
    return parse_stream_count(decode_output(result))


def block_subsong_order(info: BgmAcbInfo, awb_stream_count: int) -> list[dict[str, int]]:
    waveform_rows = info.waveform_rows
    block_rows = info.block_rows
    if block_rows and waveform_rows:
        order: list[dict[str, int]] = []
        for index, block in enumerate(block_rows, 1):
            waveform_index = int(block.get("Name", index)) - 1
            if waveform_index < 0 or waveform_index >= len(waveform_rows):
                waveform_index = min(index - 1, len(waveform_rows) - 1)
            stream_awb_id = int(waveform_rows[waveform_index].get("StreamAwbId", waveform_index))
            subsong = stream_awb_id + 1
            if 1 <= subsong <= awb_stream_count:
                order.append(
                    {
                        "block": index,
                        "subsong": subsong,
                        "length_ms": int(block.get("Length", 0)),
                        "start_ms": int(block.get("StartPosition", 0)),
                    }
                )
        if order:
            return order

    if waveform_rows:
        order = []
        for index, row in enumerate(waveform_rows, 1):
            subsong = int(row.get("StreamAwbId", index - 1)) + 1
            if 1 <= subsong <= awb_stream_count:
                order.append({"block": index, "subsong": subsong, "length_ms": 0, "start_ms": 0})
        if order:
            return order

    return [{"block": index, "subsong": index, "length_ms": 0, "start_ms": 0} for index in range(1, awb_stream_count + 1)]


def decode_subsong(vgmstream: str, awb_path: Path, output_wav: Path, subsong: int) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    run_command([vgmstream, "-s", str(subsong), "-o", str(output_wav), str(awb_path)])


def concat_wavs(input_paths: list[Path], output_path: Path) -> None:
    if not input_paths:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(input_paths[0]), "rb") as first:
        params = first.getparams()
        comparable_params = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
    with wave.open(str(output_path), "wb") as out:
        out.setparams(params)
        for input_path in input_paths:
            with wave.open(str(input_path), "rb") as src:
                src_params = src.getparams()
                src_comparable_params = (
                    src_params.nchannels,
                    src_params.sampwidth,
                    src_params.framerate,
                    src_params.comptype,
                    src_params.compname,
                )
                if src_comparable_params != comparable_params:
                    raise ToolError(f"WAV params differ; cannot concatenate {input_path}")
                out.writeframes(src.readframes(src.getnframes()))


def extract_one(
    *,
    client: AndroidClient,
    cue: str,
    info: BgmAcbInfo,
    remote_awb: str,
    vgmstream: str,
    output_root: Path,
    acb_cache_dir: Path,
    awb_cache_dir: Path,
    concat: bool,
) -> dict[str, Any]:
    cue_dir = output_root / safe_filename(cue)
    source_dir = cue_dir / "source"
    block_dir = cue_dir / "blocks"
    source_dir.mkdir(parents=True, exist_ok=True)

    local_acb = source_dir / f"{safe_filename(cue)}.acb"
    local_awb = source_dir / f"{safe_filename(cue)}.awb"
    cached_acb = acb_cache_dir / f"{safe_filename(cue)}.acb"
    cached_awb = awb_cache_dir / f"{info.awb_hash}.awb"

    pull_cached(client, info.remote_acb, cached_acb)
    pull_cached(client, remote_awb, cached_awb)
    if not local_acb.exists():
        shutil.copy2(cached_acb, local_acb)
    if not local_awb.exists():
        shutil.copy2(cached_awb, local_awb)

    count = stream_count(vgmstream, local_awb)
    order = block_subsong_order(info, count)
    exported_blocks: list[Path] = []

    if len(order) == 1:
        final_wav = cue_dir / f"{safe_filename(cue)}.wav"
        decode_subsong(vgmstream, local_awb, final_wav, order[0]["subsong"])
    else:
        for entry in order:
            block_wav = block_dir / f"{safe_filename(cue)}_{entry['block']:02d}_subsong{entry['subsong']}.wav"
            decode_subsong(vgmstream, local_awb, block_wav, entry["subsong"])
            exported_blocks.append(block_wav)
        final_wav = cue_dir / f"{safe_filename(cue)}.wav"
        if concat:
            concat_wavs(exported_blocks, final_wav)

    report = {
        "cue": cue,
        "remote_acb": info.remote_acb,
        "remote_awb": remote_awb,
        "awb_hash": info.awb_hash,
        "local_acb": str(local_acb),
        "local_awb": str(local_awb),
        "stream_count": count,
        "block_order": order,
        "block_wavs": [str(path) for path in exported_blocks],
        "final_wav": str(final_wav) if final_wav.exists() else "",
        "waveform_rows": info.waveform_rows,
        "block_rows": info.block_rows,
    }
    save_json(cue_dir / "metadata.json", report)
    return report


def make_output_root(base_output: Path, run_id: str) -> Path:
    root = base_output / (run_id or datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not root.exists():
        return root
    suffix = 2
    while True:
        candidate = base_output / f"{root.name}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch extract Gakumasu BGM cues to WAV.")
    parser.add_argument("--adb", default=default_adb())
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--octo-root", default=DEFAULT_OCTO_ROOT)
    parser.add_argument("--cue", action="append", default=[], help="BGM cue name. Can be repeated or comma-separated.")
    parser.add_argument("--cue-file", default="", help="Text file with one or more BGM cue names.")
    parser.add_argument("--from-scripts", action="store_true", help="Collect sud_bgm cues from ADV scripts first.")
    parser.add_argument("--script-roots", default="", help="Script roots for --from-scripts. Defaults to --octo-root.")
    parser.add_argument("--script-max-kb", type=int, default=DEFAULT_SCRIPT_MAX_KB)
    parser.add_argument("--limit", type=int, default=0, help="Limit cue count after collection. 0 means no limit.")
    parser.add_argument("--index-mode", choices=("auto", "full", "targeted", "cache-only"), default="auto")
    parser.add_argument("--index-threshold", type=int, default=2, help="Auto mode builds full ACB index when missing cues reach this count.")
    parser.add_argument("--refresh-acb-index", action="store_true")
    parser.add_argument("--refresh-file-index", action="store_true")
    parser.add_argument("--no-concat", action="store_true", help="For multi-block BGM, keep block WAVs only.")
    parser.add_argument("--elapsed", action="store_true", help="Prefix printed lines with elapsed time.")
    parser.add_argument("--vgmstream", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", default=str(Path("output") / "bgm_batch"))
    parser.add_argument("--cache-dir", default=str(Path("output") / "_cache"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.elapsed:
        install_elapsed_printing()

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()
    vgmstream = locate_vgmstream(args.vgmstream)

    cache_dir = Path(args.cache_dir).resolve()
    acb_index_path = cache_dir / "bgm_acb_index.json"
    file_index_path = cache_dir / "octo_file_index.json"
    acb_cache_dir = cache_dir / "bgm_acb"
    awb_cache_dir = cache_dir / "bgm_awb"

    cues = split_cue_values(args.cue)
    if args.cue_file:
        cues.extend(cue for cue in read_cue_file(Path(args.cue_file)) if cue not in cues)
    if args.from_scripts:
        roots = split_cue_values([])  # keeps typing simple; overwritten below
        roots = [part for part in re.split(r"[\s,;]+", args.script_roots.strip()) if part] if args.script_roots else [args.octo_root]
        script_cues = collect_script_bgm_cues(client, roots, script_max_kb=args.script_max_kb)
        cues.extend(cue for cue in script_cues if cue not in cues)
    if args.limit > 0:
        cues = cues[: args.limit]
    if not cues:
        raise ToolError("no BGM cues provided; use --cue, --cue-file, or --from-scripts")

    print(f"BGM cues: {len(cues)}")
    output_root = make_output_root(Path(args.output).resolve(), args.run_id)
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"output run: {output_root}")
    print(f"cache dir: {cache_dir}")

    if args.refresh_acb_index:
        acb_index = build_acb_index(client, args.octo_root, acb_index_path, acb_cache_dir)
    else:
        acb_index = load_acb_index(acb_index_path, args.octo_root)

    missing = [cue for cue in cues if cue not in acb_index.get("cues", {})]
    should_full_index = args.index_mode == "full" or (
        args.index_mode == "auto" and len(missing) >= max(1, args.index_threshold)
    )
    if missing and should_full_index:
        acb_index = build_acb_index(client, args.octo_root, acb_index_path, acb_cache_dir)
    elif missing and args.index_mode == "cache-only":
        raise ToolError(f"cues missing from BGM ACB index: {', '.join(missing)}")

    infos: list[BgmAcbInfo] = []
    for cue in cues:
        infos.append(resolve_acb_info(client, cue, args.octo_root, acb_index, acb_index_path, acb_cache_dir))

    if args.refresh_file_index:
        file_index = build_file_index(client, args.octo_root, file_index_path)
    else:
        file_index = load_file_index(file_index_path, args.octo_root)

    missing_hashes = sorted({info.awb_hash for info in infos if info.awb_hash not in file_index.get("files", {})})
    if missing_hashes and (args.index_mode == "full" or len(missing_hashes) >= max(1, args.index_threshold)):
        file_index = build_file_index(client, args.octo_root, file_index_path)

    reports = []
    for index, info in enumerate(infos, 1):
        print(f"extract {index}/{len(infos)}: {info.cue}")
        remote_awb = resolve_awb_path(client, info.awb_hash, args.octo_root, file_index, file_index_path)
        reports.append(
            extract_one(
                client=client,
                cue=info.cue,
                info=info,
                remote_awb=remote_awb,
                vgmstream=vgmstream,
                output_root=output_root,
                acb_cache_dir=acb_cache_dir,
                awb_cache_dir=awb_cache_dir,
                concat=not args.no_concat,
            )
        )

    summary = {
        "octo_root": args.octo_root,
        "output_root": str(output_root),
        "cue_count": len(cues),
        "cues": cues,
        "items": reports,
    }
    save_json(output_root / "summary.json", summary)
    print(f"summary: {output_root / 'summary.json'}")
    print(f"exported: {len(reports)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
