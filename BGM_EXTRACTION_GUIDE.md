# Gakumasu ADV BGM Extraction Guide

This guide records the BGM-specific extraction path learned from
`sud_bgm_adv_foreboding-001`. Use Windows Command Prompt commands; do not give
PowerShell commands to the user.

```text
Workspace:
C:\Repositories\GakumasuVoice

Default device:
127.0.0.1:16384

Game package:
com.bandainamcoent.idolmaster_gakuen

Octo cache root:
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400
```

Audio decoding uses `vgmstream-cli.exe`, usually available from PATH or:

```text
C:\Softwares\vgmstream-cli\vgmstream-cli.exe
```

## Finding The BGM Cue

ADV scripts use `[bgmplay bgm=...]` and `[bgmstop ...]`. If the user gives a
dialogue line or scene description, first find the script, then inspect nearby
lines for `bgmplay`.

Example from the shared idol 10.5 scene:

```cmd
chcp 65001 >nul && adb -s 127.0.0.1:16384 shell su 0 sh -c "find /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400 -type f -size -500k -print0 | xargs -0 -n 100 grep -a -n '承知しました' 2>/dev/null | head -80"
```

Nearby script lines showed:

```text
[bgmplay bgm=sud_bgm_adv_foreboding-001 ...]
name=黒井 ...
name=四音 ...
```

Use the BGM cue name, not the script filename, as the extraction key.

## Locate The ACB

Like voice banks, BGM ACB files in the octo cache are hash-named files without a
`.acb` extension. Search for the cue name in binary files, pull candidate files,
and confirm with `vgmstream-cli -m`.

```cmd
adb -s 127.0.0.1:16384 shell su 0 sh -c "grep -R -a -l 'sud_bgm_adv_foreboding-001' /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400 2>/dev/null"
```

For `sud_bgm_adv_foreboding-001`, the ACB was:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/6/5236303236/b3f0e70aa714f75f0f80a8c6ce8305bb
```

Pull through `/sdcard/Download` because direct reads from `/data/data` need
root:

```cmd
adb -s 127.0.0.1:16384 shell su 0 cp <remote_acb> /sdcard/Download/<cue>.acb
adb -s 127.0.0.1:16384 pull /sdcard/Download/<cue>.acb output\bgm\<cue>\source\<cue>.acb
adb -s 127.0.0.1:16384 shell rm /sdcard/Download/<cue>.acb
vgmstream-cli -m output\bgm\<cue>\source\<cue>.acb
```

## Streaming BGM Uses External AWB

Important pitfall: some BGM ACBs only expose very short `[pre]` streams through
vgmstream when opened alone. `sud_bgm_adv_foreboding-001.acb` decoded only two
about-0.08s streams, but the real BGM was in an external AWB.

The ACB is a CRI `@UTF` table. Parse the root table and read:

```text
StreamAwbHash
StreamAwbAfs2Header
WaveformTable
BlockSequenceTable
BlockTable
```

Minimal `@UTF` parser notes for these ACBs:

```text
All integer fields are big-endian.

@UTF header:
0x00: "@UTF"
0x04: table size (u32)
0x08: version + row offset (u32); row_offset = value & 0xffff
0x0c: string table offset (u32)
0x10: binary/data offset (u32)
0x14: table name string offset (u32)
0x18: column count (u16)
0x1a: row width (u16)
0x1c: row count (u32)

base  = utf_offset + 8
rbase = base + row_offset
sbase = base + string_table_offset
dbase = base + binary_data_offset
```

Each column definition is one flag byte plus a big-endian string offset for the
column name. Use `flag & 0xf0` for storage and `flag & 0x0f` for value type.
The useful storage types here are:

```text
0x50: value is stored per row
0x30: constant value follows the column definition
0x10: zero/null value
```

The useful value types here are:

```text
0x00/0x01: u8
0x02/0x03: u16
0x04/0x05: u32
0x06/0x07: u64
0x08: float
0x09: double
0x0a: string offset relative to sbase
0x0b: binary blob as two u32 values: offset,size relative to dbase
```

For `StreamAwbHash`, the root row contains a binary blob that is itself a nested
`@UTF` table. Parse that table, then read the `Hash` blob. The 16 bytes of that
blob, hex-encoded, are the external AWB filename.

For `sud_bgm_adv_foreboding-001`:

```text
StreamAwbHash row:
Name = sud_bgm_adv_foreboding-001
Hash = c6cffd358e759078aa9f67e9888d682c

