"""The toolkit shell: a window with a shared top bar and a tab per tool.

The shell owns the single ``AudioEngine`` and a few global services (mute,
note playback, playback suppression). It pumps the engine from the Tk loop and
forwards each ``AudioState`` to the *active* tool only.

Register tools by editing ``TOOLS`` below. Order = tab order.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from .core import synth
from .core.audio import AudioEngine
from .core.pitch import note_to_freq
from .core.theme import ACCENT_DIM, BG, FONT, MUTED, PANEL, PANEL2, RED, TEXT
from .tools.bend_trainer import BendTrainerTool
from .tools.jam_helper import JamHelperTool
from .widgets.levelmeter import LevelMeterWidget

REFRESH_MS = 30

# The toolkit's tools, in tab order. Add/remove entries to extend/trim the app.
TOOLS = [BendTrainerTool, JamHelperTool]


class ToolkitApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("PitchViz - Harmonica Toolkit (key of C)")
        root.configure(bg=BG)
        root.geometry("980x820")
        root.minsize(880, 740)

        self.engine = AudioEngine()
        self.muted = False
        self._suppress_until = 0.0
        self.tools: list = []
        self.active = None

        self._build_ui()
        self._register_tools()

        if self.engine.devices:
            self.device_combo.current(0)
            self.engine.start(self.engine.devices[0][0])
            self._set_status("Listening...")
        else:
            self._set_status("No input devices found.")

        self._select_active(0)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_refresh()

    # ----- shared services (used by tools) -------------------------------

    def play(self, midi: int):
        if self.muted:
            return
        synth.play_freq(note_to_freq(midi))
        self._suppress_until = time.monotonic() + synth.DURATION

    def play_click(self, accent: bool = False):
        if self.muted:
            return
        synth.play_click(accent)

    def play_chord(self, midis: list[int], duration: float | None = None):
        if self.muted:
            return
        dur = duration if duration is not None else synth.DURATION
        freqs = [note_to_freq(m) for m in midis]
        synth.play_chord(freqs, duration=dur)
        self._suppress_until = time.monotonic() + dur

    def play_success(self):
        if self.muted:
            return
        synth.play_success()
        self._suppress_until = time.monotonic() + 0.6

    def stop_audio(self):
        synth.stop()
        self._suppress_until = time.monotonic()

    def suppressed(self) -> bool:
        return time.monotonic() < self._suppress_until

    def practice_bend(self, hole: int, goal: int):
        """Jump to the Bend Trainer, locked on a hole with the given goal."""
        for i, tool in enumerate(self.tools):
            if isinstance(tool, BendTrainerTool):
                tool.practice(hole, goal)
                self.notebook.select(i)
                return

    # ----- UI -------------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                        foreground=TEXT, arrowcolor=TEXT, borderwidth=1)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED)],
                  selectbackground=[("readonly", ACCENT_DIM)],
                  selectforeground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground=PANEL2, foreground=TEXT,
                        background=PANEL2, arrowcolor=TEXT, borderwidth=1)
        style.map("TSpinbox",
                  fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED)])

        # Tabs: fixed size, color-only selected state (no grow/shrink on select).
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=[16, 8], borderwidth=0, font=(FONT, 10))
        style.map("TNotebook.Tab",
                  background=[("selected", BG), ("active", PANEL2)],
                  foreground=[("selected", TEXT), ("active", TEXT)],
                  padding=[("selected", [16, 8]), ("active", [16, 8])])

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(top, text="Input:").pack(side="left")
        self.device_combo = ttk.Combobox(
            top, state="readonly", values=[label for _, label in self.engine.devices])
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)
        self.mute_btn = tk.Button(top, text="\U0001F50A", command=self._toggle_mute,
                                  width=3, relief="flat", bg=PANEL, fg=TEXT,
                                  activebackground=PANEL2)
        self.mute_btn.pack(side="right")

        self.level = LevelMeterWidget(self.root, on_threshold=self._on_threshold)
        self.level.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(self.root, text="drag bar to set detection threshold (shared by all tools)",
                 bg=BG, fg="#666", font=(FONT, 8)).pack(anchor="w", padx=14, pady=(0, 6))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.status_var = tk.StringVar(value="Listening...")
        tk.Label(self.root, textvariable=self.status_var, bg=BG, fg=MUTED,
                 anchor="w").pack(fill="x", padx=14, pady=(0, 8))

    def _register_tools(self):
        for cls in TOOLS:
            tool = cls(self.notebook, self.engine, self)
            self.notebook.add(tool.frame, text=tool.title)
            self.tools.append(tool)

    def _set_status(self, text: str):
        self.status_var.set(text)

    # ----- global controls ------------------------------------------------

    def _toggle_mute(self):
        self.muted = not self.muted
        self.mute_btn.config(text="\U0001F507" if self.muted else "\U0001F50A",
                             fg=RED if self.muted else TEXT)
        if self.muted:
            synth.stop()

    def _on_threshold(self, frac: float):
        self.engine.set_threshold(frac)
        self.level.threshold_frac = frac
        self.level.redraw()

    def _on_device_change(self, _event=None):
        sel = self.device_combo.current()
        if 0 <= sel < len(self.engine.devices):
            self.engine.start(self.engine.devices[sel][0])
            self._set_status(self.engine.last_error or "Listening...")

    # ----- tab switching --------------------------------------------------

    def _select_active(self, index: int):
        if 0 <= index < len(self.tools):
            self.active = self.tools[index]
            self.active.on_show()

    def _on_tab_changed(self, _event=None):
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        if self.active is self.tools[index]:
            return
        if self.active is not None:
            self.active.on_hide()
        self._select_active(index)

    # ----- main loop ------------------------------------------------------

    def _schedule_refresh(self):
        self._refresh()
        self.root.after(REFRESH_MS, self._schedule_refresh)

    def _refresh(self):
        state = self.engine.poll()
        if state is None:
            return
        self.level.level_fraction = state.level_fraction
        self.level.threshold_frac = self.engine.threshold_frac
        self.level.redraw()
        if self.active is not None:
            self.active.on_audio(state)

    def _on_close(self):
        synth.stop()
        self.engine.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    ToolkitApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
