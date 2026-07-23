# Gakumasu Character Speaker Asset Guide

This guide records the validated workflow for finding and exporting character
ADV speaker images from the Gakumasu Android Octo cache.

Validated on 2026-07-23 with Kayo Rinha / `krnh`.

## Scope

ADV scripts can reference a character in two different ways:

- 2D speaker images in message lines, for example
  `speaker=img_adv_speaker_krnh_000-001`.
- 3D actor models in actor groups, for example
  `actor id=krnh body=mdl_chr_krnh-casl-0000_body`.

For Rinha, early story scripts use the 2D `img_adv_speaker_krnh_*` images and do
not declare a `krnh` actor model. Later scripts declare the `krnh` 3D actor and
use `mdl_chr_krnh-*` model parts.

## Rinha Result

The verified 2D speaker assets are:

```text
img_adv_speaker_krnh_000-000.png  512x512
img_adv_speaker_krnh_000-001.png  512x512
img_adv_speaker_krnh_000-002.png  512x512
img_adv_speaker_krnh_000-003.png  512x512
img_adv_speaker_krnh_000-004.png  512x512
img_adv_speaker_krnh_000-005.png  512x512
img_adv_speaker_krnh_000-006.png  512x512
img_adv_speaker_krnh_000-007.png  512x512
img_adv_speaker_krnh_000-008.png  512x512
img_chr_krnh_00-thumb-circle.png  256x256
```

Local extraction output from the verified run:

```text
output\rinha\speaker_assets\clean
output\rinha\speaker_assets\krnh_2d_clean_contact_sheet.jpg
```

No separate `stand`, `full`, `body`, `illust`, or similar full-body 2D Rinha
standing asset name was found in the `krnh` A-asset search. The available
early-story 2D assets are the ADV speaker half-body images plus the circular
character thumbnail.

## Script Evidence

Early 2D-only script example:

```text
output\rinha\scripts\5231303339_607c52562bf329f18f0db7b0c733b455.txt
```

The `actorgroup` only declares `ttmr`, while Rinha dialogue uses message speaker
images such as:

```text
speaker=img_adv_speaker_krnh_000-000
```

Later 3D script example:

```text
output\rinha\scripts\5232383934_201a12117fc49b5e9e99a5640b5a560c.txt
```

The script declares Rinha as a 3D actor:

```text
actor id=krnh body=mdl_chr_krnh-casl-0000_body face=mdl_chr_krnh-base-0000_face hair=mdl_chr_krnh-base-0000_hair
```

Use this script distinction when checking whether a character appearance is a
2D speaker image or a 3D model scene.

## Discovery Flow

Connect to the emulator and search for character assets:

```cmd
adb connect 127.0.0.1:16384
adb -s 127.0.0.1:16384 shell su 0 grep -R -a -l krnh /data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400
```

Filter A-assets by decoding the parent folder name as hex ASCII. A path like:

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/4/4131313334/825bea3c75660d2641f96ffac6593083
```

decodes to:

```text
4131313334 -> A1134
```

Pull the A-assets with:

```cmd
python tools\pull_adb_paths.py --list <paths.tsv> --out <bundle_dir>
```

Search the pulled bundles for ASCII asset names:

```text
img_adv_speaker_<character_code>
img_chr_<character_code>
mdl_chr_<character_code>
mot_all_chr_<character_code>
```

For Rinha, the `krnh` A-asset search found 20 A-assets. The image assets were
the 9 `img_adv_speaker_krnh_000-*` bundles and `img_chr_krnh_00-thumb-circle`.
Other `krnh` A-assets were 3D model, material, or motion related.

## AB Header Decryption

Gakumasu Octo A-assets may not start with a plain `UnityFS` header. They can look
like random bytes while still containing mostly readable Unity strings such as
`m_Width`, `m_Height`, `m_TextureFormat`, `AssetBundle`, and the logical asset
name near offset `0x6cd`.

The verified fix is to decrypt only the first 256 bytes of the AssetBundle using
the logical asset name as the mask key. After decrypting correctly, the header
starts with:

```text
UnityFS
```

The method matches the public `Gakuen-idolmaster-ab-decrypt` approach:

```text
https://github.com/nijinekoyo/Gakuen-idolmaster-ab-decrypt
```

Important details:

- Speaker bundles whose embedded names end in `u` are decrypted with the name
  without the final `u`.
- Speaker bundles whose embedded names are followed by a control byte are
  decrypted with the clean logical name.
- Examples:
  - `img_adv_speaker_krnh_000-000u` -> key `img_adv_speaker_krnh_000-000`
  - `img_adv_speaker_krnh_000-005\x01` -> key `img_adv_speaker_krnh_000-005`
  - `img_chr_krnh_00-thumb-circleY` -> key `img_chr_krnh_00-thumb-circle`

After decryption, `tools\inspect_unity_bundle.py` should show one
`AssetBundle` and one `Texture2D` object for these small speaker bundles.
`tools\export_unity_textures.py` can then export the PNGs. UnityPy fallback
warnings are expected because the Unity version string in the bundle header is
not complete.

## Verified Rinha A-Assets

```text
A1134  img_adv_speaker_krnh_000-000
A4241  img_adv_speaker_krnh_000-001
A7185  img_adv_speaker_krnh_000-002
A1724  img_adv_speaker_krnh_000-003
A6186  img_adv_speaker_krnh_000-004
A4056  img_adv_speaker_krnh_000-005
A7431  img_adv_speaker_krnh_000-006
A2157  img_adv_speaker_krnh_000-007
A3590  img_adv_speaker_krnh_000-008
A4788  img_chr_krnh_00-thumb-circle
```

Additional `krnh` A-assets seen in the cache included `mdl_chr_krnh`,
`mdl_chr_krnh-base-0000_hair`, `chr_krnh-casl-0000_bdy_col`,
`chr_krnh-casl-0000_bdy_def-q`, and `mot_all_chr_krnh_*`, which confirms the
later 3D model asset set.

## Practical Checks

- Build a contact sheet after export and visually inspect it. Speaker assets
  should be transparent character portraits, not material maps.
- Keep clean copies under logical filenames such as
  `img_adv_speaker_krnh_000-000.png` instead of only long cache filenames.
- Do not infer a full-body 2D standing sprite from `speaker=` usage. In Rinha's
  current cache evidence, the early-story 2D representation is the ADV speaker
  portrait set.
