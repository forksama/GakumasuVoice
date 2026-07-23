# Gakumasu Background Asset Extraction Guide

This guide records the validated workflow for pulling ADV / character-story
background assets from the Android emulator cache.

Validated on 2026-07-23.

## Summary

Story backgrounds are mixed:

- `env_2d_adv_*`: 2D background image usage.
- `env_3d_adv_*`: Unity 3D scene/model usage.
- `img_adv_still_*`: still/cutscene image usage.

The 2D backgrounds can be pulled from Octo cache UnityFS bundles and exported as
Texture2D PNG files. The validated extraction produced:

```text
output\background_assets\source\a_bgsize_candidates
  835 bundles, about 689.7 MB

output\background_assets\extracted\a_bgsize_wide_textures
  214 PNGs
  211 files at 2048x1024
  3 files at 1024x2048

output\background_assets\extracted\a_bgsize_wide_contact_sheet.jpg
```

3D scene bundles can also be pulled, but sampled large scene bundles produced
material textures and texture atlases rather than ready-to-use panorama renders.
Treat 3D panorama extraction as a rendering or screenshot-stitching task.

## Emulator Cache

Package:

```text
com.bandainamcoent.idolmaster_gakuen
```

Known working emulator endpoint:

```text
127.0.0.1:16384
```

Cache root:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400
```

A-cache paths have this shape:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/<last-digit>/<hex-ascii-A-id>/<hash>
```

Example:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/1/4133303431/db6c034137f9fb2a17bbafbb5e6f08f1
```

`4133303431` decodes to `A3041`.

## Local Tools

Helper scripts:

```text
tools\apk_urls.py
tools\export_unity_textures.py
tools\filter_bg_candidates.py
tools\inspect_unity_bundle.py
tools\make_contact_sheet.py
tools\pull_adb_paths.py
tools\stat_adb_paths.py
tools\summarize_unity_bundles.py
```

Install Python dependencies when needed:

```cmd
python -m pip install UnityPy Pillow
```

`export_unity_textures.py` sets the Unity fallback version to `6000.0.77f1`;
UnityPy fallback warnings are expected for these cache bundles.

## Reuse Existing Candidate TSV

If `output\background_assets\source\a_bgsize_candidates.tsv` exists:

```cmd
adb connect 127.0.0.1:16384
python tools\pull_adb_paths.py --list output\background_assets\source\a_bgsize_candidates.tsv --out output\background_assets\source\a_bgsize_candidates
python tools\export_unity_textures.py output\background_assets\source\a_bgsize_candidates\*.bundle --out output\background_assets\extracted\a_bgsize_wide_textures --min-area 500000 --non-square-ratio 1.3
python tools\make_contact_sheet.py output\background_assets\extracted\a_bgsize_wide_textures --out output\background_assets\extracted\a_bgsize_wide_contact_sheet.jpg --count 120 --cols 4 --thumb-w 260 --thumb-h 140
```

## Full Discovery Flow

Connect and verify root access:

```cmd
adb connect 127.0.0.1:16384
adb -s 127.0.0.1:16384 shell su 0 ls /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400
```

Dump cache paths:

```cmd
adb -s 127.0.0.1:16384 shell su 0 sh -c "find /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400 -type f" > output\background_assets\source\a_paths_raw.txt
```

Keep likely A-asset paths:

```cmd
python -c "from pathlib import Path; src=Path('output/background_assets/source/a_paths_raw.txt'); out=Path('output/background_assets/source/a_paths.txt'); lines=[p.strip() for p in src.read_text(encoding='utf-8', errors='ignore').splitlines() if '/41' in p and p.strip()]; out.parent.mkdir(parents=True, exist_ok=True); out.write_text('\n'.join(lines)+'\n', encoding='utf-8'); print(len(lines), out)"
```

Stat sizes:

```cmd
python tools\stat_adb_paths.py --paths output\background_assets\source\a_paths.txt --out output\background_assets\source\a_sizes.tsv
```

Filter likely 2D background bundles. The validated heuristic is size
`780000..980000` bytes and decoded A-id under `9000`:

```cmd
python tools\filter_bg_candidates.py --sizes output\background_assets\source\a_sizes.tsv --out output\background_assets\source\a_bgsize_candidates.tsv
```

Expected result for the validated cache:

```text
835 candidates, 689.7 MB
```

Pull, export, and make a contact sheet:

```cmd
python tools\pull_adb_paths.py --list output\background_assets\source\a_bgsize_candidates.tsv --out output\background_assets\source\a_bgsize_candidates
python tools\export_unity_textures.py output\background_assets\source\a_bgsize_candidates\*.bundle --out output\background_assets\extracted\a_bgsize_wide_textures --min-area 500000 --non-square-ratio 1.3
python tools\make_contact_sheet.py output\background_assets\extracted\a_bgsize_wide_textures --out output\background_assets\extracted\a_bgsize_wide_contact_sheet.jpg --count 120 --cols 4 --thumb-w 260 --thumb-h 140
```

## 3D Scene Notes

Large 3D scene bundles contain many `GameObject`, `Transform`, `MeshRenderer`,
`MeshFilter`, `Light`, `ParticleSystem`, `AnimationClip`, and `Texture2D`
objects. In the validated top-12 large-bundle sample, exported textures were
material maps/atlases, not complete panorama background renders.

Validated 3D sample outputs:

```text
output\background_assets\source\a_top12
output\background_assets\extracted\a_top12_textures
output\background_assets\source\a_top12_summary.txt
```

## Pitfalls

- Use `adb exec-out su 0 cat <remote>` for private `/data/data/...` pulls.
- Do not expect logical names such as `env_2d_adv_*` in bundle object names; most
  names are stripped.
- Contact sheets are the fastest sanity check. Real backgrounds should look like
  scenes, not normal maps, UI fragments, or material swatches.
