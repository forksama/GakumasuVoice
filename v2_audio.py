"""WAV inspection and the v2 duration-bucket policy.

Two things live here, shared by the extractor, the duration splitter and the
verifier so the rule cannot drift:

* reading a WAV's real duration and format from its header (no decoder needed),
* deciding which bucket a duration falls into.

Duration is read from the file rather than trusted from script metadata.  Measured
over all 1764 items migrated during the v2 work, every file was 48 kHz / 16-bit /
mono and the script's declared ``voice_duration`` picked the same bucket as the real
audio in 1764 of 1764 cases (largest gap 0.24 s), so the two agree -- but the file is
what the user actually hears, so the file wins.

Buckets are inclusive at both ends: ``min_seconds <= seconds <= max_seconds`` is
``keep``; anything else is ``reject``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: Inclusive lower bound of the wanted duration, in seconds.
DEFAULT_MIN_SECONDS = 3.0
#: Inclusive upper bound of the wanted duration, in seconds.
DEFAULT_MAX_SECONDS = 10.0

BUCKET_KEEP = "keep"
BUCKET_REJECT = "reject"
BUCKETS = (BUCKET_KEEP, BUCKET_REJECT)

_HEADER_BYTES = 12


class WavFormatError(RuntimeError):
    pass


@dataclass(frozen=True)
class WavInfo:
    seconds: float
    sample_rate: int
    channels: int
    bits_per_sample: int
    byte_rate: int
    data_bytes: int
    file_bytes: int


def _iter_chunks(handle, file_bytes: int):
    while True:
        header = handle.read(8)
        if len(header) < 8:
            return
        chunk_id, chunk_size = struct.unpack("<4sI", header)
        payload_start = handle.tell()
        yield chunk_id, chunk_size, payload_start
        handle.seek(payload_start + chunk_size + (chunk_size % 2))


def read_wav_info(path: Path) -> WavInfo:
    """Return the real duration and format of a WAV file.

    Raises :class:`WavFormatError` when the file is not a well-formed,
    non-truncated RIFF/WAVE file.
    """
    file_bytes = path.stat().st_size
    if file_bytes < 44:
        raise WavFormatError(f"{path.name}: smaller than a WAV header ({file_bytes} bytes)")

    with path.open("rb") as handle:
        header = handle.read(_HEADER_BYTES)
        if len(header) < _HEADER_BYTES or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise WavFormatError(f"{path.name}: missing RIFF/WAVE signature")
        declared = struct.unpack("<I", header[4:8])[0]
        if declared + 8 != file_bytes:
            raise WavFormatError(f"{path.name}: RIFF size {declared + 8} != file size {file_bytes}")

        fmt: tuple[int, int, int, int] | None = None
        data_bytes: int | None = None
        for chunk_id, chunk_size, payload_start in _iter_chunks(handle, file_bytes):
            if chunk_id == b"fmt ":
                body = handle.read(chunk_size)
                if len(body) < 16:
                    raise WavFormatError(f"{path.name}: short fmt chunk")
                _tag, channels, sample_rate, byte_rate, _align, bits = struct.unpack("<HHIIHH", body[:16])
                fmt = (channels, sample_rate, byte_rate, bits)
            elif chunk_id == b"data":
                remaining = file_bytes - payload_start
                if chunk_size > remaining:
                    raise WavFormatError(f"{path.name}: data chunk {chunk_size} exceeds remaining {remaining}")
                if chunk_size < remaining - 1:
                    raise WavFormatError(f"{path.name}: data chunk {chunk_size} does not cover remaining {remaining}")
                data_bytes = chunk_size
                break

    if fmt is None:
        raise WavFormatError(f"{path.name}: no fmt chunk")
    if data_bytes is None:
        raise WavFormatError(f"{path.name}: no data chunk")

    channels, sample_rate, byte_rate, bits = fmt
    if byte_rate <= 0:
        byte_rate = sample_rate * channels * max(bits, 8) // 8
    if byte_rate <= 0:
        raise WavFormatError(f"{path.name}: unusable byte rate")

    return WavInfo(
        seconds=data_bytes / byte_rate,
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits,
        byte_rate=byte_rate,
        data_bytes=data_bytes,
        file_bytes=file_bytes,
    )


def wav_problem(path: Path) -> str:
    """``""`` when the file is a valid, non-truncated WAV, else the reason."""
    try:
        read_wav_info(path)
    except (WavFormatError, OSError) as exc:
        return str(exc)
    return ""


def bucket_for_duration(
    seconds: float,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> str:
    """``keep`` when ``min_seconds <= seconds <= max_seconds`` (both inclusive)."""
    return BUCKET_KEEP if min_seconds <= seconds <= max_seconds else BUCKET_REJECT


def bucket_dir(items_dir: Path, bucket: str) -> Path:
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r}")
    return items_dir / bucket


def item_paths_in_bucket(items_dir: Path, bucket: str, stem: str) -> tuple[Path, Path]:
    directory = bucket_dir(items_dir, bucket)
    return directory / f"{stem}.wav", directory / f"{stem}.json"
