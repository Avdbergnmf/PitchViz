"""Chord recognition from polyphonic audio for backing-track analysis.

The detector is split into deliberately small stages:

1. ``chroma()`` builds a 12-bin harmonic pitch-class profile from an audio block.
2. ``ChordDetector`` scores that profile against editable chord templates.
3. ``dominant_chord()`` aggregates many frames for one timeline segment.
4. ``ChordTracker`` smooths live frame-by-frame display updates.

This is still a lightweight room-mic estimator, not a full transcription model,
but the scoring path is explicit enough to tune without rewriting the app.

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

# Chroma extraction knobs. They are module-level constants on purpose: this is
# the first place to tune if a source consistently over/under-emphasizes notes.
CHROMA_HARMONICS: tuple[tuple[int, float], ...] = (
    (1, 1.00),
    (2, 0.32),
    (3, 0.10),
    (4, 0.08),
)
CHROMA_SIGMA_SEMITONES = 0.22
CHROMA_COMPRESS_POWER = 0.55


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


@dataclass(frozen=True)
class ChordScore:
    """Detailed score for one candidate chord.

    ``score`` is for ranking; ``confidence`` is thresholded by callers and also
    shown in the UI. ``confidence`` mixes absolute chord-tone fit with the margin
    over the runner-up, because ambiguous chroma should stay unstable.
    """

    chord: Chord
    score: float
    confidence: float
    tone_energy: float
    root_energy: float
    third_energy: float
    outside_energy: float
    margin: float


@dataclass(frozen=True)
class ChordScoring:
    """Weights for template scoring.

    Root/third/fifth are intentionally a little uneven: the third distinguishes
    major/minor, the root anchors the label, and the fifth is common but less
    diagnostic. ``outside_penalty`` discourages templates that explain only a
    small part of a noisy profile.
    """

    root_weight: float = 1.25
    third_weight: float = 1.15
    fifth_weight: float = 0.90
    outside_penalty: float = 0.42
    root_bonus: float = 0.18
    third_bonus: float = 0.12
    margin_weight: float = 2.6


def _circular_distance(a: np.ndarray, b: float) -> np.ndarray:
    d = np.abs(a - b)
    return np.minimum(d, 12.0 - d)


def chroma(samples, samplerate: int, fmin: float = 65.0, fmax: float = 2000.0) -> np.ndarray:
    """12-bin harmonic pitch-class profile of an audio block.

    Compared with the old direct FFT-bin folding, this uses compressed
    magnitudes, soft semitone assignment, and harmonic back-folding. That makes
    the profile less brittle when a backing track has strong overtones or weak
    bass fundamentals.
    """
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim > 1:
        samples = samples[:, 0]
    n = samples.size
    out = np.zeros(12, dtype=np.float64)
    if n < 4:
        return out

    samples = samples - np.mean(samples)
    windowed = samples * np.hanning(n)
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, 1.0 / samplerate)

    mask = (freqs >= fmin) & (freqs <= fmax)
    freqs = freqs[mask]
    mag = spectrum[mask]
    if freqs.size == 0:
        return out

    mag = np.power(mag, CHROMA_COMPRESS_POWER)
    pcs = np.arange(12, dtype=np.float64)

    for harmonic, weight in CHROMA_HARMONICS:
        fund = freqs / harmonic
        hmask = fund >= fmin
        if not np.any(hmask):
            continue
        midi = 12.0 * np.log2(fund[hmask] / 440.0) + 69.0
        pc_float = np.mod(midi, 12.0)
        energy = mag[hmask] * weight
        for pc, e in zip(pc_float, energy):
            dist = _circular_distance(pcs, pc)
            out += e * np.exp(-0.5 * np.square(dist / CHROMA_SIGMA_SEMITONES))

    total = out.sum()
    return out / total if total > 0 else out


def chord_midis(ch: Chord, octave: int = 4) -> list[int]:
    """Playable MIDI notes for a chord near the harmonica range."""
    root_midi = 12 * (octave + 1) + ch.root
    return [root_midi + iv for iv in QUALITIES[ch.quality]]


def all_triads() -> list[Chord]:
    """Every major/minor triad (12 roots x 2 qualities)."""
    return [Chord(r, q) for r in range(12) for q in QUALITIES]


class ChordDetector:
    """Template scorer for major/minor triads.

    Pass ``candidates`` to restrict recognition to the selected scale before
    scoring. This is the correct place to "snap" detection: the detector never
    spends its vote on chords the current mode should ignore.
    """

    def __init__(
        self,
        candidates: list[Chord] | tuple[Chord, ...] | None = None,
        scoring: ChordScoring | None = None,
    ):
        self.candidates = tuple(candidates) if candidates is not None else tuple(all_triads())
        self.scoring = scoring or ChordScoring()

    def score_all(self, chroma_vec) -> list[ChordScore]:
        c = np.asarray(chroma_vec, dtype=np.float64)
        total = float(np.sum(c))
        if total <= 1e-12:
            return []
        c = c / total

        raw: list[tuple[Chord, float, float, float, float, float]] = []
        for chord in self.candidates:
            root, third, fifth = chord.notes
            root_e = float(c[root])
            third_e = float(c[third])
            fifth_e = float(c[fifth])
            tone = root_e + third_e + fifth_e
            outside = max(0.0, 1.0 - tone)
            weighted = (
                root_e * self.scoring.root_weight
                + third_e * self.scoring.third_weight
                + fifth_e * self.scoring.fifth_weight
                - outside * self.scoring.outside_penalty
                + root_e * self.scoring.root_bonus
                + third_e * self.scoring.third_bonus
            )
            raw.append((chord, weighted, tone, root_e, third_e, outside))

        raw.sort(key=lambda item: item[1], reverse=True)
        if not raw:
            return []
        best_score = raw[0][1]
        second_score = raw[1][1] if len(raw) > 1 else best_score - 1.0
        margin = max(0.0, best_score - second_score)

        out: list[ChordScore] = []
        for chord, score, tone, root_e, third_e, outside in raw:
            local_margin = margin if chord == raw[0][0] else 0.0
            confidence = np.clip(
                0.72 * tone + self.scoring.margin_weight * local_margin,
                0.0,
                1.0,
            )
            out.append(ChordScore(
                chord=chord,
                score=float(score),
                confidence=float(confidence),
                tone_energy=float(tone),
                root_energy=float(root_e),
                third_energy=float(third_e),
                outside_energy=float(outside),
                margin=float(local_margin),
            ))
        return out

    def detect(self, chroma_vec, min_confidence: float = 0.42) -> ChordScore | None:
        scores = self.score_all(chroma_vec)
        if not scores or scores[0].confidence < min_confidence:
            return None
        return scores[0]

    def aggregate(self, chromas, min_confidence: float = 0.42) -> ChordScore | None:
        """Detect one chord from many chroma frames.

        Mean + median gives stable segments without letting one short transient
        dominate the whole slot.
        """
        if not chromas:
            return None
        arr = np.asarray(chromas, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[1] != 12:
            return None
        profile = 0.55 * np.mean(arr, axis=0) + 0.45 * np.median(arr, axis=0)
        return self.detect(profile, min_confidence=min_confidence)


def _detector(candidates: list[Chord] | tuple[Chord, ...] | None = None) -> ChordDetector:
    return ChordDetector(candidates=candidates)


def detect_chord(
    chroma_vec,
    candidates: list[Chord] | tuple[Chord, ...] | None = None,
    min_confidence: float = 0.42,
) -> tuple[Chord, float] | None:
    """Best-matching triad for a chroma vector, with confidence."""
    match = _detector(candidates).detect(chroma_vec, min_confidence=min_confidence)
    return (match.chord, match.confidence) if match else None


def best_chord(
    chromas,
    min_score: float = 0.42,
    candidates: list[Chord] | tuple[Chord, ...] | None = None,
) -> Chord | None:
    """Pick the triad that best fits a pile of chroma frames."""
    match = _detector(candidates).aggregate(chromas, min_confidence=min_score)
    return match.chord if match else None


def dominant_chord(
    chromas,
    min_score: float = 0.42,
    candidates: list[Chord] | tuple[Chord, ...] | None = None,
) -> Chord | None:
    """Chord estimate for a timeline slot.

    The name is kept for API compatibility with the tool layer.
    """
    return best_chord(chromas, min_score=min_score, candidates=candidates)


class ChordTracker:
    """Smooths live chord guesses into a stable current chord."""

    def __init__(self, hold_frames: int = 4, min_score: float = 0.48):
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

    def update(
        self,
        chroma_vec,
        candidates: list[Chord] | tuple[Chord, ...] | None = None,
    ) -> Chord | None:
        match = _detector(candidates).detect(chroma_vec, min_confidence=self.min_score)
        cand = match.chord if match is not None else None
        self.score = match.confidence if match is not None else 0.0
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
    dur = 0.4
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)

    def tone(midi, amp=1.0):
        f = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        return amp * (
            np.sin(2 * np.pi * f * t)
            + 0.35 * np.sin(2 * np.pi * 2 * f * t)
            + 0.18 * np.sin(2 * np.pi * 3 * f * t)
        )

    checks = [
        ("C", "maj", [60, 64, 67]),
        ("A", "min", [57, 60, 64]),
        ("B", "min", [59, 62, 66]),
        ("G", "maj", [55, 59, 62]),
        ("E", "min", [52, 55, 59]),
        ("F", "maj", [53, 57, 60]),
        ("D", "min", [50, 53, 57]),
    ]
    print(f"{'expected':>10} {'detected':>10} {'conf':>7}")
    failures = 0
    for name, qual, midis in checks:
        sig = sum(tone(m) for m in midis)
        det = detect_chord(chroma(sig, sr))
        got = det[0].name if det else "(none)"
        want = name + ("m" if qual == "min" else "")
        ok = got == want
        print(f"{want:>10} {got:>10} {det[1] if det else 0:>7.2f}{'' if ok else '  <-- MISMATCH'}")
        failures += 0 if ok else 1

    # Restricted candidates should choose inside the key/scale candidate set.
    from .music import diatonic_chord_options, root_pc
    g_major = [ch for ch, _label in diatonic_chord_options(root_pc("G"), "Major")]
    am_sig = sum(tone(m) for m in [57, 60, 64])
    det = detect_chord(chroma(am_sig, sr), candidates=g_major)
    if det is None or det[0].name != "Am":
        print(f"restricted expected Am, got {det[0].name if det else None}  <-- MISMATCH")
        failures += 1
    else:
        print(f"{'restricted':>10} {'Am':>10} {det[1]:>7.2f}")

    # Segment aggregate: mostly C with a short G tail should stay C.
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
