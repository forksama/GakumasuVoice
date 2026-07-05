from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ADB = r"C:\Program Files\NetEase\MuMu Player 12\nx_main\adb.exe"
DEFAULT_DEVICE = "127.0.0.1:16384"
DEFAULT_PACKAGE = "com.bandainamcoent.idolmaster_gakuen"
DEFAULT_OCTO_ROOT = f"/data/data/{DEFAULT_PACKAGE}/files/octo/v1/400"
DEFAULT_SCRIPT_BUCKETS = ("3", "4", "6", "7", "9")
DEFAULT_SCRIPT_ROOTS = tuple(f"{DEFAULT_OCTO_ROOT}/{bucket}" for bucket in DEFAULT_SCRIPT_BUCKETS)
DEFAULT_SCRIPT_ROOT = DEFAULT_SCRIPT_ROOTS[0]
DEFAULT_SCRIPT_MAX_KB = 500
DEFAULT_CHARACTER_CODE = "krnh"
DEFAULT_CHARACTER_NAME = "燐羽"


@dataclass(frozen=True)
class MessageEvent:
    script_path: str
    line_no: int
    text: str
    speaker: str
    start: float | None
    duration: float | None
    raw: str


@dataclass(frozen=True)
class VoiceEvent:
    script_path: str
    line_no: int
    voice_cue: str
    start: float | None
    duration: float | None
    raw: str


@dataclass(frozen=True)
class VoiceLine:
    script_path: str
    line_no: int
    voice_line_no: int
    text: str
    speaker: str
    voice_cue: str
    bank_name: str
    start: float | None
    duration: float | None
    voice_start: float | None
    voice_duration: float | None
    output_slug: str


def decode_adv_text(value: str) -> str:
    return (
        value.replace(r"\n", "\n")
        .replace(r"\=", "=")
        .replace(r"\{", "{")
        .replace(r"\}", "}")
    )


def tsv_text(value: str) -> str:
    return value.replace("\r", "").replace("\n", r"\n")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value or "item"


def short_progress_item(value: str, max_len: int = 48) -> str:
    if len(value) <= max_len:
        return value
    return "..." + value[-(max_len - 3) :]


def remote_tail(remote_path: str) -> str:
    parts = remote_path.strip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else remote_path


def split_remote_paths(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]


def extract_attr(line: str, key: str, following_keys: Iterable[str]) -> str:
    marker = f"{key}="
    start = line.find(marker)
    if start < 0:
        return ""
    value_start = start + len(marker)
    end_candidates = []
    for next_key in following_keys:
        pos = line.find(f" {next_key}=", value_start)
        if pos >= 0:
            end_candidates.append(pos)
    close_pos = line.rfind("]")
    if close_pos >= value_start:
        end_candidates.append(close_pos)
    end = min(end_candidates) if end_candidates else len(line)
    return line[value_start:end].strip()


def parse_clip_float(line: str, field: str) -> float | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+(?:\.\d+)?)', line)
    if not match:
        return None
    return float(match.group(1))


