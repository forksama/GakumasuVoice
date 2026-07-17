from __future__ import annotations

import argparse
import builtins
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


DEFAULT_ADB = r"C:\Program Files\NetEase\MuMu Player 12\nx_main\adb.exe"
DEFAULT_DEVICE = "127.0.0.1:16384"
DEFAULT_PACKAGE = "com.bandainamcoent.idolmaster_gakuen"
DEFAULT_OCTO_ROOT = f"/data/data/{DEFAULT_PACKAGE}/files/octo/v1/400"
DEFAULT_SCRIPT_BUCKETS = ("3", "4", "6", "7", "9")
DEFAULT_SCRIPT_ROOTS = tuple(f"{DEFAULT_OCTO_ROOT}/{bucket}" for bucket in DEFAULT_SCRIPT_BUCKETS)
DEFAULT_SCRIPT_ROOT = DEFAULT_SCRIPT_ROOTS[0]
DEFAULT_SCRIPT_MAX_KB = 500
ADV_RUBY_RE = re.compile(r"<r=([^>\r\n]+)>(.*?)</r>")
ADV_TEXT_TAG_RE = re.compile(r"</?[A-Za-z][^>\r\n]*>")

_ORIGINAL_PRINT = builtins.print
_ELAPSED_START: float | None = None


def elapsed_prefix() -> str:
    if _ELAPSED_START is None:
        return ""
    elapsed_seconds = max(0, int(time.monotonic() - _ELAPSED_START))
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"


def elapsed_print(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = False) -> None:
    prefix = elapsed_prefix()
    if not prefix:
        _ORIGINAL_PRINT(*args, sep=sep, end=end, file=file, flush=flush)
        return

    text = sep.join(str(arg) for arg in args)
    lines = text.split("\n")
    prefixed = "\n".join(f"{prefix} {line}" if line else prefix for line in lines)
    _ORIGINAL_PRINT(prefixed, end=end, file=file, flush=flush)


def install_elapsed_printing() -> None:
    global _ELAPSED_START
    _ELAPSED_START = time.monotonic()
    builtins.print = elapsed_print


@dataclass(frozen=True)
class CharacterPreset:
    slug: str
    code: str
    character_name: str
    full_name: str
    english_name: str
    aliases: tuple[str, ...] = ()


IDOL_PRESETS: tuple[CharacterPreset, ...] = (
    CharacterPreset("saki", "hski", "咲季", "花海咲季", "Saki Hanami", ("hanami-saki", "花海咲季", "咲季")),
    CharacterPreset("temari", "ttmr", "手毬", "月村手毬", "Temari Tsukimura", ("tsukimura-temari", "月村手毬", "手毬")),
    CharacterPreset("kotone", "fktn", "ことね", "藤田ことね", "Kotone Fujita", ("fujita-kotone", "藤田ことね", "ことね")),
    CharacterPreset("mao", "amao", "麻央", "有村麻央", "Mao Arimura", ("arimura-mao", "有村麻央", "麻央")),
    CharacterPreset("lilja", "kllj", "リーリヤ", "葛城リーリヤ", "Lilja Katsuragi", ("rirya", "katsuragi-lilja", "葛城リーリヤ", "リーリヤ")),
    CharacterPreset("china", "kcna", "千奈", "倉本千奈", "China Kuramoto", ("kuramoto-china", "倉本千奈", "千奈")),
    CharacterPreset("sumika", "ssmk", "清夏", "紫雲清夏", "Sumika Shiun", ("shiun-sumika", "紫雲清夏", "清夏")),
    CharacterPreset("hiro", "shro", "広", "篠澤広", "Hiro Shinosawa", ("shinosawa-hiro", "篠澤広", "広")),
    CharacterPreset("rinami", "hrnm", "莉波", "姫崎莉波", "Rinami Himesaki", ("himesaki-rinami", "姫崎莉波", "莉波")),
    CharacterPreset("ume", "hume", "佑芽", "花海佑芽", "Ume Hanami", ("hanami-ume", "花海佑芽", "佑芽")),
    CharacterPreset("misuzu", "hmsz", "美鈴", "秦谷美鈴", "Misuzu Hataya", ("hataya-misuzu", "秦谷美鈴", "美鈴")),
    CharacterPreset("sena", "jsna", "星南", "十王星南", "Sena Juo", ("juo-sena", "十王星南", "星南")),
    CharacterPreset("tsubame", "atbm", "燕", "雨夜燕", "Tsubame Amaya", ("amaya-tsubame", "雨夜燕", "燕")),
)
EXTRA_PRESETS: tuple[CharacterPreset, ...] = (
    CharacterPreset(
        "asari",
        "nasr",
        "あさり先生",
        "根緒 亜紗里",
        "Asari Neo",
        ("asari-sensei", "neo-asari", "根緒亜紗里", "根緒 亜紗里", "あさり"),
    ),
    CharacterPreset("rinha", "krnh", "燐羽", "賀陽燐羽", "Rinha Kayo", ("kayo-rinha", "賀陽燐羽", "燐羽")),
)
ALL_PRESETS: tuple[CharacterPreset, ...] = IDOL_PRESETS + EXTRA_PRESETS


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


