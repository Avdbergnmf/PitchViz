"""Music-theory helpers: keys, scales, and harmonica positions.

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


# --- Harmonica positions ----------------------------------------------------
# Position number tells you which "key" you're playing on a given harp. Each
# step up the circle of fifths (+7 semitones) is the next position.
POSITION_INFO: dict[int, tuple[str, str]] = {
    1: ("1st position (straight harp)", "major / folk, melody playing"),
    2: ("2nd position (cross harp)", "blues & rock - bluesy dominant sound"),
    3: ("3rd position (slant)", "minor / Dorian - moody, jazzy"),
    4: ("4th position", "minor (natural minor flavor)"),
    5: ("5th position", "minor / Phrygian"),
    6: ("6th position", "diminished / advanced"),
    12: ("12th position", "major (bright, soft)"),
}


def position_for(track_root: int, harp_root: int = 0) -> int:
    """Which position the track key sits in on the given harp (default C harp).

    Position n's root is harp_root + 7*(n-1) (mod 12) on the circle of fifths.
    """
    for n in range(1, 13):
        if (harp_root + 7 * (n - 1)) % 12 == track_root:
            return n
    return 1


def position_label(track_root: int, harp_root: int = 0) -> tuple[int, str, str]:
    """(position_number, name, typical_use) for the track key on the harp."""
    n = position_for(track_root, harp_root)
    name, use = POSITION_INFO.get(n, (f"{n}th position", "advanced"))
    return n, name, use


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


def chord_closeness(a, b) -> float:
    """Higher = more related (shared tones, nearby root)."""
    if a is None or b is None:
        return 0.0
    shared = len(set(a.notes) & set(b.notes))
    rd = min((a.root - b.root) % 12, (b.root - a.root) % 12)
    return shared * 10.0 - rd * 0.5


def chord_fits_scale(ch, root: int, scale: str) -> bool:
    """True if the chord is a diatonic triad in the key/scale."""
    if ch is None:
        return False
    return (ch.root, ch.quality) in set(diatonic_triads(root, scale))


def snap_to_scale(
    ch,
    root: int,
    scale: str,
    min_closeness: float = 15.0,
) -> tuple["Chord | None", "Chord | None"]:
    """If ``ch`` is outside the scale, maybe return a close diatonic substitute.

    Returns ``(result, original)`` where ``original`` is set only when a snap
    happened. ``min_closeness`` needs ~15 for one shared tone + nearby root, ~25
    for two shared tones.
    """
    if ch is None:
        return None, None
    if chord_fits_scale(ch, root, scale):
        return ch, None
    opts = diatonic_chord_options(root, scale)
    if not opts:
        return ch, None
    best_ch, _ = max(opts, key=lambda item: chord_closeness(ch, item[0]))
    if chord_closeness(ch, best_ch) >= min_closeness:
        return best_ch, ch
    return ch, None


def nearby_diatonic(current, root: int, scale: str, limit: int = 6) -> list[tuple["Chord", str]]:
    """Scale chords closest to ``current``, best matches first (excludes exact match)."""
    opts = diatonic_chord_options(root, scale)
    ranked = sorted(opts, key=lambda item: chord_closeness(current, item[0]), reverse=True)
    out = [(c, lab) for c, lab in ranked if c != current]
    return out[:limit] if out else ranked[:limit]


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