WaveformTable:
StreamAwbId 0 -> 155664 samples, 48000 Hz, about 3.243s
StreamAwbId 1 -> 3570576 samples, 48000 Hz, about 74.387s

Cue user data:
{"blockEndPositionMs": [3243.0, 77630.0]}

BlockTable:
block 0 length 3243ms, start 0ms
block 1 length 74387ms, start 3243ms, loop-capable body
```

The 16-byte `StreamAwbHash` is the external AWB filename in octo cache. Locate
it by exact filename:

```cmd
adb -s 127.0.0.1:16384 shell su 0 sh -c "find /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400 -name c6cffd358e759078aa9f67e9888d682c -print"
```

For `sud_bgm_adv_foreboding-001`, the AWB was:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/4/5233393734/c6cffd358e759078aa9f67e9888d682c
```

Confirm the external file starts with `AFS2`:

```cmd
adb -s 127.0.0.1:16384 shell su 0 sh -c "dd if=<remote_awb> bs=64 count=1 2>/dev/null | xxd -g 1"
```

Pull it beside the ACB using the same base name:

```cmd
adb -s 127.0.0.1:16384 shell su 0 cp <remote_awb> /sdcard/Download/<cue>.awb
adb -s 127.0.0.1:16384 pull /sdcard/Download/<cue>.awb output\bgm\<cue>\source\<cue>.awb
adb -s 127.0.0.1:16384 shell rm /sdcard/Download/<cue>.awb
```

## Decode The BGM

Open the AWB directly to see the real BGM streams:

```cmd
vgmstream-cli -m output\bgm\<cue>\source\<cue>.awb
vgmstream-cli -m -s 1 output\bgm\<cue>\source\<cue>.awb
vgmstream-cli -m -s 2 output\bgm\<cue>\source\<cue>.awb
```

For block-based BGM, subsong 1 is usually the intro and subsong 2 is the loop
body. Export both:

```cmd
vgmstream-cli -s 1 -o output\bgm\<cue>\<cue>_01_intro.wav output\bgm\<cue>\source\<cue>.awb
vgmstream-cli -s 2 -o output\bgm\<cue>\<cue>_02_loop.wav output\bgm\<cue>\source\<cue>.awb
```

If the user asks for a single WAV, concatenate the decoded WAV frames in order
when their WAV params match. For `sud_bgm_adv_foreboding-001`, the final file
was:

```text
output\bgm\sud_bgm_adv_foreboding-001\sud_bgm_adv_foreboding-001.wav
48kHz stereo PCM, 3726242 frames, 77.630s
```

If `ffmpeg` is unavailable, Python's standard `wave` module is enough to
concatenate PCM WAV files. Do not concatenate encoded HCA/AWB bytes directly.

## Batch Extraction Logic

A reusable BGM extractor should follow this shape:

1. Scan text-sized ADV files for `[bgmplay bgm=...]`.
2. Collect unique BGM cue names and optionally keep script path/line references.
3. For each cue, locate the ACB by binary grep of the cue name.
4. Pull and validate the ACB with `vgmstream-cli -m`.
5. Parse the ACB `@UTF` tables. If `StreamAwbHash` exists, pull the external AWB
   by hash filename.
6. Decode AWB subsongs directly. Use `BlockTable`/`blockEndPositionMs` to label
   intro/body and to concatenate one-cycle output.
7. If there is no external AWB and vgmstream exposes full streams from the ACB,
   decode from the ACB as usual.
8. Write metadata beside the WAVs: cue, script references, ACB path, AWB path,
   stream count, subsong durations, and final WAV duration.

Do not assume every BGM cue is a single subsong. Do not assume the ACB filename
or directory reveals the cue. Do not trust a tiny `[pre]` decode as the final
BGM when the ACB contains `StreamAwbHash` or `BlockSequenceTable`.

## Minimal Task Template

```text
请阅读 C:\Repositories\GakumasuVoice\BGM_EXTRACTION_GUIDE.md。
使用 Windows Command Prompt，不要用 PowerShell。
从 ADV 脚本定位 <场景/台词> 附近的 [bgmplay bgm=...]。
拉取对应 ACB；如果 ACB 只解出 [pre] 或包含 StreamAwbHash，则按 hash 找外置 AWB。
导出 intro/body WAV，并在需要时拼成单个 WAV。
输出到 output\bgm\<cue>\，source\ 中保留 ACB/AWB。
```
