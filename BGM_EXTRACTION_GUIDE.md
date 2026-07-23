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

## Validated BGM Categories

Small-sample validation on 2026-07-18 showed the tested BGM categories all use
the same high-level packaging: a small hash-named `@UTF` ACB plus a real
external `AFS2` AWB referenced by `StreamAwbHash`.

The ACB is not the final audio when `vgmstream-cli -m <cue>.acb` only shows a
short `[pre]` stream around 0.08-0.10s. Parse the ACB, find the external AWB,
then decode the AWB.

Validated examples:

```text
sud_bgm_adv_foreboding-001
  ACB pre only: 2 streams around 0.086s
  AWB hash: c6cffd358e759078aa9f67e9888d682c
  AWB blocks: 3.243s then 74.387s

sud_bgm_adv_daily-001
  ACB pre only: 2 streams around 0.10s
  AWB hash: e230d43e3414db9af71c4309dc8e21d1
  AWB blocks: 11.388s then 58.192s
  Note: final block order is AWB subsong 2, then AWB subsong 1.

sud_bgm_adv_inst-all-001
  ACB pre only: 1 stream around 0.102s
  AWB hash: 5db19de464da27cc88cbf7d59d9e946e
  AWB: 1 stream, 158.118s

sud_bgm_general_gasha-02
  ACB pre only: 1 stream around 0.089s
  AWB hash: d5a6cffdf874d9e28342a115c5bdfdd0
  AWB: 1 stream, 82.862s

sud_bgm_produce_cmn-01
  ACB pre only: 1 stream around 0.099s
  AWB hash: 49f937a4d14f11a3e784828dbec8c4b7
  AWB: 1 stream, 60.728s

sud_bgm_audition
  ACB pre only: 1 stream around 0.096s
  AWB hash: 6f08f385880456c25e86ae1855c28636
  AWB: 1 stream, 24.267s
```

Use the local probe helper to inspect an ACB:

```cmd
python tools\probe_bgm_acb.py output\bgm\<cue>\source\<cue>.acb
```

### Single-Stream External AWB

When `WaveformTable` has one row and the AWB has one stream, decode the AWB
directly:

```cmd
vgmstream-cli -o output\bgm\<cue>\<cue>.wav output\bgm\<cue>\source\<cue>.awb
```

Equivalent explicit form:

```cmd
vgmstream-cli -s 1 -o output\bgm\<cue>\<cue>.wav output\bgm\<cue>\source\<cue>.awb
```

This covered the validated `adv_inst`, `general`, `produce`, and `audition`
samples.

### Multi-Block External AWB

When `BlockTable` has multiple rows, do not assume AWB subsongs are already in
playback order. Use this mapping:

```text
BlockTable rows give playback order.
BlockTable.Name is a 1-based index into WaveformTable rows.
WaveformTable.StreamAwbId maps to AWB subsong index as StreamAwbId + 1.
```

Then export each block in `BlockTable` order and concatenate decoded PCM WAVs.

For `sud_bgm_adv_foreboding-001`, `BlockTable.Name` maps to AWB subsongs 1 then
2:

```cmd
vgmstream-cli -s 1 -o output\bgm\<cue>\<cue>_01_block.wav output\bgm\<cue>\source\<cue>.awb
vgmstream-cli -s 2 -o output\bgm\<cue>\<cue>_02_block.wav output\bgm\<cue>\source\<cue>.awb
```

For `sud_bgm_adv_daily-001`, `BlockTable.Name` maps to AWB subsongs 2 then 1:

```cmd
vgmstream-cli -s 2 -o output\bgm\sud_bgm_adv_daily-001\sud_bgm_adv_daily-001_01_block.wav output\bgm\sud_bgm_adv_daily-001\source\sud_bgm_adv_daily-001.awb
vgmstream-cli -s 1 -o output\bgm\sud_bgm_adv_daily-001\sud_bgm_adv_daily-001_02_block.wav output\bgm\sud_bgm_adv_daily-001\source\sud_bgm_adv_daily-001.awb
```

Concatenate those decoded WAV files in block order only when a single one-cycle
WAV is desired. Keep separate block WAVs if preserving loop structure matters.

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
