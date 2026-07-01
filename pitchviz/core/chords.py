"""Chord recognition from (polyphonic) audio - for analyzing a backing track.

This is deliberately simple and robust rather than state-of-the-art:

1. ``chroma()`` folds an FFT magnitude spectrum into a 12-bin pitch-class
   profile (energy per note name, octave-independent).
2. ``detect_chord()`` correlates that profile against major/minor triad
   templates for all 12 roots and returns the best match + a confidence.
3. ``ChordTracker`` smooths frame-by-frame guesses so the reported chord only
   changes once a new one is stable for a few frames.

Polyphonic chord ID from a room mic is inherently noisy; treat results as a
helpful estimate the user can correct, not ground truth.

Self-test:
    python -m pitchviz.core.chords
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .pitch import NOTE_NAMES

# Chord qualities as semitone offsets from the root.
QUALITIES: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7),
    "min": (0, 3, 7),
}


def chroma(samples, samplerate: int, fmin: float = 65.0, fmax: float = 2000.0) -> np.ndarray:
    """12-bin pitch-class energy profile of an audio block (sums to 1, or 0s)."""
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim > 1:
        samples = samples[:, 0]
    n = samples.size
    out = np.zeros(12)
    if n < 4:
        return out

    samples = samples - np.mean(samples)
    windowed = samples * np.hanning(n)
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, 1.0 / samplerate)

    mask = (freqs >= fmin) & (freqs <= fmax)
    f = freqs[mask]
    mag = spectrum[mask]
    if f.size == 0:
        return out

    # Map each bin to a pitch class (A4=440 -> MIDI 69).
    pcs = (np.round(12.0 * np.log2(f / 440.0) + 69).astype(int)) % 12
    np.add.at(out, pcs, mag)
    total = out.sum()
    return out / total if total > 0 else out


def _build_templates() -> list[tuple[int, str, np.ndarray]]:
    templates = []
    for root in range(12):
        for quality, ivs in QUALITIES.items():
            v = np.zeros(12)
            for i in ivs:
                v[(root + i) % 12] = 1.0
            v /= np.linalg.norm(v)
            templates.append((root, quality, v))
    return templates


TEMPLATES = _build_templates()


@dataclass(frozen=True)
class Chord:
    root: int            # pitch class 0..11
    quality: str         # "maj" or "min"

    @property
    def name(self) -> str:
        return NOTE_NAMES[self.root] + ("m" if self.quality == "min" else "")

    @property
    def notes(self) -> tuple[int, ...]:
        """Chord-tone pitch classes (root, third, fifth)."""
        return tuple((self.root + i) % 12 for i in QUALITIES[self.quality])


def chord_midis(ch: Chord, octave: int = 4) -> list[int]:
    """Playable MIDI notes for a chord near the harmonica range (default octave 4)."""
    root_midi = 12 * (octave + 1) + ch.root
    ivs = QUALITIES[ch.quality]
    return [root_midi + iv for iv in ivs]


def all_triads() -> list[Chord]:
    """Every major/minor triad (12 roots × 2 qualities)."""
    return [Chord(r, q) for r in range(12) for q in QUALITIES]


def detect_chord(chroma_vec) -> tuple[Chord, float] | None:
    """Best-matching triad for a chroma vector, with a 0..1 confidence."""
    c = np.asarray(chroma_vec, dtype=np.float64)
    norm = np.linalg.norm(c)
    if norm < 1e-9:
        return None
    c = c / norm
    best = None
    best_score = -1.0
    for root, quality, template in TEMPLATES:
        score = float(np.dot(c, template))
        if score > best_score:
            best_score = score
            best = (root, quality)
    return Chord(best[0], best[1]), best_score


def best_chord(chromas, min_score: float = 0.45) -> Chord | None:
    """Pick the triad that best fits a pile of chroma frames (e.g. one bar slot)."""
    if not chromas:
        return None
    summed = np.sum(np.asarray(chromas, dtype=np.float64), axis=0)
    det = detect_chord(summed)
    if det is None or det[1] < min_score:
        return None
    return det[0]


def dominant_chord(chromas, min_score: float = 0.45) -> Chord | None:
    """Triad that matched the most frames in a slot (robust to early/late changes)."""
    if not chromas:
        return None
    counts: dict[tuple[int, str], int] = {}
    score_sum: dict[tuple[int, str], float] = {}
    for vec in chromas:
        det = detect_chord(vec)
        if det is None or det[1] < min_score:
            continue
        key = (det[0].root, det[0].quality)
        counts[key] = counts.get(key, 0) + 1
        score_sum[key] = score_sum.get(key, 0.0) + det[1]
    if not counts:
        return None
    best_key = max(counts.keys(), key=lambda k: (counts[k], score_sum[k]))
    return Chord(best_key[0], best_key[1])


class ChordTracker:
    """Smooths per-frame chord guesses into a stable current chord."""

    def __init__(self, hold_frames: int = 5, min_score: float = 0.6):
        self.hold_frames = hold_frames
        self.min_score = min_score
        self.current: Chord | None = None
        self.score = 0.0
        self._candidate: Chord | None = None
        self._count = 0

    def reset(self):
        self.current = None
        self.score = 0.0
        self._candidate = None
        self._count = 0

    def update(self, chroma_vec) -> Chord | None:
        det = detect_chord(chroma_vec)
        cand = det[0] if (det is not None and det[1] >= self.min_score) else None
        self.score = det[1] if det is not None else 0.0
        if cand == self._candidate:
            self._count += 1
        else:
            self._candidate = cand
            self._count = 1
        if self._count >= self.hold_frames and cand is not None:
            self.current = cand
        return self.current


def _self_test() -> int:
    """Synthesize triads as stacked sine partials and confirm recognition."""
    sr = 44100
    dur = 0.2
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)

    def tone(midi):
        f = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        # fundamental + a couple of partials, like a real instrument
        return (np.sin(2 * np.pi * f * t)
                + 0.4 * np.sin(2 * np.pi * 2 * f * t)
                + 0.2 * np.sin(2 * np.pi * 3 * f * t))

    checks = [
        ("C", "maj", [60, 64, 67]),
        ("A", "min", [57, 60, 64]),
        ("G", "maj", [55, 59, 62]),
        ("E", "min", [52, 55, 59]),
        ("F", "maj", [53, 57, 60]),
        ("D", "min", [50, 53, 57]),
    ]
    print(f"{'expected':>10} {'detected':>10} {'score':>7}")
    failures = 0
    for name, qual, midis in checks:
        sig = sum(tone(m) for m in midis)
        det = detect_chord(chroma(sig, sr))
        got = det[0].name if det else "(none)"
        want = name + ("m" if qual == "min" else "")
        ok = got == want
        print(f"{want:>10} {got:>10} {det[1]:>7.2f}{'' if ok else '  <-- MISMATCH'}")
        failures += 0 if ok else 1

    # Dominant chord: mostly C with a short G tail should stay C.
    c_sig = sum(tone(m) for m in [60, 64, 67])
    g_sig = sum(tone(m) for m in [55, 59, 62])
    slot = [chroma(c_sig, sr)] * 8 + [chroma(g_sig, sr)] * 2
    dom = dominant_chord(slot)
    if dom is None or dom.name != "C":
        print(f"dominant_chord expected C, got {dom.name if dom else None}  <-- MISMATCH")
        failures += 1
    else:
        print(f"{'dominant':>10} {'C':>10} {'ok':>7}")

    print("\nAll good." if failures == 0 else f"\n{failures} mismatch(es).")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