def parse_script_events(script: str, script_path: str) -> tuple[list[MessageEvent], list[VoiceEvent]]:
    messages: list[MessageEvent] = []
    voices: list[VoiceEvent] = []
    text_followers = ("name", "speaker", "se", "hide", "wait", "isInner", "clip")
    name_followers = ("speaker", "se", "hide", "wait", "isInner", "clip")

    for line_no, line in enumerate(script.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[message "):
            text = decode_adv_text(extract_attr(stripped, "text", text_followers))
            speaker = decode_adv_text(extract_attr(stripped, "name", name_followers))
            messages.append(
                MessageEvent(
                    script_path=script_path,
                    line_no=line_no,
                    text=text,
                    speaker=speaker,
                    start=parse_clip_float(stripped, "_startTime"),
                    duration=parse_clip_float(stripped, "_duration"),
                    raw=stripped,
                )
            )
        elif stripped.startswith("[voice "):
            voice_cue = extract_attr(stripped, "voice", ("actorId", "channel", "clip"))
            if voice_cue:
                voices.append(
                    VoiceEvent(
                        script_path=script_path,
                        line_no=line_no,
                        voice_cue=voice_cue,
                        start=parse_clip_float(stripped, "_startTime"),
                        duration=parse_clip_float(stripped, "_duration"),
                        raw=stripped,
                    )
                )
    return messages, voices


def character_bank_name(voice_cue: str, character_code: str) -> str | None:
    match = re.match(rf"(.+)_{re.escape(character_code)}-\d+$", voice_cue)
    if not match:
        return None
    return match.group(1)


def find_matching_message(voice: VoiceEvent, messages: list[MessageEvent]) -> MessageEvent | None:
    timed_candidates: list[tuple[float, MessageEvent]] = []
    if voice.start is not None:
        for message in messages:
            if message.start is None:
                continue
            end = message.start + (message.duration or 0) + 0.75
            if message.start - 0.25 <= voice.start <= end:
                timed_candidates.append((abs(voice.start - message.start), message))
    if timed_candidates:
        return min(timed_candidates, key=lambda item: item[0])[1]

    preceding = [message for message in messages if message.line_no <= voice.line_no]
    if preceding:
        return preceding[-1]
    return messages[0] if messages else None


def collect_character_voice_lines(script: str, script_path: str, character_code: str) -> list[VoiceLine]:
    messages, voices = parse_script_events(script, script_path)
    lines: list[VoiceLine] = []
    for voice in voices:
        bank = character_bank_name(voice.voice_cue, character_code)
        if not bank:
            continue
        message = find_matching_message(voice, messages)
        if not message:
            continue
        lines.append(
            VoiceLine(
                script_path=script_path,
                line_no=message.line_no,
                voice_line_no=voice.line_no,
                text=message.text,
                speaker=message.speaker,
                voice_cue=voice.voice_cue,
                bank_name=bank,
                start=message.start,
                duration=message.duration,
                voice_start=voice.start,
                voice_duration=voice.duration,
                output_slug=safe_filename(voice.voice_cue),
            )
        )
    return lines


def parse_stream_count(metadata: str) -> int:
    match = re.search(r"stream count:\s*(\d+)", metadata)
    return int(match.group(1)) if match else 1


def parse_stream_name(metadata: str) -> str:
    match = re.search(r"stream name:\s*(.+)", metadata)
    return match.group(1).strip() if match else ""


class ToolError(RuntimeError):
    pass


class Progress:
    def __init__(self, label: str, total: int, *, enabled: bool = True, width: int = 28):
        self.label = label
        self.total = total
        self.enabled = enabled and total > 0
        self.width = width
        self.current = 0
        self.last_len = 0
        self.finished = False
        if self.enabled:
            self.show()

    def show(self, item: str = "") -> None:
        if not self.enabled or self.finished:
            return
        self._render(item)

    def step(self, item: str = "") -> None:
        if not self.enabled or self.finished:
            return
        self.current += 1
        self._render(item)

    def finish(self) -> None:
        if not self.enabled or self.finished:
            return
        self.current = self.total
        self._render()
        sys.stdout.write("\n")
        sys.stdout.flush()
        self.finished = True

    def _render(self, item: str = "") -> None:
        shown = min(max(self.current, 0), self.total)
        ratio = shown / self.total
        filled = int(self.width * ratio)
        bar = "#" * filled + "." * (self.width - filled)
        pct = int(ratio * 100)
        message = f"\r{self.label}: [{bar}] {shown}/{self.total} {pct:3d}%"
        if item:
            message += f" {short_progress_item(item)}"
        padding = " " * max(0, self.last_len - len(message))
        sys.stdout.write(message + padding)
        sys.stdout.flush()
        self.last_len = len(message)


def run_command(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True)
    if check and result.returncode != 0:
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise ToolError(f"command failed ({result.returncode}): {' '.join(args)}\n{stdout}\n{stderr}")
    return result


def decode_output(result: subprocess.CompletedProcess) -> str:
    data = result.stdout
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


class AndroidClient:
    def __init__(self, adb: str, device: str):
        self.adb = adb
        self.device = device

    def ensure_connected(self) -> None:
        run_command([self.adb, "connect", self.device], check=False)
        result = run_command([self.adb, "-s", self.device, "get-state"], check=False)
        if result.returncode != 0 or b"device" not in result.stdout:
            raise ToolError(f"cannot connect to Android device {self.device}")

    def shell(self, *args: str, check: bool = True) -> str:
        result = run_command([self.adb, "-s", self.device, "shell", *args], check=check)
        return decode_output(result)

    def root_shell(self, *args: str, check: bool = True) -> str:
        return self.shell("su", "0", *args, check=check)

    def grep_files(self, pattern: str, root: str) -> list[str]:
        output = self.root_shell("grep", "-R", "-a", "-l", pattern, root, check=False)
        return sorted({line.strip() for line in output.splitlines() if line.startswith("/")})

    def grep_text_files(
        self,
        pattern: str,
        roots: Iterable[str],
        max_kb: int,
        *,
        label: str = "",
        progress: bool = False,
    ) -> list[str]:
        paths: set[str] = set()
        root_list = list(roots)
        bar = Progress(label, len(root_list), enabled=progress) if label else None
        for root in root_list:
            if bar:
                bar.show(root.rsplit("/", 1)[-1] or root)
            output = self.root_shell(
                "find",
                root,
                "-type",
                "f",
                "-size",
                f"-{max_kb}k",
                "-exec",
                "grep",
                "-a",
                "-l",
                pattern,
                "{}",
                "\\;",
                check=False,
            )
            paths.update(line.strip() for line in output.splitlines() if line.startswith("/"))
            if bar:
                bar.step(root.rsplit("/", 1)[-1] or root)
        if bar:
            bar.finish()
        return sorted(paths)

    def remote_file_exists(self, remote_path: str) -> bool:
        result = run_command(
            [self.adb, "-s", self.device, "shell", "su", "0", "test", "-f", remote_path],
            check=False,
        )
        return result.returncode == 0

    def cat_text(self, remote_path: str) -> str:
        return self.root_shell("cat", remote_path)

    def pull_private_file(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote_tmp = f"/sdcard/Download/gakumas_voice_{os.getpid()}_{safe_filename(local_path.name)}"
        self.root_shell("cp", remote_path, remote_tmp)
        self.root_shell("chmod", "644", remote_tmp)
        result = run_command([self.adb, "-s", self.device, "pull", remote_tmp, str(local_path)], check=False)
        self.shell("rm", "-f", remote_tmp, check=False)
        if result.returncode != 0:
            raise ToolError(decode_output(result))


def locate_vgmstream(explicit: str | None) -> str:
    if explicit:
        return explicit
    beside = Path(__file__).with_name("vgmstream-cli.exe")
    if beside.exists():
        return str(beside)
    found = shutil.which("vgmstream-cli.exe") or shutil.which("vgmstream-cli")
    if found:
        return found
    raise ToolError("vgmstream-cli.exe not found; put it in PATH or pass --vgmstream")


def read_existing_voice_map(paths: Iterable[Path]) -> dict[str, str]:
    bank_to_path: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) >= 6 and row[0].startswith("sud_vo_") and row[-1].startswith("/"):
                    bank_to_path[row[0]] = row[-1]
    return bank_to_path


def vgmstream_metadata(vgmstream: str, acb_path: Path, subsong: int | None = None) -> str:
    args = [vgmstream, "-m"]
    if subsong is not None:
        args.extend(["-s", str(subsong)])
    args.append(str(acb_path))
    result = run_command(args)
    return decode_output(result)


def build_subsong_map(vgmstream: str, acb_path: Path, *, progress: bool = False, label: str = "") -> dict[str, int]:
    first_metadata = vgmstream_metadata(vgmstream, acb_path, None)
    count = parse_stream_count(first_metadata)
    mapping: dict[str, int] = {}
    bar = Progress(label or f"scan subsongs {acb_path.name}", count, enabled=progress)
    for index in range(1, count + 1):
        bar.show(str(index))
        metadata = vgmstream_metadata(vgmstream, acb_path, index)
        name = parse_stream_name(metadata)
        if name:
            mapping[name] = index
        bar.step(str(index))
    bar.finish()
    return mapping


def resolve_bank_path(client: AndroidClient, vgmstream: str, bank_name: str, octo_root: str, acb_dir: Path, known: dict[str, str]) -> str:
    if bank_name in known and client.remote_file_exists(known[bank_name]):
        return known[bank_name]

    candidates = client.grep_files(bank_name, octo_root)
    for remote_path in candidates:
        with tempfile.TemporaryDirectory(prefix="gakumas_bank_probe_") as tmp:
            local_probe = Path(tmp) / f"{bank_name}.acb"
            try:
                client.pull_private_file(remote_path, local_probe)
                metadata = vgmstream_metadata(vgmstream, local_probe)
            except Exception:
                continue
            if bank_name in metadata:
                known[bank_name] = remote_path
                return remote_path

    raise ToolError(f"could not resolve ACB path for bank {bank_name}")


def write_voice_line_outputs(
    *,
    line: VoiceLine,
    subsong_index: int,
    acb_path: Path,
    output_root: Path,
    vgmstream: str,
    script_local_path: Path,
) -> Path:
    item_dir = output_root / "items" / line.output_slug
    item_dir.mkdir(parents=True, exist_ok=True)
    wav_path = item_dir / "voice.wav"
    subtitle_path = item_dir / "subtitle.txt"
    metadata_path = item_dir / "metadata.json"

    run_command([vgmstream, "-s", str(subsong_index), "-o", str(wav_path), str(acb_path)])
    subtitle_path.write_text(line.text + "\n", encoding="utf-8")
    metadata = asdict(line)
    metadata.update(
        {
            "subsong_index": subsong_index,
            "acb_file": str(acb_path),
            "script_file": str(script_local_path),
            "wav_file": str(wav_path),
            "subtitle_file": str(subtitle_path),
        }
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return wav_path


def extract_rinha(args: argparse.Namespace) -> int:
    output_root = Path(args.output).resolve()
    scripts_dir = output_root / "scripts"
    acb_dir = output_root / "acb"
    reports_dir = output_root / "reports"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    acb_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()
    vgmstream = locate_vgmstream(args.vgmstream)

    script_roots = split_remote_paths(args.script_root) if args.script_root else split_remote_paths(args.script_roots)
    if not script_roots:
        raise ToolError("no script roots configured")
    if args.script_max_kb <= 0:
        raise ToolError("--script-max-kb must be greater than 0")

    krnh_script_paths = client.grep_text_files(
        f"{args.character_code}-",
        script_roots,
        args.script_max_kb,
        label="search voice",
        progress=args.progress,
    )
    name_script_paths = client.grep_text_files(
        f"name={args.character_name}",
        script_roots,
        args.script_max_kb,
        label="search name",
        progress=args.progress,
    )
    speaker_script_paths = client.grep_text_files(
        f"img_adv_speaker_{args.character_code}",
        script_roots,
        args.script_max_kb,
        label="search speaker",
        progress=args.progress,
    )
    full_name_script_paths = (
        client.grep_text_files(
            args.full_name,
            script_roots,
            args.script_max_kb,
            label="search full name",
            progress=args.progress,
        )
        if args.full_name
        else []
    )
    all_script_paths = sorted(set(krnh_script_paths) | set(name_script_paths) | set(speaker_script_paths) | set(full_name_script_paths))

    print(f"script roots: {len(script_roots)}")
    print(f"scripts with {args.character_code}- voice: {len(krnh_script_paths)}")
    print(f"scripts with name={args.character_name}: {len(name_script_paths)}")
    print(f"scripts with speaker image {args.character_code}: {len(speaker_script_paths)}")
    if args.full_name:
        print(f"scripts mentioning {args.full_name}: {len(full_name_script_paths)}")

    script_texts: dict[str, str] = {}
    script_progress = Progress("pull scripts", len(all_script_paths), enabled=args.progress)
    for remote_path in all_script_paths:
        script_progress.show(remote_tail(remote_path))
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        local_name = safe_filename(remote_path.split("/")[-2] + "_" + remote_path.split("/")[-1]) + ".txt"
        (scripts_dir / local_name).write_text(script_text, encoding="utf-8")
        script_progress.step(remote_tail(remote_path))
    script_progress.finish()

    voice_lines: list[VoiceLine] = []
    parse_progress = Progress("parse scripts", len(script_texts), enabled=args.progress)
    for remote_path, script_text in script_texts.items():
        parse_progress.show(remote_tail(remote_path))
        voice_lines.extend(collect_character_voice_lines(script_text, remote_path, args.character_code))
        parse_progress.step(remote_tail(remote_path))
    parse_progress.finish()

    bank_names = sorted({line.bank_name for line in voice_lines})
    known_maps = read_existing_voice_map([Path(args.voice_map)] if args.voice_map else [])
    if not args.voice_map:
        temp_map = Path(os.environ.get("TEMP", "")) / "mumu_probe" / "sud_vo_map_clean.tsv"
        known_maps.update(read_existing_voice_map([temp_map]))

    bank_remote_paths: dict[str, str] = {}
    bank_local_paths: dict[str, Path] = {}
    bank_subsong_maps: dict[str, dict[str, int]] = {}

    for bank_index, bank_name in enumerate(bank_names, 1):
        print(f"bank {bank_index}/{len(bank_names)}: {bank_name}")
        remote_acb = resolve_bank_path(client, vgmstream, bank_name, args.octo_root, acb_dir, known_maps)
        bank_remote_paths[bank_name] = remote_acb
        local_acb = acb_dir / f"{safe_filename(bank_name)}.acb"
        if not local_acb.exists():
            print(f"pull ACB {bank_name}")
            client.pull_private_file(remote_acb, local_acb)
        bank_local_paths[bank_name] = local_acb
        bank_subsong_maps[bank_name] = build_subsong_map(
            vgmstream,
            local_acb,
            label=f"scan subsongs {bank_index}/{len(bank_names)}",
            progress=args.progress,
        )

    summary_rows = []
    unmatched: list[VoiceLine] = []
    export_progress = Progress("export wav", len(voice_lines), enabled=args.progress)
    for index, line in enumerate(voice_lines, 1):
        export_progress.show(line.voice_cue)
        subsong_index = bank_subsong_maps.get(line.bank_name, {}).get(line.voice_cue)
        if not subsong_index:
            unmatched.append(line)
            export_progress.step(line.voice_cue)
            continue
        script_local_name = safe_filename(line.script_path.split("/")[-2] + "_" + line.script_path.split("/")[-1]) + ".txt"
        wav_path = write_voice_line_outputs(
            line=line,
            subsong_index=subsong_index,
            acb_path=bank_local_paths[line.bank_name],
            output_root=output_root,
            vgmstream=vgmstream,
            script_local_path=scripts_dir / script_local_name,
        )
        summary_rows.append(
            {
                "index": index,
                "speaker": line.speaker,
                "text": tsv_text(line.text),
                "voice_cue": line.voice_cue,
                "bank_name": line.bank_name,
                "subsong_index": subsong_index,
                "wav_file": str(wav_path),
                "script_path": line.script_path,
                "line_no": line.line_no,
                "voice_line_no": line.voice_line_no,
                "acb_path": bank_remote_paths[line.bank_name],
            }
        )
        export_progress.step(line.voice_cue)
    export_progress.finish()

    summary_path = output_root / "rinha_lines.tsv"
    with summary_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "index",
                "speaker",
                "text",
                "voice_cue",
                "bank_name",
                "subsong_index",
                "wav_file",
                "script_path",
                "line_no",
                "voice_line_no",
                "acb_path",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    coverage = {
        "script_root": args.script_root or args.script_roots,
        "script_roots": script_roots,
        "script_max_kb": args.script_max_kb,
        "octo_root": args.octo_root,
        "character_code": args.character_code,
        "character_name": args.character_name,
        "full_name": args.full_name,
        "scripts_with_character_voice": krnh_script_paths,
        "scripts_with_character_name": name_script_paths,
        "scripts_with_character_speaker_image": speaker_script_paths,
        "scripts_with_full_name_text": full_name_script_paths,
        "voice_line_count": len(voice_lines),
        "exported_count": len(summary_rows),
        "unmatched_count": len(unmatched),
        "bank_count": len(bank_names),
        "bank_remote_paths": bank_remote_paths,
    }
    (reports_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    if unmatched:
        (reports_dir / "unmatched.json").write_text(
            json.dumps([asdict(line) for line in unmatched], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"voice lines found: {len(voice_lines)}")
    print(f"voice lines exported: {len(summary_rows)}")
    print(f"summary: {summary_path}")
    print(f"items: {output_root / 'items'}")
    if unmatched:
        print(f"WARN: unmatched lines: {len(unmatched)}")
    return 0 if not unmatched else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract Gakumasu character ADV subtitles and voice WAVs.")
    subparsers = parser.add_subparsers(dest="command")
    extract = subparsers.add_parser("extract-rinha", help="Extract Rinha/Kayo Rinha voice lines.")
    extract.add_argument("--adb", default=DEFAULT_ADB)
    extract.add_argument("--device", default=DEFAULT_DEVICE)
    extract.add_argument("--script-root", default="", help="Single legacy script root. Overrides --script-roots when set.")
    extract.add_argument("--script-roots", default=",".join(DEFAULT_SCRIPT_ROOTS))
    extract.add_argument("--script-max-kb", type=int, default=DEFAULT_SCRIPT_MAX_KB)
    extract.add_argument("--no-progress", action="store_false", dest="progress", help="Disable progress bars.")
    extract.add_argument("--octo-root", default=DEFAULT_OCTO_ROOT)
    extract.add_argument("--character-code", default=DEFAULT_CHARACTER_CODE)
    extract.add_argument("--character-name", default=DEFAULT_CHARACTER_NAME)
    extract.add_argument("--full-name", default="賀陽燐羽")
    extract.add_argument("--voice-map", default="")
    extract.add_argument("--vgmstream", default="")
    extract.add_argument("--output", default=str(Path("output") / "rinha"))
    extract.set_defaults(func=extract_rinha)
    return parser


def main(argv: list[str] | None = None) -> int:
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