def normalize_adv_text(value: str) -> str:
    value = value.replace("{user}", "プロデューサー")
    value = ADV_RUBY_RE.sub(lambda match: match.group(1), value)
    value = ADV_TEXT_TAG_RE.sub("", value)
    return value


def tsv_text(value: str) -> str:
    return value.replace("\r", "").replace("\n", r"\n")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value or "item"


def normalize_lookup(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def find_character_preset(value: str) -> CharacterPreset | None:
    needle = normalize_lookup(value)
    if not needle:
        return None
    for preset in ALL_PRESETS:
        candidates = (preset.slug, preset.code, preset.character_name, preset.full_name, preset.english_name, *preset.aliases)
        if needle in {normalize_lookup(candidate) for candidate in candidates}:
            return preset
    return None


def character_table(presets: Iterable[CharacterPreset] = IDOL_PRESETS) -> str:
    lines = ["slug        code  display  full_name       english"]
    for preset in presets:
        lines.append(
            f"{preset.slug:<11} {preset.code:<5} {preset.character_name:<8} {preset.full_name:<13} {preset.english_name}"
        )
    return "\n".join(lines)


def character_help_text() -> str:
    return "\n".join(
        [
            "Character presets (13 idols):",
            character_table(IDOL_PRESETS),
            "",
            "Extra compatible preset:",
            character_table(EXTRA_PRESETS),
            "",
            "Examples:",
            "  python gakumasu_voice.py extract --character kotone --limit 200",
            "  python gakumasu_voice.py extract --character-code fktn --character-name ことね --full-name 藤田ことね --limit 200",
        ]
    )


def short_progress_item(value: str, max_len: int = 48) -> str:
    if len(value) <= max_len:
        return value
    return "..." + value[-(max_len - 3) :]


def remote_tail(remote_path: str) -> str:
    parts = remote_path.strip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else remote_path


def script_local_name(remote_path: str) -> str:
    parts = remote_path.split("/")
    if len(parts) >= 2:
        return safe_filename(parts[-2] + "_" + parts[-1]) + ".txt"
    return safe_filename(remote_path) + ".txt"


def summary_lines_path(output_root: Path, character_slug: str) -> Path:
    return output_root / f"{safe_filename(character_slug)}_lines.tsv"


def default_run_id(limit: int) -> str:
    mode = f"limit{limit}" if limit else "full"
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{mode}"


def make_run_output_root(base_output: Path, character_slug: str, run_id: str) -> Path:
    character_dir = base_output / safe_filename(character_slug)
    candidate = character_dir / safe_filename(run_id)
    if not candidate.exists():
        return candidate
    for suffix in range(2, 1000):
        next_candidate = character_dir / f"{safe_filename(run_id)}_{suffix}"
        if not next_candidate.exists():
            return next_candidate
    raise ToolError(f"could not create a unique run directory under {character_dir}")


def paired_item_paths(items_dir: Path, index: int, line: VoiceLine) -> tuple[Path, Path, Path]:
    prefix = f"{index:04d}_{line.output_slug}"
    subtitle_path = items_dir / f"{prefix}_01_subtitle.txt"
    wav_path = items_dir / f"{prefix}_02_voice.wav"
    metadata_path = items_dir / f"{prefix}_03_metadata.json"
    return subtitle_path, wav_path, metadata_path


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
            text = normalize_adv_text(decode_adv_text(extract_attr(stripped, "text", text_followers)))
            speaker = normalize_adv_text(decode_adv_text(extract_attr(stripped, "name", name_followers)))
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


def count_character_script_voice_subtitles(
    script_texts: dict[str, str],
    character_code: str,
    *,
    character_name: str = "",
    count_display_name: bool = False,
) -> dict:
    voice_cue_count = 0
    voice_subtitle_pair_count = 0
    display_name_subtitle_count = 0 if count_display_name and character_name else None
    bank_names: set[str] = set()

    for script_path, script_text in script_texts.items():
        messages, voices = parse_script_events(script_text, script_path)
        for voice in voices:
            bank_name = character_bank_name(voice.voice_cue, character_code)
            if bank_name:
                voice_cue_count += 1
                bank_names.add(bank_name)
        voice_subtitle_pair_count += len(collect_character_voice_lines(script_text, script_path, character_code))

        if display_name_subtitle_count is not None:
            display_name_subtitle_count += sum(1 for message in messages if message.speaker == character_name)

    return {
        "voice_cue_count": voice_cue_count,
        "voice_subtitle_pair_count": voice_subtitle_pair_count,
        "display_name_subtitle_count": display_name_subtitle_count,
        "bank_count": len(bank_names),
        "bank_names": sorted(bank_names),
    }


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
        prefix = elapsed_prefix()
        message = f"\r{prefix + ' ' if prefix else ''}{self.label}: [{bar}] {shown}/{self.total} {pct:3d}%"
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

    def iter_text_files(self, roots: Iterable[str], max_kb: int) -> Iterator[str]:
        root_list = list(roots)
        if not root_list:
            return
        process = subprocess.Popen(
            [
                self.adb,
                "-s",
                self.device,
                "shell",
                "su",
                "0",
                "find",
                *root_list,
                "-type",
                "f",
                "-size",
                f"-{max_kb}k",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if process.stdout is None:
                return
            for raw_line in process.stdout:
                remote_path = raw_line.decode("utf-8", errors="replace").strip()
                if remote_path.startswith("/"):
                    yield remote_path
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stderr is not None:
                process.stderr.close()

    def text_file_contains(self, remote_path: str, pattern: str) -> bool:
        result = run_command(
            [self.adb, "-s", self.device, "shell", "su", "0", "grep", "-a", "-q", pattern, remote_path],
            check=False,
        )
        return result.returncode == 0

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


@dataclass
class BankCacheStats:
    remote_cache_hits: int = 0
    remote_cache_misses: int = 0
    stale_remote_entries: int = 0
    voice_map_hits: int = 0
    grep_lookups: int = 0
    updated_remote_entries: int = 0
    local_acb_hits: int = 0
    local_acb_pulls: int = 0
    local_acb_copies: int = 0


class BankPathCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {"version": 1, "banks": {}}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"WARN: ignoring invalid bank cache: {path}")
        self.data.setdefault("version", 1)
        self.data.setdefault("banks", {})

    def get_remote_path(self, bank_name: str, octo_root: str) -> str:
        entry = self.data.get("banks", {}).get(bank_name, {})
        if entry.get("octo_root") != octo_root:
            return ""
        remote_path = entry.get("remote_path", "")
        return remote_path if isinstance(remote_path, str) else ""

    def mark_verified(self, bank_name: str) -> None:
        entry = self.data.get("banks", {}).get(bank_name)
        if entry:
            entry["verified_at"] = datetime.now().isoformat(timespec="seconds")

    def update_remote_path(self, bank_name: str, remote_path: str, octo_root: str, source: str) -> None:
        self.data.setdefault("banks", {})[bank_name] = {
            "remote_path": remote_path,
            "octo_root": octo_root,
            "source": source,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "verified_at": datetime.now().isoformat(timespec="seconds"),
        }

    def remove(self, bank_name: str) -> None:
        self.data.setdefault("banks", {}).pop(bank_name, None)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


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


def resolve_bank_path(
    client: AndroidClient,
    vgmstream: str,
    bank_name: str,
    octo_root: str,
    acb_dir: Path,
    known: dict[str, str],
    *,
    bank_cache: BankPathCache | None = None,
    stats: BankCacheStats | None = None,
) -> str:
    if bank_cache:
        cached_remote = bank_cache.get_remote_path(bank_name, octo_root)
        if cached_remote:
            if client.remote_file_exists(cached_remote):
                if stats:
                    stats.remote_cache_hits += 1
                bank_cache.mark_verified(bank_name)
                return cached_remote
            if stats:
                stats.stale_remote_entries += 1
            bank_cache.remove(bank_name)
        elif stats:
            stats.remote_cache_misses += 1

    if bank_name in known and client.remote_file_exists(known[bank_name]):
        if stats:
            stats.voice_map_hits += 1
        if bank_cache:
            bank_cache.update_remote_path(bank_name, known[bank_name], octo_root, "voice_map")
        return known[bank_name]

    if stats:
        stats.grep_lookups += 1
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
                if stats:
                    stats.updated_remote_entries += 1
                if bank_cache:
                    bank_cache.update_remote_path(bank_name, remote_path, octo_root, "grep")
                return remote_path

    raise ToolError(f"could not resolve ACB path for bank {bank_name}")


def acb_sidecar_path(acb_path: Path) -> Path:
    return acb_path.with_suffix(acb_path.suffix + ".json")


def read_acb_sidecar(acb_path: Path) -> dict:
    sidecar = acb_sidecar_path(acb_path)
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_acb_sidecar(acb_path: Path, bank_name: str, remote_acb: str) -> None:
    acb_sidecar_path(acb_path).write_text(
        json.dumps(
            {
                "bank_name": bank_name,
                "remote_acb": remote_acb,
                "cached_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def ensure_local_acb(
    *,
    client: AndroidClient,
    remote_acb: str,
    bank_name: str,
    run_acb_dir: Path,
    shared_acb_dir: Path | None,
    stats: BankCacheStats | None = None,
) -> Path:
    run_acb_dir.mkdir(parents=True, exist_ok=True)
    run_acb = run_acb_dir / f"{safe_filename(bank_name)}.acb"
    if run_acb.exists() and run_acb.stat().st_size > 0:
        if stats:
            stats.local_acb_hits += 1
        return run_acb

    if shared_acb_dir:
        shared_acb_dir.mkdir(parents=True, exist_ok=True)
        shared_acb = shared_acb_dir / f"{safe_filename(bank_name)}.acb"
        if shared_acb.exists() and shared_acb.stat().st_size > 0:
            sidecar = read_acb_sidecar(shared_acb)
            if not sidecar or sidecar.get("remote_acb") == remote_acb:
                shutil.copy2(shared_acb, run_acb)
                if stats:
                    stats.local_acb_hits += 1
                    stats.local_acb_copies += 1
                return run_acb

        print(f"pull ACB {bank_name}")
        client.pull_private_file(remote_acb, shared_acb)
        write_acb_sidecar(shared_acb, bank_name, remote_acb)
        shutil.copy2(shared_acb, run_acb)
        if stats:
            stats.local_acb_pulls += 1
            stats.local_acb_copies += 1
        return run_acb

    print(f"pull ACB {bank_name}")
    client.pull_private_file(remote_acb, run_acb)
    if stats:
        stats.local_acb_pulls += 1
    return run_acb


def collect_limited_character_voice_lines(
    *,
    client: AndroidClient,
    script_roots: Iterable[str],
    script_max_kb: int,
    character_code: str,
    line_limit: int,
    scripts_dir: Path,
    progress: bool,
) -> tuple[dict[str, str], list[VoiceLine], list[str], int, bool]:
    script_texts: dict[str, str] = {}
    voice_lines: list[VoiceLine] = []
    scripts_with_character_voice: list[str] = []
    seen_paths: set[str] = set()
    scanned_text_files = 0
    stopped_early = False
    pattern = f"{character_code}-"

    for remote_path in client.iter_text_files(script_roots, script_max_kb):
        if remote_path in seen_paths:
            continue
        seen_paths.add(remote_path)
        scanned_text_files += 1
        if progress and scanned_text_files % 100 == 0:
            print(f"limited scan: checked {scanned_text_files} text files, collected {len(voice_lines)}/{line_limit}")

        if not client.text_file_contains(remote_path, pattern):
            continue

        scripts_with_character_voice.append(remote_path)
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")

        remaining = line_limit - len(voice_lines)
        voice_lines.extend(collect_character_voice_lines(script_text, remote_path, character_code)[:remaining])
        if progress:
            print(f"limited scan: {remote_tail(remote_path)} -> {len(voice_lines)}/{line_limit}")

        if len(voice_lines) >= line_limit:
            stopped_early = True
            break

    return script_texts, voice_lines, scripts_with_character_voice, scanned_text_files, stopped_early


def write_voice_line_outputs(
    *,
    index: int,
    line: VoiceLine,
    subsong_index: int,
    acb_path: Path,
    items_dir: Path,
    vgmstream: str,
    script_local_path: Path,
) -> tuple[Path, Path, Path]:
    items_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path, wav_path, metadata_path = paired_item_paths(items_dir, index, line)

    run_command([vgmstream, "-s", str(subsong_index), "-o", str(wav_path), str(acb_path)])
    subtitle_path.write_text(line.text + "\n", encoding="utf-8")
    metadata = asdict(line)
    metadata.update(
        {
            "output_index": index,
            "subsong_index": subsong_index,
            "acb_file": str(acb_path),
            "script_file": str(script_local_path),
            "wav_file": str(wav_path),
            "subtitle_file": str(subtitle_path),
            "metadata_file": str(metadata_path),
        }
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return subtitle_path, wav_path, metadata_path


def resolve_character_selection(args: argparse.Namespace) -> tuple[str, str, str, str]:
    preset = find_character_preset(args.character) if args.character else None
    if args.character and not preset and not args.character_code:
        raise ToolError(f"unknown --character {args.character!r}; run `python gakumasu_voice.py characters` to list presets")
    character_code = args.character_code or (preset.code if preset else "")
    character_name = args.character_name or (preset.character_name if preset else "")
    full_name = args.full_name or (preset.full_name if preset else "")
    character_slug = args.character_slug or (preset.slug if preset else character_code)
    if not character_code:
        raise ToolError("--character or --character-code is required")
    if not character_slug:
        raise ToolError("--character-slug is required when using a custom character code")
    return character_code, character_name, full_name, character_slug


def extract_character(args: argparse.Namespace) -> int:
    script_roots = split_remote_paths(args.script_root) if args.script_root else split_remote_paths(args.script_roots)
    if not script_roots:
        raise ToolError("no script roots configured")
    if args.script_max_kb <= 0:
        raise ToolError("--script-max-kb must be greater than 0")
    if args.limit < 0:
        raise ToolError("--limit must be 0 or greater")

    character_code, character_name, full_name, character_slug = resolve_character_selection(args)

    base_output = Path(args.output).resolve()
    line_limit = args.limit or 0
    run_id = args.run_id or default_run_id(line_limit)
    output_root = make_run_output_root(base_output, character_slug, run_id)
    bank_cache_path = None if args.no_bank_cache else Path(args.bank_cache or (base_output / "_cache" / "bank_paths.json")).resolve()
    shared_acb_dir = None if args.no_acb_cache else Path(args.acb_cache_dir or (base_output / "_cache" / "acb")).resolve()
    bank_cache = BankPathCache(bank_cache_path) if bank_cache_path else None
    bank_cache_stats = BankCacheStats()

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()
    vgmstream = locate_vgmstream(args.vgmstream)

    scripts_dir = output_root / "scripts"
    acb_dir = output_root / "acb"
    items_dir = output_root / "items"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    acb_dir.mkdir(parents=True, exist_ok=True)
    items_dir.mkdir(parents=True, exist_ok=True)

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

    if limited_search:
        print(f"limited extraction: stop after {line_limit} voice/subtitle items")
        (
            script_texts,
            voice_lines,
            voice_script_paths,
            scanned_text_file_count,
            stopped_early,
        ) = collect_limited_character_voice_lines(
            client=client,
            script_roots=script_roots,
            script_max_kb=args.script_max_kb,
            character_code=character_code,
            line_limit=line_limit,
            scripts_dir=scripts_dir,
            progress=args.progress,
        )
        name_script_paths: list[str] = []
        speaker_script_paths: list[str] = []
        full_name_script_paths: list[str] = []
        search_complete = not stopped_early
        print(f"script roots: {len(script_roots)}")
        print(f"text files checked before stop: {scanned_text_file_count}")
        print(f"candidate scripts with {character_code}- voice: {len(voice_script_paths)}")
    else:
        voice_script_paths = client.grep_text_files(
            f"{character_code}-",
            script_roots,
            args.script_max_kb,
            label="search voice",
            progress=args.progress,
        )
        name_script_paths = (
            client.grep_text_files(
                f"name={character_name}",
                script_roots,
                args.script_max_kb,
                label="search name",
                progress=args.progress,
            )
            if character_name
            else []
        )
        speaker_script_paths = client.grep_text_files(
            f"img_adv_speaker_{character_code}",
            script_roots,
            args.script_max_kb,
            label="search speaker",
            progress=args.progress,
        )
        full_name_script_paths = (
            client.grep_text_files(
                full_name,
                script_roots,
                args.script_max_kb,
                label="search full name",
                progress=args.progress,
            )
            if full_name
            else []
        )
        all_script_paths = sorted(
            set(voice_script_paths) | set(name_script_paths) | set(speaker_script_paths) | set(full_name_script_paths)
        )

        print(f"script roots: {len(script_roots)}")
        print(f"scripts with {character_code}- voice: {len(voice_script_paths)}")
        if character_name:
            print(f"scripts with name={character_name}: {len(name_script_paths)}")
        print(f"scripts with speaker image {character_code}: {len(speaker_script_paths)}")
        if full_name:
            print(f"scripts mentioning {full_name}: {len(full_name_script_paths)}")

        script_texts = {}
        script_progress = Progress("pull scripts", len(all_script_paths), enabled=args.progress)
        for remote_path in all_script_paths:
            script_progress.show(remote_tail(remote_path))
            script_text = client.cat_text(remote_path)
            script_texts[remote_path] = script_text
            (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")
            script_progress.step(remote_tail(remote_path))
        script_progress.finish()

        voice_lines = []
        parse_progress = Progress("parse scripts", len(script_texts), enabled=args.progress)
        for remote_path, script_text in script_texts.items():
            parse_progress.show(remote_tail(remote_path))
            voice_lines.extend(collect_character_voice_lines(script_text, remote_path, character_code))
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
        subtitle_path, wav_path, metadata_path = write_voice_line_outputs(
            index=index,
            line=line,
            subsong_index=subsong_index,
            acb_path=bank_local_paths[line.bank_name],
            items_dir=items_dir,
            vgmstream=vgmstream,
            script_local_path=scripts_dir / script_local_name(line.script_path),
        )
        summary_rows.append(
            {
                "index": index,
                "speaker": line.speaker,
                "text": tsv_text(line.text),
                "voice_cue": line.voice_cue,
                "bank_name": line.bank_name,
                "subsong_index": subsong_index,
                "subtitle_file": str(subtitle_path),
                "wav_file": str(wav_path),
                "metadata_file": str(metadata_path),
                "script_path": line.script_path,
                "line_no": line.line_no,
                "voice_line_no": line.voice_line_no,
                "acb_path": bank_remote_paths[line.bank_name],
            }
        )
        export_progress.step(line.voice_cue)
    export_progress.finish()

    summary_path = summary_lines_path(output_root, character_slug)
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
                "subtitle_file",
                "wav_file",
                "metadata_file",
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
        "voice_line_count": len(voice_lines),
        "exported_count": len(summary_rows),
        "unmatched_count": len(unmatched),
        "bank_count": len(bank_names),
        "bank_remote_paths": bank_remote_paths,
        "bank_cache_path": str(bank_cache_path) if bank_cache_path else "",
        "acb_cache_dir": str(shared_acb_dir) if shared_acb_dir else "",
        "bank_cache_stats": asdict(bank_cache_stats),
    }
    (output_root / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    if unmatched:
        (output_root / "unmatched.json").write_text(
            json.dumps([asdict(line) for line in unmatched], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"voice lines found: {len(voice_lines)}")
    print(f"voice lines exported: {len(summary_rows)}")
    print(f"summary: {summary_path}")
    print(f"items: {output_root / 'items'}")
    print(f"bank cache stats: {asdict(bank_cache_stats)}")
    if unmatched:
        print(f"WARN: unmatched lines: {len(unmatched)}")
    return 0 if not unmatched else 2


def count_character(args: argparse.Namespace) -> int:
    script_roots = split_remote_paths(args.script_root) if args.script_root else split_remote_paths(args.script_roots)
    if not script_roots:
        raise ToolError("no script roots configured")
    if args.script_max_kb <= 0:
        raise ToolError("--script-max-kb must be greater than 0")

    character_code, character_name, full_name, character_slug = resolve_character_selection(args)
    base_output = Path(args.output).resolve()
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_count"
    output_root = make_run_output_root(base_output, character_slug, run_id)
    scripts_dir = output_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()

    print(f"character: {character_slug} ({character_code})")
    if character_name or full_name:
        print(f"name filters: display={character_name or '-'} full={full_name or '-'}")
    print(f"output run: {output_root}")
    print("count mode: scripts only; no ACB lookup or WAV export")

    if args.progress:
        print(f"searching voice scripts for {character_code}- in {len(script_roots)} roots", flush=True)
    voice_script_paths = client.grep_text_files(
        f"{character_code}-",
        script_roots,
        args.script_max_kb,
        label="search voice",
        progress=args.progress,
    )
    if args.progress:
        print(f"voice script search done: {len(voice_script_paths)} scripts", flush=True)
    name_script_paths = (
        client.grep_text_files(
            f"name={character_name}",
            script_roots,
            args.script_max_kb,
            label="search name",
            progress=args.progress,
        )
        if args.include_name_count and character_name
        else []
    )
    all_script_paths = sorted(set(voice_script_paths) | set(name_script_paths))

    print(f"script roots: {len(script_roots)}")
    print(f"scripts with {character_code}- voice: {len(voice_script_paths)}")
    if args.include_name_count and character_name:
        print(f"scripts with name={character_name}: {len(name_script_paths)}")

    script_texts: dict[str, str] = {}
    script_progress = Progress("pull scripts", len(all_script_paths), enabled=args.progress)
    for remote_path in all_script_paths:
        script_progress.show(remote_tail(remote_path))
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")
        script_progress.step(remote_tail(remote_path))
    script_progress.finish()

    counts = count_character_script_voice_subtitles(
        script_texts,
        character_code,
        character_name=character_name,
        count_display_name=args.include_name_count,
    )
    report = {
        "script_root": args.script_root or args.script_roots,
        "script_roots": script_roots,
        "script_max_kb": args.script_max_kb,
        "output_base": str(base_output),
        "output_root": str(output_root),
        "run_id": output_root.name,
        "character_slug": character_slug,
        "character_code": character_code,
        "character_name": character_name,
        "full_name": full_name,
        "include_name_count": args.include_name_count,
        "candidate_script_count": len(all_script_paths),
        "scripts_with_character_voice": voice_script_paths,
        "scripts_with_character_name": name_script_paths,
        **counts,
    }
    report_path = output_root / "count.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"voice cue count: {counts['voice_cue_count']}")
    print(f"voice/subtitle pairs: {counts['voice_subtitle_pair_count']}")
    if args.include_name_count and character_name:
        print(f"display-name subtitle count: {counts['display_name_subtitle_count']}")
    print(f"bank count: {counts['bank_count']}")
    print(f"count report: {report_path}")
    return 0


def write_text_only_line_summary(output_root: Path, character_slug: str, voice_lines: list[VoiceLine]) -> Path:
    summary_path = summary_lines_path(output_root, character_slug)
    with summary_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "index",
                "speaker",
                "text",
                "voice_cue",
                "bank_name",
                "script_path",
                "line_no",
                "voice_line_no",
                "start",
                "duration",
                "voice_start",
                "voice_duration",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for index, line in enumerate(voice_lines, 1):
            writer.writerow(
                {
                    "index": index,
                    "speaker": line.speaker,
                    "text": tsv_text(line.text),
                    "voice_cue": line.voice_cue,
                    "bank_name": line.bank_name,
                    "script_path": line.script_path,
                    "line_no": line.line_no,
                    "voice_line_no": line.voice_line_no,
                    "start": line.start,
                    "duration": line.duration,
                    "voice_start": line.voice_start,
                    "voice_duration": line.voice_duration,
                }
            )
    return summary_path


def lines_character(args: argparse.Namespace) -> int:
    script_roots = split_remote_paths(args.script_root) if args.script_root else split_remote_paths(args.script_roots)
    if not script_roots:
        raise ToolError("no script roots configured")
    if args.script_max_kb <= 0:
        raise ToolError("--script-max-kb must be greater than 0")

    character_code, character_name, full_name, character_slug = resolve_character_selection(args)
    base_output = Path(args.output).resolve()
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_lines"
    output_root = make_run_output_root(base_output, character_slug, run_id)
    scripts_dir = output_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    client = AndroidClient(args.adb, args.device)
    client.ensure_connected()

    print(f"character: {character_slug} ({character_code})")
    if character_name or full_name:
        print(f"name filters: display={character_name or '-'} full={full_name or '-'}")
    print(f"output run: {output_root}")
    print("lines mode: scripts only; no ACB lookup or WAV export")

    if args.progress:
        print(f"searching voice scripts for {character_code}- in {len(script_roots)} roots", flush=True)
    voice_script_paths = client.grep_text_files(
        f"{character_code}-",
        script_roots,
        args.script_max_kb,
        label="search voice",
        progress=args.progress,
    )
    if args.progress:
        print(f"voice script search done: {len(voice_script_paths)} scripts", flush=True)

    print(f"script roots: {len(script_roots)}")
    print(f"scripts with {character_code}- voice: {len(voice_script_paths)}")

    script_texts: dict[str, str] = {}
    script_progress = Progress("pull scripts", len(voice_script_paths), enabled=args.progress)
    for remote_path in voice_script_paths:
        script_progress.show(remote_tail(remote_path))
        script_text = client.cat_text(remote_path)
        script_texts[remote_path] = script_text
        (scripts_dir / script_local_name(remote_path)).write_text(script_text, encoding="utf-8")
        script_progress.step(remote_tail(remote_path))
    script_progress.finish()

    voice_lines: list[VoiceLine] = []
    parse_progress = Progress("parse scripts", len(script_texts), enabled=args.progress)
    for remote_path, script_text in script_texts.items():
        parse_progress.show(remote_tail(remote_path))
        voice_lines.extend(collect_character_voice_lines(script_text, remote_path, character_code))
        parse_progress.step(remote_tail(remote_path))
    parse_progress.finish()

    summary_path = write_text_only_line_summary(output_root, character_slug, voice_lines)
    report = {
        "script_root": args.script_root or args.script_roots,
        "script_roots": script_roots,
        "script_max_kb": args.script_max_kb,
        "output_base": str(base_output),
        "output_root": str(output_root),
        "run_id": output_root.name,
        "character_slug": character_slug,
        "character_code": character_code,
        "character_name": character_name,
        "full_name": full_name,
        "scripts_with_character_voice": voice_script_paths,
        "voice_subtitle_pair_count": len(voice_lines),
        "bank_count": len({line.bank_name for line in voice_lines}),
        "summary_path": str(summary_path),
    }
    report_path = output_root / "lines.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"voice/subtitle pairs: {len(voice_lines)}")
    print(f"bank count: {report['bank_count']}")
    print(f"summary: {summary_path}")
    print(f"line report: {report_path}")
    return 0


def extract_rinha(args: argparse.Namespace) -> int:
    return extract_character(args)


def print_characters(args: argparse.Namespace) -> int:
    print(character_help_text())
    return 0


def add_extract_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_character: str = "",
    default_output: Path = Path("output"),
) -> None:
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--script-root", default="", help="Single legacy script root. Overrides --script-roots when set.")
    parser.add_argument("--script-roots", default=",".join(DEFAULT_SCRIPT_ROOTS))
    parser.add_argument("--script-max-kb", type=int, default=DEFAULT_SCRIPT_MAX_KB)
    parser.add_argument("--no-progress", action="store_false", dest="progress", help="Disable progress bars.")
    parser.add_argument("--octo-root", default=DEFAULT_OCTO_ROOT)
    parser.add_argument("--character", default=default_character, help="Preset slug/code/name, e.g. kotone, fktn, 藤田ことね.")
    parser.add_argument("--character-code", default="", help="Override or provide the voice cue code, e.g. fktn.")
    parser.add_argument("--character-name", default="", help="Override or provide the ADV display name, e.g. ことね.")
    parser.add_argument("--full-name", default="", help="Override or provide the full name search text, e.g. 藤田ことね.")
    parser.add_argument("--character-slug", default="", help="Override output character directory name.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N matched voice/subtitle items. 0 means full extraction.")
    parser.add_argument("--run-id", default="", help="Run subdirectory name. Defaults to timestamp plus full/limitN.")
    parser.add_argument("--voice-map", default="")
    parser.add_argument("--bank-cache", default="", help="Persistent bank_name to remote ACB path cache. Defaults to <output>\\_cache\\bank_paths.json.")
    parser.add_argument("--no-bank-cache", action="store_true", help="Disable persistent remote bank path cache.")
    parser.add_argument("--acb-cache-dir", default="", help="Shared local ACB cache directory. Defaults to <output>\\_cache\\acb.")
    parser.add_argument("--no-acb-cache", action="store_true", help="Disable shared local ACB cache.")
    parser.add_argument("--vgmstream", default="")
    parser.add_argument("--output", default=str(default_output), help="Base output directory; creates <output>\\<character>\\<run-id>.")
    parser.set_defaults(func=extract_character)


def add_count_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_character: str = "",
    default_output: Path = Path("output"),
) -> None:
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--script-root", default="", help="Single legacy script root. Overrides --script-roots when set.")
    parser.add_argument("--script-roots", default=",".join(DEFAULT_SCRIPT_ROOTS))
    parser.add_argument("--script-max-kb", type=int, default=DEFAULT_SCRIPT_MAX_KB)
    parser.add_argument("--no-progress", action="store_false", dest="progress", help="Disable progress bars.")
    parser.add_argument("--character", default=default_character, help="Preset slug/code/name, e.g. asari, nasr, kotone.")
    parser.add_argument("--character-code", default="", help="Override or provide the voice cue code, e.g. nasr.")
    parser.add_argument("--character-name", default="", help="Override or provide the ADV display name, e.g. あさり先生.")
    parser.add_argument("--full-name", default="", help="Override or provide the full name search text, e.g. 根緒 亜紗里.")
    parser.add_argument("--character-slug", default="", help="Override output character directory name.")
    parser.add_argument("--include-name-count", action="store_true", help="Also count message subtitles whose name= display text matches the character.")
    parser.add_argument("--run-id", default="", help="Run subdirectory name. Defaults to timestamp plus count.")
    parser.add_argument("--output", default=str(default_output), help="Base output directory; creates <output>\\<character>\\<run-id>.")
    parser.set_defaults(func=count_character)


def add_lines_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_character: str = "",
    default_output: Path = Path("output"),
) -> None:
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--script-root", default="", help="Single legacy script root. Overrides --script-roots when set.")
    parser.add_argument("--script-roots", default=",".join(DEFAULT_SCRIPT_ROOTS))
    parser.add_argument("--script-max-kb", type=int, default=DEFAULT_SCRIPT_MAX_KB)
    parser.add_argument("--no-progress", action="store_false", dest="progress", help="Disable progress bars.")
    parser.add_argument("--character", default=default_character, help="Preset slug/code/name, e.g. asari, nasr, kotone.")
    parser.add_argument("--character-code", default="", help="Override or provide the voice cue code, e.g. nasr.")
    parser.add_argument("--character-name", default="", help="Override or provide the ADV display name, e.g. あさり先生.")
    parser.add_argument("--full-name", default="", help="Override or provide the full name search text, e.g. 根緒 亜紗里.")
    parser.add_argument("--character-slug", default="", help="Override output character directory name.")
    parser.add_argument("--run-id", default="", help="Run subdirectory name. Defaults to timestamp plus lines.")
    parser.add_argument("--output", default=str(default_output), help="Base output directory; creates <output>\\<character>\\<run-id>.")
    parser.set_defaults(func=lines_character)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Gakumasu character ADV subtitles and voice WAVs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    subparsers = parser.add_subparsers(dest="command")
    extract = subparsers.add_parser(
        "extract",
        help="Extract a character selected by --character or --character-code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    add_extract_arguments(extract)
    count = subparsers.add_parser(
        "count",
        help="Count character ADV voice/subtitle pairs without ACB lookup or WAV export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    add_count_arguments(count)
    lines = subparsers.add_parser(
        "lines",
        help="Write character ADV voice/subtitle text lines without ACB lookup or WAV export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    add_lines_arguments(lines)
    extract_character_legacy = subparsers.add_parser(
        "extract-character",
        help="Compatibility alias for extract.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=character_help_text(),
    )
    add_extract_arguments(extract_character_legacy)
    rinha = subparsers.add_parser("extract-rinha", help="Extract Rinha/Kayo Rinha voice lines.")
    add_extract_arguments(rinha, default_character="rinha")
    kotone = subparsers.add_parser("extract-kotone", help="Extract Kotone/Fujita Kotone voice lines.")
    add_extract_arguments(kotone, default_character="kotone")
    characters = subparsers.add_parser("characters", help="List built-in character presets.")
    characters.set_defaults(func=print_characters)
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
