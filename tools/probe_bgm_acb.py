from __future__ import annotations

import argparse
import struct
from pathlib import Path
from typing import Any


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from(">Q", data, offset)[0]


def read_cstr(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("utf-8", errors="replace")


def parse_utf(data: bytes, offset: int = 0) -> tuple[str, list[dict[str, Any]]]:
    if data[offset : offset + 4] != b"@UTF":
        raise ValueError(f"missing @UTF at 0x{offset:x}")

    base = offset + 8
    row_offset = u32(data, offset + 0x08) & 0xFFFF
    string_table_offset = u32(data, offset + 0x0C)
    binary_data_offset = u32(data, offset + 0x10)
    table_name_offset = u32(data, offset + 0x14)
    column_count = u16(data, offset + 0x18)
    row_width = u16(data, offset + 0x1A)
    row_count = u32(data, offset + 0x1C)

    row_base = base + row_offset
    string_base = base + string_table_offset
    data_base = base + binary_data_offset
    table_name = read_cstr(data, string_base + table_name_offset)

    def read_value(pos: int, value_type: int) -> tuple[Any, int]:
        if value_type in (0x00, 0x01):
            return data[pos], pos + 1
        if value_type in (0x02, 0x03):
            return u16(data, pos), pos + 2
        if value_type in (0x04, 0x05):
            return u32(data, pos), pos + 4
        if value_type in (0x06, 0x07):
            return u64(data, pos), pos + 8
        if value_type == 0x08:
            return struct.unpack_from(">f", data, pos)[0], pos + 4
        if value_type == 0x09:
            return struct.unpack_from(">d", data, pos)[0], pos + 8
        if value_type == 0x0A:
            return read_cstr(data, string_base + u32(data, pos)), pos + 4
        if value_type == 0x0B:
            blob_offset = u32(data, pos)
            blob_size = u32(data, pos + 4)
            if blob_offset == 0xFFFFFFFF:
                return b"", pos + 8
            start = data_base + blob_offset
            return data[start : start + blob_size], pos + 8
        raise ValueError(f"unsupported @UTF value type 0x{value_type:x}")

    columns: list[dict[str, Any]] = []
    pos = offset + 0x20
    for _ in range(column_count):
        flags = data[pos]
        pos += 1
        name = read_cstr(data, string_base + u32(data, pos))
        pos += 4
        storage = flags & 0xF0
        value_type = flags & 0x0F
        const_value = None
        if storage == 0x30:
            const_value, pos = read_value(pos, value_type)
        columns.append(
            {
                "name": name,
                "storage": storage,
                "type": value_type,
                "const": const_value,
            }
        )

    rows: list[dict[str, Any]] = []
    for row_index in range(row_count):
        row_pos = row_base + row_index * row_width
        row: dict[str, Any] = {}
        for column in columns:
            if column["storage"] == 0x50:
                value, row_pos = read_value(row_pos, column["type"])
            elif column["storage"] == 0x30:
                value = column["const"]
            elif column["storage"] == 0x10:
                value = None
            else:
                value = None
            row[column["name"]] = value
        rows.append(row)

    return table_name, rows


def first_blob_table(root_rows: list[dict[str, Any]], name: str) -> tuple[str, list[dict[str, Any]]] | None:
    if not root_rows:
        return None
    blob = root_rows[0].get(name)
    if not isinstance(blob, bytes) or not blob.startswith(b"@UTF"):
        return None
    return parse_utf(blob)


def print_blob_table(root_rows: list[dict[str, Any]], blob_name: str, *, max_rows: int = 8) -> None:
    table = first_blob_table(root_rows, blob_name)
    if not table:
        print(f"{blob_name}: none")
        return

    table_name, rows = table
    print(f"{blob_name}: {table_name} rows={len(rows)}")
    for row in rows[:max_rows]:
        fields = []
        for key, value in row.items():
            if isinstance(value, bytes) or value is None:
                continue
            fields.append(f"{key}={value}")
        print("  " + " ".join(fields))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Gakumasu BGM ACB external AWB metadata.")
    parser.add_argument("acb", nargs="+", type=Path)
    args = parser.parse_args()

    for acb_path in args.acb:
        data = acb_path.read_bytes()
        table_name, root_rows = parse_utf(data)
        print(f"ACB: {acb_path}")
        print(f"root: {table_name} rows={len(root_rows)}")

        stream_awb_hash = first_blob_table(root_rows, "StreamAwbHash")
        if stream_awb_hash:
            hash_table_name, hash_rows = stream_awb_hash
            print(f"StreamAwbHash: {hash_table_name} rows={len(hash_rows)}")
            for row in hash_rows:
                hash_blob = row.get("Hash")
                hash_hex = hash_blob.hex() if isinstance(hash_blob, bytes) else ""
                print(f"  Name={row.get('Name', '')} Hash={hash_hex}")
        else:
            print("StreamAwbHash: none")

        waveform_table = first_blob_table(root_rows, "WaveformTable")
        if waveform_table:
            waveform_table_name, waveform_rows = waveform_table
            print(f"WaveformTable: {waveform_table_name} rows={len(waveform_rows)}")
            for row in waveform_rows[:8]:
                fields = []
                for key in ("Id", "StreamAwbId", "SamplingRate", "NumSamples", "Streaming"):
                    if key in row:
                        fields.append(f"{key}={row[key]}")
                print("  " + " ".join(fields))
        else:
            print("WaveformTable: none")
        print_blob_table(root_rows, "BlockSequenceTable")
        print_blob_table(root_rows, "BlockTable")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
