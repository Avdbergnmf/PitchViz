"""PitchViz - Bucket 1: Microphone level meter.

Opens a small window with a device picker and a live input-level bar,
similar to the Windows sound settings meter. No pitch detection yet.

Run:
    python level_meter.py
"""

import queue
import tkinter as tk
from tkinter import ttk

import numpy as np
import sounddevice as sd

# Level meter range, in dBFS (decibels relative to full scale).
# -60 dB is roughly "silence", 0 dB is the loudest a signal can be.
MIN_DB = -60.0
MAX_DB = 0.0

# How often the GUI refreshes, in milliseconds.
REFRESH_MS = 30

# How long the peak-hold marker lingers before it starts decaying.
PEAK_HOLD_FRAMES = 30


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
            label = f"{idx}: {dev['name']}"
            inputs.append((idx, label))
    return inputs


class LevelMeterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PitchViz - Mic Level Meter")
        self.root.geometry("460x200")
        self.root.minsize(420, 200)

        # Latest RMS value, passed from the audio thread to the GUI thread.
        self._rms_queue: "queue.Queue[float]" = queue.Queue(maxsize=8)
        self._stream: sd.InputStream | None = None
        self._peak_fraction = 0.0
        self._peak_age = 0

        self.devices = list_input_devices()
        self._build_ui()

        if self.devices:
            self.device_combo.current(0)
            self._start_stream(self.devices[0][0])
        else:
            self.status_var.set("No input devices found.")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_refresh()

    # ----- UI -------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        device_row = ttk.Frame(self.root)
        device_row.pack(fill="x", **pad)
        ttk.Label(device_row, text="Input device:").pack(side="left")
        self.device_combo = ttk.Combobox(
            device_row,
            state="readonly",
            values=[label for _, label in self.devices],
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)

        # The level bar is drawn on a canvas so we control colors/segments.
        self.canvas = tk.Canvas(self.root, height=46, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="x", **pad)

        info_row = ttk.Frame(self.root)
        info_row.pack(fill="x", **pad)
        self.level_var = tk.StringVar(value="-- dBFS")
        ttk.Label(info_row, textvariable=self.level_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="Listening...")
        ttk.Label(info_row, textvariable=self.status_var, foreground="#888").pack(side="right")

    def _draw_bar(self, fraction: float):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1:  # not laid out yet
            return

        # Background track.
        self.canvas.create_rectangle(0, 0, w, h, fill="#2b2b2b", outline="")

        fill_w = int(w * fraction)
        # Color the active portion green -> yellow -> red across thirds.
        for x in range(0, fill_w, 4):
            frac_x = x / w
            if frac_x < 0.6:
                color = "#3ddc84"
            elif frac_x < 0.85:
                color = "#f4d03f"
            else:
                color = "#e74c3c"
            self.canvas.create_rectangle(x, 0, x + 3, h, fill=color, outline="")

        # Peak-hold marker.
        peak_x = int(w * self._peak_fraction)
        if peak_x > 0:
            self.canvas.create_rectangle(peak_x - 1, 0, peak_x + 1, h, fill="#ffffff", outline="")

    # ----- Audio ----------------------------------------------------------

    def _audio_callback(self, indata, frames, time_info, status):
        # Runs on a separate (audio) thread; keep it light.
        mono = indata[:, 0] if indata.ndim > 1 else indata
        rms = float(np.sqrt(np.mean(np.square(mono))))
        try:
            self._rms_queue.put_nowait(rms)
        except queue.Full:
            pass

    def _start_stream(self, device_index: int):
        self._stop_stream()
        try:
            self._stream = sd.InputStream(
                device=device_index,
                channels=1,
                callback=self._audio_callback,
                blocksize=1024,
            )
            self._stream.start()
            self.status_var.set("Listening...")
        except Exception as exc:  # surface device errors in the UI
            self.status_var.set(f"Error: {exc}")
            self._stream = None

    def _stop_stream(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def _on_device_change(self, _event=None):
        selection = self.device_combo.current()
        if 0 <= selection < len(self.devices):
            self._start_stream(self.devices[selection][0])

    # ----- Loop -----------------------------------------------------------

    def _schedule_refresh(self):
        self._refresh()
        self.root.after(REFRESH_MS, self._schedule_refresh)

    def _refresh(self):
        # Drain to the most recent RMS reading available.
        rms = None
        while True:
            try:
                rms = self._rms_queue.get_nowait()
            except queue.Empty:
                break

        if rms is not None:
            db = rms_to_dbfs(rms)
            fraction = db_to_fraction(db)
            self.level_var.set(f"{db:5.1f} dBFS")

            if fraction >= self._peak_fraction:
                self._peak_fraction = fraction
                self._peak_age = 0
            else:
                self._peak_age += 1
                if self._peak_age > PEAK_HOLD_FRAMES:
                    self._peak_fraction = max(0.0, self._peak_fraction - 0.01)

            self._draw_bar(fraction)
        else:
            # No new audio; still let the peak marker decay smoothly.
            self._draw_bar(0.0)

    def _on_close(self):
        self._stop_stream()
        self.root.destroy()


def main():
    root = tk.Tk()
    LevelMeterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
