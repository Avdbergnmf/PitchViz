"""Shared microphone engine: one input stream feeding every tool.

The shell owns a single ``AudioEngine`` and pumps it from the Tk loop via
``poll()``. Each poll drains the audio queue to the most recent block, computes
the input level and the current pitch, and returns an immutable ``AudioState``
snapshot the active tool can read. Keeping this here means tools never touch
``sounddevice`` directly.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from .chords import chroma as compute_chroma
from .harmonica import note_locations
from .pitch import detect_pitch, freq_to_midi, freq_to_note

# Level meter range, in dBFS (decibels relative to full scale).
MIN_DB = -60.0
MAX_DB = 0.0

SAMPLERATE = 44100
BLOCKSIZE = 2048
FPS_EST = SAMPLERATE / BLOCKSIZE        # ~frames per second of audio blocks
NOTE_HOLD_FRAMES = 12                   # keep showing a note briefly after it stops
CHORD_CHROMA_SAMPLES = 8192             # ~186 ms: enough FFT context for chords


def rms_to_dbfs(rms: float) -> float:
    """Convert an RMS amplitude (0..1) to dBFS, clamped to our display range."""
    if rms <= 1e-9:
        return MIN_DB
    db = 20.0 * np.log10(rms)
    return float(np.clip(db, MIN_DB, MAX_DB))


def db_to_fraction(db: float) -> float:
    """Map a dBFS value to a 0..1 fraction for the bar width."""
    return (db - MIN_DB) / (MAX_DB - MIN_DB)


def list_input_devices():
    """Return [(index, label), ...] for devices that can capture audio."""
    devices = sd.query_devices()
    inputs = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            inputs.append((idx, f"{idx}: {dev['name']}"))
    return inputs


@dataclass(frozen=True)
class AudioState:
    """Immutable snapshot of the mic for one processed block."""

    level_fraction: float          # 0..1 input level for the meter
    silent_frames: int             # consecutive blocks with no detected pitch
    has_pitch: bool                # a pitch was detected *this* block
    freq: float | None             # detected frequency this block (or None)
    midi_f: float | None           # fractional MIDI (held briefly after stop)
    midi: int | None               # nearest MIDI note (held briefly after stop)
    note_name: str                 # e.g. "A#4" (last detected)
    cents: float                   # signed cents off (last detected)
    hole: int | None               # harmonica hole producing the note
    action: str | None             # "blow" or "draw"
    chroma: np.ndarray             # 12-bin pitch-class profile (for chord ID)


class AudioEngine:
    """Owns the input stream and turns raw blocks into ``AudioState``."""

    def __init__(self, samplerate: int = SAMPLERATE, blocksize: int = BLOCKSIZE):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=16)
        self._stream: sd.InputStream | None = None
        self.devices = list_input_devices()
        self.last_error: str | None = None

        # Detection threshold, shared by the level meter widget.
        self.threshold_frac = 0.20

        # Held state across blocks (so notes don't flicker out instantly).
        self._silent_frames = NOTE_HOLD_FRAMES
        self._midi: int | None = None
        self._midi_f: float | None = None
        self._hole: int | None = None
        self._action: str | None = None
        self._note_name = ""
        self._cents = 0.0
        self._chroma_buffer = np.zeros(CHORD_CHROMA_SAMPLES, dtype=np.float64)
        self._chroma_filled = 0

    # ----- threshold ------------------------------------------------------

    def gate_rms(self) -> float:
        db = MIN_DB + self.threshold_frac * (MAX_DB - MIN_DB)
        return float(10.0 ** (db / 20.0))

    def set_threshold(self, frac: float):
        self.threshold_frac = max(0.0, min(1.0, frac))

    # ----- stream ---------------------------------------------------------

    def _callback(self, indata, frames, time_info, status):
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _reset_chroma_buffer(self):
        self._chroma_buffer.fill(0.0)
        self._chroma_filled = 0

    def _append_chroma_samples(self, block: np.ndarray):
        block = np.asarray(block, dtype=np.float64)
        n = block.size
        if n >= CHORD_CHROMA_SAMPLES:
            self._chroma_buffer[:] = block[-CHORD_CHROMA_SAMPLES:]
            self._chroma_filled = CHORD_CHROMA_SAMPLES
            return
        self._chroma_buffer[:-n] = self._chroma_buffer[n:]
        self._chroma_buffer[-n:] = block
        self._chroma_filled = min(CHORD_CHROMA_SAMPLES, self._chroma_filled + n)

    def _chroma_source(self, fallback: np.ndarray) -> np.ndarray:
        if self._chroma_filled <= fallback.size:
            return fallback
        return self._chroma_buffer[-self._chroma_filled:]

    def start(self, device_index: int):
        self.stop()
        self._reset_chroma_buffer()
        try:
            self._stream = sd.InputStream(
                device=device_index, channels=1, samplerate=self.samplerate,
                blocksize=self.blocksize, callback=self._callback,
            )
            self._stream.start()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self._stream = None

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    # ----- polling --------------------------------------------------------

    def poll(self) -> AudioState | None:
        """Process the latest queued block. Returns None if no new audio."""
        blocks = []
        while True:
            try:
                blocks.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not blocks:
            return None

        block = blocks[-1]
        for queued in blocks:
            self._append_chroma_samples(queued)

        rms = float(np.sqrt(np.mean(np.square(block))))
        level = db_to_fraction(rms_to_dbfs(rms))
        chroma_vec = compute_chroma(self._chroma_source(block), self.samplerate)

        freq = detect_pitch(block, self.samplerate, rms_gate=self.gate_rms())
        if freq is not None:
            self._silent_frames = 0
            self._midi_f = freq_to_midi(freq)
            name, cents, midi = freq_to_note(freq)
            self._midi = midi
            self._note_name = name
            self._cents = cents
            locs = note_locations(midi)
            if locs:
                self._hole = locs[0].hole
                self._action = "blow" if "blow" in locs[0].action else "draw"
            else:
                self._hole = self._action = None
        else:
            self._silent_frames += 1
            if self._silent_frames > NOTE_HOLD_FRAMES:
                self._midi = None
                self._midi_f = None
                self._hole = self._action = None

        return AudioState(
            level_fraction=level,
            silent_frames=self._silent_frames,
            has_pitch=freq is not None,
            freq=freq,
            midi_f=self._midi_f,
            midi=self._midi,
            note_name=self._note_name,
            cents=self._cents,
            hole=self._hole,
            action=self._action,
            chroma=chroma_vec,
        )
