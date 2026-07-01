"""Simple tone playback for click-to-hear.

Generates a short, pleasant tone for a given frequency and plays it through
the default output device. Used so you can hear a note or a bend target.
"""

import numpy as np
import sounddevice as sd

PLAY_SR = 44100
DURATION = 0.6          # seconds
CLICK_DUR = 0.06        # metronome tick length
CLICK_FREQ = 880.0
CLICK_ACCENT_FREQ = 1100.0
# A few harmonics give it a slightly reedy, less pure-sine character.
HARMONICS = [(1, 1.0), (2, 0.35), (3, 0.15)]


def make_tone(freq: float, duration: float = DURATION, sr: int = PLAY_SR) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    wave = np.zeros_like(t)
    for mult, amp in HARMONICS:
        wave += amp * np.sin(2 * np.pi * freq * mult * t)
    wave /= sum(a for _, a in HARMONICS)

    # Short attack + release fade to avoid clicks.
    fade = int(sr * 0.02)
    if fade > 0 and 2 * fade < wave.size:
        env = np.ones_like(wave)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        wave *= env

    return (0.35 * wave).astype(np.float32)


def make_sequence(freqs, note_dur: float = 0.13, sr: int = PLAY_SR) -> np.ndarray:
    """Concatenate short tones into a quick melodic sequence (e.g. a chime)."""
    parts = [make_tone(f, duration=note_dur, sr=sr) for f in freqs if f > 0]
    return np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)


def play_freq(freq: float):
    """Play a tone at the given frequency (non-blocking). Safe to call rapidly."""
    if freq <= 0:
        return
    try:
        sd.stop()
        sd.play(make_tone(freq), PLAY_SR)
    except Exception:
        # Audio output is best-effort; never let playback crash the GUI.
        pass


def make_chord(freqs, duration: float = DURATION, sr: int = PLAY_SR) -> np.ndarray:
    """Stack several frequencies into one chord (quieter per voice)."""
    freqs = [f for f in freqs if f > 0]
    if not freqs:
        return np.zeros(1, dtype=np.float32)
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    wave = np.zeros_like(t)
    amp = 0.35 / len(freqs)
    for f in freqs:
        for mult, h in HARMONICS:
            wave += amp * h * np.sin(2 * np.pi * f * mult * t)
    fade = int(sr * 0.02)
    if fade > 0 and 2 * fade < wave.size:
        env = np.ones_like(wave)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        wave *= env
    return wave.astype(np.float32)


def play_click(accent: bool = False):
    """Short metronome tick (non-blocking)."""
    freq = CLICK_ACCENT_FREQ if accent else CLICK_FREQ
    try:
        t = np.linspace(0, CLICK_DUR, int(PLAY_SR * CLICK_DUR), endpoint=False)
        wave = np.sin(2 * np.pi * freq * t) * np.exp(-t * 35)
        sd.play((0.45 * wave).astype(np.float32), PLAY_SR)
    except Exception:
        pass


def play_chord(freqs, duration: float = DURATION):
    """Play several frequencies together as a chord."""
    if not freqs:
        return
    try:
        sd.stop()
        sd.play(make_chord(freqs, duration=duration), PLAY_SR)
    except Exception:
        pass


def play_success():
    """A short, bright major arpeggio to reward holding a bend goal."""
    # C6, E6, G6, C7.
    freqs = [1046.50, 1318.51, 1567.98, 2093.00]
    try:
        sd.stop()
        sd.play(make_sequence(freqs, note_dur=0.10), PLAY_SR)
    except Exception:
        pass


def stop():
    try:
        sd.stop()
    except Exception:
        pass
