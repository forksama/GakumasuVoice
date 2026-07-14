# GakumasuVoice

Extract Gakumasu ADV voice lines from a MuMu Player 12 Android cache.

## Usage

Run from Windows Command Prompt:

```cmd
cd /d C:\Repositories\GakumasuVoice
extract_character_assets.bat --character kotone --limit 200
```

Equivalent Python command:

```cmd
python gakumasu_voice.py extract --character kotone --limit 200
```

List built-in presets:

```cmd
python gakumasu_voice.py characters
python gakumasu_voice.py extract --help
```

The extractor uses `voice=..._<character_code>-...` as the authoritative character
voice marker. `name=<display name>` alone is not enough, because scripts can show
one name while the actual voice cue belongs to another character.

Use `--limit N` to stop after exporting N matched voice/subtitle items. Limited
mode streams candidate text files and stops scanning once enough target voice
lines have been collected, so `--limit 200` avoids scanning the whole cache.

Bank lookup is cached by default. The first run still has to locate each new
`bank_name`, but later runs reuse `output\_cache\bank_paths.json` after verifying
that the remote file still exists. ACB files are also cached under
`output\_cache\acb` and copied into each run's `acb` folder, so each run remains
self-contained without repeating slow `adb pull` work.

## Character Presets

Pass any `slug`, `code`, display name, or full name to `--character`.

| slug | code | display | full name | English |
| --- | --- | --- | --- | --- |
| saki | hski | 咲季 | 花海咲季 | Saki Hanami |
| temari | ttmr | 手毬 | 月村手毬 | Temari Tsukimura |
| kotone | fktn | ことね | 藤田ことね | Kotone Fujita |
| mao | amao | 麻央 | 有村麻央 | Mao Arimura |
| lilja | kllj | リーリヤ | 葛城リーリヤ | Lilja Katsuragi |
| china | kcna | 千奈 | 倉本千奈 | China Kuramoto |
| sumika | ssmk | 清夏 | 紫雲清夏 | Sumika Shiun |
| hiro | shro | 広 | 篠澤広 | Hiro Shinosawa |
| rinami | hrnm | 莉波 | 姫崎莉波 | Rinami Himesaki |
| ume | hume | 佑芽 | 花海佑芽 | Ume Hanami |
| misuzu | hmsz | 美鈴 | 秦谷美鈴 | Misuzu Hataya |
| sena | jsna | 星南 | 十王星南 | Sena Juo |
| tsubame | atbm | 燕 | 雨夜燕 | Tsubame Amaya |

`rinha` / `krnh` remains available as a compatibility preset for the old Rinha
workflow.

For a custom or newly discovered character:

```cmd
python gakumasu_voice.py extract --character-code fktn --character-name ことね --full-name 藤田ことね --character-slug kotone --limit 200
```

## Output Layout

Each run creates a fresh subdirectory under the character directory:

```text
output\<character_slug>\<run_id>\
output\<character_slug>\<run_id>\<character_slug>_lines.tsv
output\<character_slug>\<run_id>\coverage.json
output\<character_slug>\<run_id>\unmatched.json
output\<character_slug>\<run_id>\items\
output\<character_slug>\<run_id>\scripts\
output\<character_slug>\<run_id>\acb\
```

Subtitle, voice, and metadata files are flat in `items` and grouped by the same
index and voice cue, so sorting by name keeps each pair adjacent:

```text
items\0001_sud_vo_adv_demo_001_fktn-001_01_subtitle.txt
items\0001_sud_vo_adv_demo_001_fktn-001_02_voice.wav
items\0001_sud_vo_adv_demo_001_fktn-001_03_metadata.json
items\0002_sud_vo_adv_demo_001_fktn-002_01_subtitle.txt
items\0002_sud_vo_adv_demo_001_fktn-002_02_voice.wav
items\0002_sud_vo_adv_demo_001_fktn-002_03_metadata.json
```

Verify the latest run under `output`, a character directory, or a specific run:

```cmd
python verify_outputs.py --output output\kotone
```

## Search Roots

By default it searches ADV text scripts in these MuMu cache buckets:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/3
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/4
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/6
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/7
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/9
```

The search is limited to small text-like files by default (`--script-max-kb 500`).
Override with `--script-roots` for a comma-separated list, or `--script-root` for
one legacy root.

## Cache Options

Useful cache-related parameters:

```cmd
python gakumasu_voice.py extract --character kotone --limit 200 --bank-cache output\_cache\bank_paths.json --acb-cache-dir output\_cache\acb
python gakumasu_voice.py extract --character kotone --limit 200 --no-bank-cache
python gakumasu_voice.py extract --character kotone --limit 200 --no-acb-cache
```

`--bank-cache` avoids repeated remote `grep -R` bank discovery. If a cached
remote path disappears, the extractor invalidates that entry and re-runs discovery
only for that bank, then writes the fresh path back.
