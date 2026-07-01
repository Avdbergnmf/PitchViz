"""Simple tone playback for click-to-hear.

Generates a short, pleasant tone for a given frequency and plays it through
the default output device. Used so you can hear a note or a bend target.
"""

import queue
import threading

import numpy as np
import sounddevice as sd

PLAY_SR = 44100
DURATION = 0.6          # seconds
CLICK_DUR = 0.06        # metronome tick length
CLICK_FREQ = 880.0
CLICK_ACCENT_FREQ = 1100.0
# A few harmonics give it a slightly reedy, less pure-sine character.
HARMONICS = [(1, 1.0), (2, 0.35), (3, 0.15)]

_CMD_Q: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=8)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _ensure_worker():
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        t = threading.Thread(target=_audio_worker, daemon=True, name="PitchVizAudio")
        t.start()
        _WORKER_STARTED = True


def _enqueue(cmd: str, payload=None):
    _ensure_worker()
    try:
        _CMD_Q.put_nowait((cmd, payload))
    except queue.Full:
        try:
            _CMD_Q.get_nowait()
        except queue.Empty:
            pass
        try:
            _CMD_Q.put_nowait((cmd, payload))
        except queue.Full:
            pass


def _clear_queue():
    while True:
        try:
            _CMD_Q.get_nowait()
        except queue.Empty:
            return


def _audio_worker():
    while True:
        cmd, payload = _CMD_Q.get()
        try:
            if cmd == "stop":
                sd.stop()
            elif cmd == "freq":
                freq = float(payload)
                if freq > 0:
                    sd.stop()
                    sd.play(make_tone(freq), PLAY_SR)
            elif cmd == "chord":
                freqs, duration = payload
                if freqs:
                    sd.stop()
                    sd.play(make_chord(freqs, duration=float(duration)), PLAY_SR)
            elif cmd == "click":
                freq = CLICK_ACCENT_FREQ if bool(payload) else CLICK_FREQ
                t = np.linspace(0, CLICK_DUR, int(PLAY_SR * CLICK_DUR), endpoint=False)
                wave = np.sin(2 * np.pi * freq * t) * np.exp(-t * 35)
                sd.play((0.45 * wave).astype(np.float32), PLAY_SR)
            elif cmd == "success":
                freqs = [1046.50, 1318.51, 1567.98, 2093.00]
                sd.stop()
                sd.play(make_sequence(freqs, note_dur=0.10), PLAY_SR)
        except Exception:
            pass


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
    _enqueue("freq", freq)


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
    _enqueue("click", bool(accent))


def play_chord(freqs, duration: float = DURATION):
    """Play several frequencies together as a chord."""
    if not freqs:
        return
    _enqueue("chord", (list(freqs), float(duration)))


def play_success():
    """A short, bright major arpeggio to reward holding a bend goal."""
    _enqueue("success")


def stop():
    _clear_queue()
    try:
        sd.stop()
    except Exception:
        pass
    _enqueue("stop")
