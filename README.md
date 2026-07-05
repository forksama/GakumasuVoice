# GakumasuVoice

Extract Gakumasu ADV voice lines from a MuMu Player 12 Android cache.

## Rinha Extraction

Run from Windows Command Prompt:

```cmd
cd /d C:\Repositories\GakumasuVoice
extract_rinha_assets.bat
```

The extractor uses `voice=..._krnh-...` as the authoritative Rinha voice marker.
`name=燐羽` alone is not enough, because some scripts mention or display the name
without containing a Rinha subsong, and some Rinha voice cues can use another
display name.

By default it searches ADV text scripts in these MuMu cache buckets:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/3
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/4
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/6
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/7
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/9
```

The search is limited to small text-like files by default (`--script-max-kb 500`)
so ACB/audio and large binary asset bundles do not dominate the scan. Override
with `--script-roots` for a comma-separated list, or `--script-root` for one
legacy root.

Progress bars are shown by default for the slow stages: script search, script
pulling, parsing, subsong scanning, and WAV export. Use `--no-progress` to print
plain logs only.

Default output:

```text
output\rinha\rinha_lines.tsv
output\rinha\items\<voice_cue>\subtitle.txt
output\rinha\items\<voice_cue>\voice.wav
output\rinha\items\<voice_cue>\metadata.json
output\rinha\reports\coverage.json
output\rinha\scripts\
output\rinha\acb\
```

Each folder under `output\rinha\items` is one matched subtitle/subsong pair.
