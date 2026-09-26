"""Canonical v2 file-naming rule for GakumasuVoice voice items.

This is the single definition of the v2 naming rule.  Both the extractor
(``gakumasu_voice_v2.py``) and the migration tool (``tools/migrate_items_to_v2.py``)
must agree on it, so it lives here and is covered by tests that replay the real
names produced by the migration.

THE RULE
--------
1. ``base`` is ``safe_filename(text)`` -- the subtitle text made filesystem safe,
   truncated to :data:`MAX_BASE_CHARS` characters.
2. Items are grouped by ``base``.  Groups are ordered by ``(casefold(base), base)`.
3. Inside a group, items are ordered by their key (the ``voice_cue``).
4. The first item of a group takes the bare ``base``; the rest take ``base-2``,
   ``base-3``, ...  Any candidate already taken is skipped, compared
   case-insensitively because NTFS is case-insensitive.

Ordering by the key rather than by discovery order is what makes the names
reproducible: re-extracting a character reproduces exactly the same file names
even if the device returns scripts in a different order.

The length cap is a safety valve, not a normal path: the longest subtitle in the
1362-script corpus examined during the v2 migration was 53 characters, so no
migrated name was affected by it.  It exists so that an unusually long line in a
future script cannot produce a name Windows refuses to create.  Truncation can
itself create a clash, which rule 4 then resolves with a suffix.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gakumasu_voice import safe_filename  # noqa: E402

#: Longest subtitle base name kept before truncation.  A single Windows path
#: component is limited to 255 characters; the suffix (``-12``) and the extension
#: (``.wav``) are added on top, so this leaves ample headroom while staying far
#: above the 53 characters seen in real scripts.
MAX_BASE_CHARS = 120


def base_name(text: str, fallback: str = "") -> str:
    """``safe_filename(text)``, capped and with a fallback for unusable text.

    ``safe_filename`` already maps empty or dot/space-only input to ``"item"``, so
    the fallback only matters for inputs it rejects outright.
    """
    name = safe_filename(text)
    if not name:
        name = safe_filename(fallback) if fallback else "item"
    if len(name) > MAX_BASE_CHARS:
        name = name[:MAX_BASE_CHARS].rstrip("._ ")
        if not name:
            name = "item"
    return name


def assign_file_stems(entries: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Map each unique key to its v2 file stem.

    ``entries`` yields ``(key, text)`` pairs where ``key`` is the ``voice_cue``.
    Keys must be unique; callers collapse duplicate cues before calling.
    """
    pairs = list(entries)
    keys = [key for key, _text in pairs]
    if len(set(keys)) != len(keys):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise ValueError(f"keys must be unique; duplicated: {duplicates[:5]}")

    groups: dict[str, list[str]] = defaultdict(list)
    for key, text in pairs:
        groups[base_name(text, key)].append(key)

    assigned: dict[str, str] = {}
    used: set[str] = set()
    for base in sorted(groups, key=lambda value: (value.casefold(), value)):
        counter = 0
        for key in sorted(groups[base]):
            while True:
                candidate = base if counter == 0 else f"{base}-{counter + 1}"
                counter += 1
                if candidate.casefold() not in used:
                    break
            used.add(candidate.casefold())
            assigned[key] = candidate
    return assigned


def stem_matches_text(stem: str, text: str, key: str = "") -> bool:
    """True when ``stem`` is a legal v2 name for ``text`` (bare or ``-N`` form)."""
    base = base_name(text, key)
    if stem == base:
        return True
    if not stem.startswith(base + "-"):
        return False
    number = stem[len(base) + 1 :]
    return number.isdigit() and int(number) >= 2


def group_stems(stems: Iterable[str]) -> Mapping[str, list[str]]:
    """Group stems by their disambiguation base, for contiguity reporting."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for stem in stems:
        head, _sep, tail = stem.rpartition("-")
        if head and tail.isdigit() and int(tail) >= 2:
            grouped[head].append(stem)
        else:
            grouped[stem].append(stem)
    return grouped
