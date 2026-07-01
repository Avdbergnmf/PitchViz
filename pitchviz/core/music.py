"""Music-theory helpers: keys, scales, and chord fit.

GUI-free so it can be reused and tested. Used by the Jam Helper tool to turn a
backing-track key into a set of recommended notes.
"""

from __future__ import annotations

from .pitch import NOTE_NAMES

# Scale name -> semitone intervals from the root.
SCALES: dict[str, tuple[int, ...]] = {
    "Major": (0, 2, 4, 5, 7, 9, 11),
    "Minor": (0, 2, 3, 5, 7, 8, 10),
    "Major pentatonic": (0, 2, 4, 7, 9),
    "Minor pentatonic": (0, 3, 5, 7, 10),
    "Blues": (0, 3, 5, 6, 7, 10),
}

SCALE_NAMES = list(SCALES)

# 12 possible roots (sharps), index == pitch class.
KEY_NAMES = list(NOTE_NAMES)


def root_pc(key_name: str) -> int:
    """Pitch class (0..11) for a key name like 'G' or 'A#'."""
    return NOTE_NAMES.index(key_name)


def scale_pitch_classes(root: int, scale: str) -> set[int]:
    """Set of pitch classes (0..11) in the given key/scale."""
    return {(root + i) % 12 for i in SCALES[scale]}


def note_in_scale(midi: int, root: int, scale: str) -> bool:
    return (midi % 12) in scale_pitch_classes(root, scale)


def scale_notes_in_range(root: int, scale: str, lo: int, hi: int) -> list[int]:
    """All MIDI notes of the key/scale within [lo, hi]."""
    pcs = scale_pitch_classes(root, scale)
    return [m for m in range(lo, hi + 1) if (m % 12) in pcs]


def degree_of(midi: int, root: int, scale: str) -> int | None:
    """Scale-degree index (0-based) of a note, or None if it's not in scale."""
    interval = (midi - root) % 12
    ivs = SCALES[scale]
    return ivs.index(interval) if interval in ivs else None


# --- Diatonic triads + scale suggestions ------------------------------------

# Expected triad quality per scale degree (major / natural minor).
_MAJOR_TRIADS = ("maj", "min", "min", "maj", "maj", "min", "min")
_MINOR_TRIADS = ("min", "min", "maj", "min", "min", "maj", "maj")


def diatonic_triads(root: int, scale: str) -> list[tuple[int, str]]:
    """(pitch_class, quality) for each scale degree that has a clear triad."""
    ivs = SCALES[scale]
    if scale == "Major":
        quals = _MAJOR_TRIADS
    elif scale == "Minor":
        quals = _MINOR_TRIADS
    else:
        # Pentatonic / blues: root of each scale tone as a triad (maj/min by degree).
        quals = ("maj", "min", "min", "maj", "maj", "min")[:len(ivs)]
    out = []
    for i, iv in enumerate(ivs):
        q = quals[i] if i < len(quals) else "maj"
        out.append(((root + iv) % 12, q))
    return out


_ROMAN_MAJOR = ("I", "ii", "iii", "IV", "V", "vi", "vii")
_ROMAN_MINOR = ("i", "ii", "III", "iv", "v", "VI", "VII")


def _roman_labels(scale: str, count: int) -> tuple[str, ...]:
    if scale == "Major":
        return _ROMAN_MAJOR[:count]
    if scale == "Minor":
        return _ROMAN_MINOR[:count]
    return tuple(str(i + 1) for i in range(count))


def diatonic_chord_options(root: int, scale: str) -> list[tuple["Chord", str]]:
    """Diatonic triads as (Chord, roman-or-degree label) for the key/scale."""
    from .chords import Chord

    triads = diatonic_triads(root, scale)
    labels = _roman_labels(scale, len(triads))
    return [(Chord(pc, q), lab) for (pc, q), lab in zip(triads, labels)]


def chord_fits_scale(ch, root: int, scale: str) -> bool:
    """True if the chord is a diatonic triad in the key/scale."""
    if ch is None:
        return False
    return (ch.root, ch.quality) in set(diatonic_triads(root, scale))


def score_progression(chords, root: int, scale: str) -> float:
    """0..1 fit of a recorded progression in the given key + scale."""
    known = [c for c in chords if c is not None]
    if not known:
        return 0.0
    pcs = scale_pitch_classes(root, scale)
    diatonic = set(diatonic_triads(root, scale))
    hits = 0.0
    for ch in known:
        if (ch.root, ch.quality) in diatonic:
            hits += 2.0
        elif ch.root in pcs:
            hits += 1.0
        elif any(pc in pcs for pc in ch.notes):
            hits += 0.5
    return hits / (2.0 * len(known))


def suggest_scales(chords) -> list[tuple[str, str, float]]:
    """Rank (key, scale) pairs for a progression. Best match first."""
    known = [c for c in chords if c is not None]
    if not known:
        return []
    results: list[tuple[str, str, float]] = []
    for key in KEY_NAMES:
        root = root_pc(key)
        for scale in SCALE_NAMES:
            score = score_progression(known, root, scale)
            if score > 0:
                results.append((key, scale, score))
    results.sort(key=lambda x: -x[2])
    return results[:8]
