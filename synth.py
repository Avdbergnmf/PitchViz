"""PitchViz - simple tone playback for click-to-hear.

Generates a short, pleasant tone for a given frequency and plays it through
the default output device. Used so you can hear a note or a bend target.
"""

import numpy as np
import sounddevice as sd

PLAY_SR = 44100
DURATION = 0.6          # seconds
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
