"""Pitch detection core.

Pure-math helpers with no audio or GUI dependencies, so they can be unit
tested and reused freely:

- detect_pitch(samples, samplerate) -> frequency in Hz (or None)
- freq_to_note(freq) -> (note_name, cents_offset, midi_number)

Run this file directly for a quick self-test against synthetic sine waves:
    python -m pitchviz.core.pitch
"""

from __future__ import annotations

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Sensible defaults for a diatonic harmonica (C4 ~262 Hz up past C7 ~2093 Hz),
# with headroom on both sides.
DEFAULT_FMIN = 60.0
DEFAULT_FMAX = 2500.0

# Below this RMS we treat the input as silence and report no pitch.
DEFAULT_RMS_GATE = 0.005

# Autocorrelation peak must be at least this strong relative to lag 0,
# otherwise the signal is too noisy/unpitched to trust.
DEFAULT_CLARITY = 0.30


def detect_pitch(
    samples,
    samplerate: int,
    fmin: float = DEFAULT_FMIN,
    fmax: float = DEFAULT_FMAX,
    rms_gate: float = DEFAULT_RMS_GATE,
    clarity_threshold: float = DEFAULT_CLARITY,
):
    """Estimate the fundamental frequency of a (monophonic) audio block.

    Uses FFT-based autocorrelation with parabolic interpolation for
    sub-sample accuracy. Returns the frequency in Hz, or None if the block
    is too quiet or has no clear pitch.
    """
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim > 1:
        samples = samples[:, 0]
    n = samples.size
    if n < 2:
        return None

    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < rms_gate:
        return None

    # Remove DC offset and taper the edges to reduce spectral leakage.
    samples = samples - np.mean(samples)
    windowed = samples * np.hanning(n)

    # Autocorrelation via FFT (zero-padded to avoid circular wraparound).
    size = 1 << int(np.ceil(np.log2(2 * n)))
    spectrum = np.fft.rfft(windowed, size)
    power = spectrum * np.conj(spectrum)
    corr = np.fft.irfft(power)[:n]

    if corr[0] <= 0:
        return None

    min_lag = max(1, int(samplerate / fmax))
    max_lag = min(n - 1, int(samplerate / fmin))
    if min_lag >= max_lag:
        return None

    segment = corr[min_lag:max_lag]
    peak = int(np.argmax(segment)) + min_lag

    # Clarity check: how strong is this period relative to a perfect match?
    if corr[peak] / corr[0] < clarity_threshold:
        return None

    # Parabolic interpolation around the peak for a finer estimate.
    if 1 <= peak < n - 1:
        a, b, c = corr[peak - 1], corr[peak], corr[peak + 1]
        denom = a - 2.0 * b + c
        if denom != 0:
            peak = peak + 0.5 * (a - c) / denom

    if peak <= 0:
        return None
    freq = samplerate / peak
    if freq < fmin or freq > fmax:
        return None
    return float(freq)


def freq_to_note(freq: float):
    """Map a frequency to (note_name, cents_offset, midi_number).

    cents_offset is how sharp (+) or flat (-) the input is relative to the
    nearest equal-tempered note, in the range roughly -50..+50.
    """
    if freq <= 0:
        return None
    midi_float = 69.0 + 12.0 * np.log2(freq / 440.0)
    midi = int(round(midi_float))
    cents = (midi_float - midi) * 100.0
    name = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return f"{name}{octave}", float(cents), midi


def note_to_freq(midi: int) -> float:
    """Inverse of the note mapping: MIDI number -> frequency in Hz."""
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def freq_to_midi(freq: float) -> float:
    """Frequency -> fractional MIDI number (e.g. 73.42), for smooth bend tracking."""
    return 69.0 + 12.0 * float(np.log2(freq / 440.0))


def _self_test() -> int:
    """Generate clean sine tones and confirm detection is accurate."""
    sr = 44100
    duration = 0.1
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    test_notes = {
        "C4": 261.63,
        "D4": 293.66,
        "G4": 392.00,
        "A4": 440.00,
        "C5": 523.25,
        "A5": 880.00,
    }

    print("Self-test (synthetic sine waves):")
    print(f"{'note':>5} {'true Hz':>9} {'detected':>9} {'->':>3} {'reading':>14} {'cents':>7}")
    failures = 0
    for name, true_freq in test_notes.items():
        signal = 0.5 * np.sin(2 * np.pi * true_freq * t)
        freq = detect_pitch(signal, sr)
        if freq is None:
            print(f"{name:>5} {true_freq:9.2f}    (no pitch detected)")
            failures += 1
            continue
        reading, cents, _ = freq_to_note(freq)
        err = abs(freq - true_freq)
        flag = "" if err < 1.0 else "  <-- off"
        print(f"{name:>5} {true_freq:9.2f} {freq:9.2f} {'->':>3} {reading:>14} {cents:+7.1f}{flag}")
        if err >= 1.0:
            failures += 1

    # Silence should report nothing.
    if detect_pitch(np.zeros(int(sr * duration)), sr) is not None:
        print("FAIL: silence reported a pitch")
        failures += 1
    else:
        print("silence -> no pitch (correct)")

    print("\nAll good." if failures == 0 else f"\n{failures} issue(s) found.")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
