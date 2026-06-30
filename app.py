"""PitchViz - harmonica bend trainer GUI (key of C).

  - mic input level with a draggable detection-threshold slider; the detected
    note is shown as an outline so it doesn't clash with blow/draw colors
  - a clickable 10-hole harmonica diagram. Each hole: top = blow, bottom = draw
    (with note labels). The middle is a "lock" button containing the hole number
    and a small vertical bend-bar (blow top / draw bottom) mirroring the practice
    panel (goal lines + success), and a live position marker.
  - the main bend practice panel: the whole hole as a horizontal pitch ladder
    from blow (blue) to draw (orange) with the bend targets in between, a live
    marker, a recede-able peak indicator, a goal, and a hold meter with a
    best-hold high-water mark that rewards holding the goal (configurable
    seconds) with a chime + success color.
  - a clickable piano keyboard. Hovering a note anywhere cross-highlights it.

Pitch indication works whether or not a hole is locked; progress (hold / best
hold / success) is recorded only while locked.

Run:
    python app.py
"""

import queue
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
import sounddevice as sd

import synth
from harmonica import (
    HARMONICA_C,
    HIGHEST_MIDI,
    LOWEST_MIDI,
    describe,
    hole_ladder,
    midi_name,
    note_locations,
)
from level_meter import (
    MAX_DB,
    MIN_DB,
    db_to_fraction,
    list_input_devices,
    rms_to_dbfs,
)
from pitch import detect_pitch, freq_to_midi, freq_to_note, note_to_freq

SAMPLERATE = 44100
BLOCKSIZE = 2048
REFRESH_MS = 30
FPS_EST = SAMPLERATE / BLOCKSIZE

NOTE_HOLD_FRAMES = 12
LOCK_CENTS = 15
NEAR_CENTS = 35
RANGE_MARGIN = 0.6

WHITE_SEMITONES = {0, 2, 4, 5, 7, 9, 11}

# Colors.
BG = "#1e1e1e"
PANEL = "#2b2b2b"
PANEL2 = "#333842"
DARK = "#262a31"
WHITE_KEY = "#f5f5f5"
WHITE_KEY_EDGE = "#cccccc"
BLACK_KEY = "#222222"
GREEN = "#3ddc84"
YELLOW = "#f4d03f"
RED = "#e74c3c"
TEXT = "#e8e8e8"
MUTED = "#888888"
BLOW_C = "#5aa9e6"
DRAW_C = "#f39c5a"
BLOW_HOVER = "#3d5a73"
DRAW_HOVER = "#73553d"
WHITE_KEY_HOVER = "#e6edf5"
BLACK_KEY_HOVER = "#3a3f47"
GOLD = "#ffd166"
PEAK_C = "#4d96ff"
SUCCESS_C = "#39e0c0"
DETECT_OUTLINE = "#9be8b0"
DIM = "#565c66"          # low-contrast frozen marker (out of range)
HOVER_NOTE = "#ffffff"

DEFAULT_INERTIA = 0.85
DEFAULT_MARKER_SMOOTHING = 0.3
DEFAULT_HOLD_SECONDS = 3.0
DEFAULT_THRESHOLD = 0.20


def is_white(midi: int) -> bool:
    return (midi % 12) in WHITE_SEMITONES


def accuracy_color(cents_abs: float) -> str:
    if cents_abs <= LOCK_CENTS:
        return GREEN
    if cents_abs <= NEAR_CENTS:
        return YELLOW
    return RED


def white_midis():
    return [m for m in range(LOWEST_MIDI, HIGHEST_MIDI + 1) if is_white(m)]


class PitchVizApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PitchViz - Harmonica Bend Trainer (key of C)")
        self.root.configure(bg=BG)
        self.root.geometry("980x900")
        self.root.minsize(880, 840)

        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=16)
        self._stream: sd.InputStream | None = None

        # Live mic state.
        self._level_fraction = 0.0
        self._current_midi: int | None = None
        self._live_midi_f: float | None = None
        self._silent_frames = NOTE_HOLD_FRAMES
        self._live_hole: int | None = None
        self._live_action: str | None = None

        # Selection (playback highlight) + locked practice hole.
        self._selection: tuple | None = None
        self._locked_hole: int | None = None

        # Per-hole persistent.
        self._goal_by_hole: dict[int, int] = {}
        self._best_hold: dict[tuple[int, int], float] = {}   # (hole, goal) -> max hold frac
        self._succeeded: set[tuple[int, int]] = set()

        # Active-hole transient (pitch indication).
        self._smooth_hole: int | None = None
        self._display_midi: float | None = None        # smoothed live pitch
        self._last_display_midi: float | None = None    # frozen on out-of-range
        self._in_range = False
        self._peak_progress = 0.0
        self._hold_frames = 0
        self._hold_fraction = 0.0

        # Hover.
        self._hover_hole: int | None = None
        self._hover_zone: str | None = None
        self._hover_note: int | None = None

        # Settings + playback suppression.
        self._muted = False
        self._inertia = DEFAULT_INERTIA
        self._marker_smoothing = DEFAULT_MARKER_SMOOTHING
        self._hold_seconds = DEFAULT_HOLD_SECONDS
        self._threshold_frac = DEFAULT_THRESHOLD
        self._suppress_until = 0.0
        self._settings_win: tk.Toplevel | None = None

        self._bend_geom: dict | None = None

        self.devices = list_input_devices()
        self._build_ui()

        if self.devices:
            self.device_combo.current(0)
            self._start_stream(self.devices[0][0])
        else:
            self.status_var.set("No input devices found.")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_refresh()

    # ----- UI construction ------------------------------------------------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(top, text="Input:").pack(side="left")
        self.device_combo = ttk.Combobox(
            top, state="readonly", values=[label for _, label in self.devices],
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)
        self.gear_btn = tk.Button(top, text="\u2699", command=self._open_settings,
                                  width=3, relief="flat", bg=PANEL, fg=TEXT,
                                  activebackground=PANEL2)
        self.gear_btn.pack(side="right")
        self.mute_btn = tk.Button(top, text="\U0001F50A", command=self._toggle_mute,
                                  width=3, relief="flat", bg=PANEL, fg=TEXT,
                                  activebackground=PANEL2)
        self.mute_btn.pack(side="right", padx=(0, 6))

        # Level meter + draggable detection-threshold marker.
        self.level_canvas = tk.Canvas(self.root, height=22, bg=PANEL, highlightthickness=0,
                                      cursor="sb_h_double_arrow")
        self.level_canvas.pack(fill="x", padx=14, pady=(2, 0))
        self.level_canvas.bind("<Button-1>", self._on_threshold_drag)
        self.level_canvas.bind("<B1-Motion>", self._on_threshold_drag)
        tk.Label(self.root, text="drag bar to set detection threshold", bg=BG, fg="#666",
                 font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=(0, 6))

        readout = ttk.Frame(self.root)
        readout.pack(fill="x", padx=14)
        self.note_var = tk.StringVar(value="--")
        self.detail_var = tk.StringVar(value="")
        self.hole_var = tk.StringVar(value="play or click a note...")
        tk.Label(readout, textvariable=self.note_var, bg=BG, fg=TEXT,
                 font=("Segoe UI", 34, "bold"), width=5, anchor="w").pack(side="left")
        mid = ttk.Frame(readout)
        mid.pack(side="left", padx=(16, 0), anchor="s", pady=(0, 10))
        tk.Label(mid, textvariable=self.detail_var, bg=BG, fg=MUTED,
                 font=("Segoe UI", 12)).pack(anchor="w")
        tk.Label(mid, textvariable=self.hole_var, bg=BG, fg=GREEN,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")

        self.harp_canvas = tk.Canvas(self.root, height=140, bg=BG, highlightthickness=0)
        self.harp_canvas.pack(fill="x", padx=14, pady=(8, 8))
        self.harp_canvas.bind("<Configure>", lambda e: self._draw_harp())
        self.harp_canvas.bind("<Button-1>", self._on_harp_click)
        self.harp_canvas.bind("<Motion>", self._on_harp_motion)
        self.harp_canvas.bind("<Leave>", self._on_harp_leave)

        tk.Label(self.root, text="Bend practice", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14)
        self.bend_canvas = tk.Canvas(self.root, height=220, bg=PANEL, highlightthickness=0)
        self.bend_canvas.pack(fill="x", padx=14, pady=(2, 8))
        self.bend_canvas.bind("<Configure>", lambda e: self._draw_bend())
        self.bend_canvas.bind("<Button-1>", self._on_bend_click)
        self.bend_canvas.bind("<Motion>", self._on_bend_motion)
        self.bend_canvas.bind("<Leave>", self._on_bend_leave)

        self.piano_canvas = tk.Canvas(self.root, height=120, bg=BG, highlightthickness=0)
        self.piano_canvas.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.piano_canvas.bind("<Configure>", lambda e: self._draw_piano())
        self.piano_canvas.bind("<Button-1>", self._on_piano_click)
        self.piano_canvas.bind("<Motion>", self._on_piano_motion)
        self.piano_canvas.bind("<Leave>", self._on_piano_leave)

        self.status_var = tk.StringVar(value="Listening...")
        tk.Label(self.root, textvariable=self.status_var, bg=BG, fg=MUTED,
                 anchor="w").pack(fill="x", padx=14, pady=(0, 8))

    # ----- Detection threshold -------------------------------------------

    def _gate_rms(self) -> float:
        db = MIN_DB + self._threshold_frac * (MAX_DB - MIN_DB)
        return float(10.0 ** (db / 20.0))

    def _on_threshold_drag(self, event):
        w = self.level_canvas.winfo_width()
        if w > 1:
            self._threshold_frac = max(0.0, min(1.0, event.x / w))
            self._draw_level()

    # ----- Metrics --------------------------------------------------------

    def _practice_hole(self) -> int | None:
        if self._locked_hole is not None:
            return self._locked_hole
        if (self._live_hole is not None and self._silent_frames == 0
                and hole_ladder(self._live_hole).has_bends):
            return self._live_hole
        return None

    def _goal_of(self, hole: int) -> int:
        return self._goal_by_hole.get(hole, hole_ladder(hole).deepest_bend)

    def _hole_metrics(self, hole: int) -> dict:
        lad = hole_ladder(hole)
        natural, deepest = lad.natural, lad.deepest_bend
        span = natural - deepest
        goal = self._goal_of(hole)
        best_hold = self._best_hold.get((hole, goal), 0.0)
        return {"lad": lad, "natural": natural, "deepest": deepest, "span": span,
                "goal": goal, "best_hold": best_hold,
                "succeeded": (hole, goal) in self._succeeded}

    # ----- Selection + playback ------------------------------------------

    def _play(self, midi: int):
        if self._muted:
            return
        synth.play_freq(note_to_freq(midi))
        self._suppress_until = time.monotonic() + synth.DURATION

    def _toggle_mute(self):
        self._muted = not self._muted
        self.mute_btn.config(text="\U0001F507" if self._muted else "\U0001F50A",
                             fg=RED if self._muted else TEXT)
        if self._muted:
            synth.stop()

    def _select_play(self, hole, action, steps, midi):
        self._selection = (hole, action, steps, midi)
        self._play(midi)

    def _toggle_lock(self, hole):
        lad = hole_ladder(hole)
        if not lad.has_bends:
            return
        if self._locked_hole == hole:
            self._locked_hole = None
            return
        self._locked_hole = hole
        self._selection = (hole, lad.natural_action, 0, lad.natural)
        self._play(self._goal_of(hole))

    def _reset_hole(self, hole):
        goal = self._goal_of(hole)
        self._best_hold.pop((hole, goal), None)
        self._succeeded.discard((hole, goal))

    def _reset_all(self):
        self._best_hold.clear()
        self._succeeded.clear()
        self._peak_progress = 0.0

    # ----- Click handlers -------------------------------------------------

    def _on_harp_click(self, event):
        w, h = self.harp_canvas.winfo_width(), self.harp_canvas.winfo_height()
        if w <= 1:
            return
        hole = max(1, min(10, int(event.x // (w / 10)) + 1))
        lad = hole_ladder(hole)
        if event.y < h * 0.26:
            self._select_play(hole, "blow", 0, lad.blow)
        elif event.y > h * 0.74:
            self._select_play(hole, "draw", 0, lad.draw)
        else:
            self._toggle_lock(hole)

    def _on_piano_click(self, event):
        midi = self._piano_midi_at(event.x, event.y)
        if midi is None:
            return
        locs = note_locations(midi)
        if locs:
            loc = locs[0]
            action = "blow" if "blow" in loc.action else "draw"
            if loc.bend_steps > 0:
                self._goal_by_hole[loc.hole] = midi
            self._select_play(loc.hole, action, loc.bend_steps, midi)
        else:
            self._select_play(0, "none", 0, midi)

    def _on_bend_click(self, event):
        geom = self._bend_geom
        if not geom:
            return
        rb = geom.get("reset_rect")
        if rb and rb[0] <= event.x <= rb[2] and rb[1] <= event.y <= rb[3]:
            self._reset_hole(geom["hole"])
            return
        lo, hi = geom["lo"], geom["hi"]
        x_left, x_right = geom["x_left"], geom["x_right"]
        if x_right <= x_left:
            return
        midi = max(lo, min(hi, round(lo + (event.x - x_left) / (x_right - x_left) * (hi - lo))))
        hole = geom["hole"]
        if midi in hole_ladder(hole).bend_notes:
            self._goal_by_hole[hole] = midi
        self._play(midi)

    def _piano_midi_at(self, x, y):
        w, h = self.piano_canvas.winfo_width(), self.piano_canvas.winfo_height()
        if w <= 1:
            return None
        whites = white_midis()
        key_w = w / len(whites)
        bh, bw = int(h * 0.62), key_w * 0.62
        if y <= bh:
            for midi in range(LOWEST_MIDI, HIGHEST_MIDI + 1):
                if is_white(midi):
                    continue
                cx = sum(1 for m in whites if m < midi) * key_w
                if abs(x - cx) <= bw / 2:
                    return midi
        return whites[max(0, min(len(whites) - 1, int(x // key_w)))]

    # ----- Hover handlers -------------------------------------------------

    def _set_hover_note(self, note):
        if note != self._hover_note:
            self._hover_note = note
            self._draw_piano()
            self._draw_harp()
            self._draw_bend()

    def _on_harp_motion(self, event):
        w, h = self.harp_canvas.winfo_width(), self.harp_canvas.winfo_height()
        if w <= 1:
            return
        hole = max(1, min(10, int(event.x // (w / 10)) + 1))
        lad = hole_ladder(hole)
        if event.y < h * 0.26:
            zone, note = "blow", lad.blow
        elif event.y > h * 0.74:
            zone, note = "draw", lad.draw
        else:
            zone, note = "mid", None
        if (hole, zone) != (self._hover_hole, self._hover_zone) or note != self._hover_note:
            self._hover_hole, self._hover_zone, self._hover_note = hole, zone, note
            self._draw_harp()
            self._draw_piano()
            self._draw_bend()

    def _on_harp_leave(self, _e):
        self._hover_hole = self._hover_zone = None
        self._set_hover_note(None)
        self._draw_harp()

    def _on_piano_motion(self, event):
        self._set_hover_note(self._piano_midi_at(event.x, event.y))

    def _on_piano_leave(self, _e):
        self._set_hover_note(None)

    def _on_bend_motion(self, event):
        geom = self._bend_geom
        if not geom:
            return
        lo, hi = geom["lo"], geom["hi"]
        x_left, x_right = geom["x_left"], geom["x_right"]
        if x_right <= x_left:
            return
        midi = round(lo + (event.x - x_left) / (x_right - x_left) * (hi - lo))
        note = midi if lo <= midi <= hi else None
        self._set_hover_note(note)

    def _on_bend_leave(self, _e):
        self._set_hover_note(None)

    # ----- Drawing: level + piano ----------------------------------------

    def _draw_level(self):
        c = self.level_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.create_rectangle(0, 0, w, h, fill=PANEL, outline="")
        fill_w = int(w * self._level_fraction)
        for x in range(0, fill_w, 4):
            frac_x = x / w
            color = GREEN if frac_x < 0.6 else YELLOW if frac_x < 0.85 else RED
            c.create_rectangle(x, 0, x + 3, h, fill=color, outline="")
        tx = int(self._threshold_frac * w)
        c.create_line(tx, 0, tx, h, fill="#fff", width=2)
        c.create_polygon(tx, 6, tx - 5, 0, tx + 5, 0, fill="#fff", outline="")

    def _draw_piano(self):
        c = self.piano_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        whites = white_midis()
        key_w = w / len(whites)

        sel_blow = sel_draw = sel_midi = None
        if self._selection:
            hole = self._selection[0]
            if hole != 0:
                sel_blow = HARMONICA_C[hole]["blow"]
                sel_draw = HARMONICA_C[hole]["draw"]
            sel_midi = self._selection[3]
        detected = self._current_midi if self._silent_frames == 0 else None

        def fill_for(midi, base, hover_col):
            if midi == sel_blow:
                return BLOW_C
            if midi == sel_draw:
                return DRAW_C
            if midi == sel_midi:
                return GOLD
            if midi == self._hover_note:
                return hover_col
            return base

        for i, midi in enumerate(whites):
            x0, x1 = i * key_w, (i + 1) * key_w
            c.create_rectangle(x0, 0, x1, h, fill=fill_for(midi, WHITE_KEY, WHITE_KEY_HOVER),
                               outline=WHITE_KEY_EDGE)
            if midi == detected:
                c.create_rectangle(x0 + 2, 2, x1 - 2, h - 2, outline=DETECT_OUTLINE, width=3)
            if midi % 12 == 0:
                c.create_text((x0 + x1) / 2, h - 12, text=midi_name(midi),
                              fill="#666", font=("Segoe UI", 8))

        bh, bw = int(h * 0.62), key_w * 0.62
        for midi in range(LOWEST_MIDI, HIGHEST_MIDI + 1):
            if is_white(midi):
                continue
            cx = sum(1 for m in whites if m < midi) * key_w
            c.create_rectangle(cx - bw / 2, 0, cx + bw / 2, bh,
                               fill=fill_for(midi, BLACK_KEY, BLACK_KEY_HOVER), outline="#000")
            if midi == detected:
                c.create_rectangle(cx - bw / 2 + 1, 2, cx + bw / 2 - 1, bh - 2,
                                   outline=DETECT_OUTLINE, width=2)

    # ----- Drawing: harmonica diagram ------------------------------------

    def _draw_harp(self):
        c = self.harp_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        cw = w / 10
        top_b, bot_b = h * 0.26, h * 0.74
        silent = self._silent_frames > 0
        sel = self._selection
        practice = self._practice_hole()

        for hole in range(1, 11):
            lad = hole_ladder(hole)
            x0, x1 = (hole - 1) * cw + 3, hole * cw - 3
            cx = (x0 + x1) / 2
            c.create_rectangle(x0, 0, x1, h, fill=PANEL, outline="#444")

            sel_here = sel is not None and sel[0] == hole

            # Blow zone (top).
            if sel_here and sel[1] == "blow":
                c.create_rectangle(x0, 0, x1, top_b, fill=BLOW_C, outline="")
                bcol = "#fff"
            elif (self._hover_hole == hole and self._hover_zone == "blow") or self._hover_note == lad.blow:
                c.create_rectangle(x0, 0, x1, top_b, fill=BLOW_HOVER, outline="")
                bcol = "#fff"
            else:
                bcol = "#cfd8e3"
            c.create_text(cx, 11, text=midi_name(lad.blow), fill=bcol, font=("Segoe UI", 8))

            # Draw zone (bottom).
            if sel_here and sel[1] == "draw":
                c.create_rectangle(x0, bot_b, x1, h, fill=DRAW_C, outline="")
                dcol = "#fff"
            elif (self._hover_hole == hole and self._hover_zone == "draw") or self._hover_note == lad.draw:
                c.create_rectangle(x0, bot_b, x1, h, fill=DRAW_HOVER, outline="")
                dcol = "#fff"
            else:
                dcol = "#cfd8e3"
            c.create_text(cx, h - 11, text=midi_name(lad.draw), fill=dcol, font=("Segoe UI", 8))

            # Lock button (middle): number on left + vertical bend-bar.
            self._draw_lock_widget(c, hole, x0, x1, top_b, bot_b, practice, silent)

            # Detected reed outline.
            if not silent and hole == self._live_hole:
                if self._live_action == "blow":
                    c.create_rectangle(x0 + 1, 1, x1 - 1, top_b, outline=DETECT_OUTLINE, width=2)
                elif self._live_action == "draw":
                    c.create_rectangle(x0 + 1, bot_b, x1 - 1, h - 1, outline=DETECT_OUTLINE, width=2)

    def _draw_lock_widget(self, c, hole, x0, x1, top_b, bot_b, practice, silent):
        lad = hole_ladder(hole)
        locked = self._locked_hole == hole
        hover_mid = self._hover_hole == hole and self._hover_zone == "mid"
        bx0, bx1 = x0 + 5, x1 - 5
        by0, by1 = top_b + 3, bot_b - 3
        outline = GOLD if locked else ("#cfd8e3" if hover_mid else "#3a3f47")
        c.create_rectangle(bx0, by0, bx1, by1, outline=outline,
                           width=2 if (locked or hover_mid) else 1, fill=DARK)

        # Hole number on the left.
        c.create_text(bx0 + (bx1 - bx0) * 0.24, (by0 + by1) / 2, text=str(hole),
                      fill="#dfe6ef", font=("Segoe UI", 12, "bold"))

        if not lad.has_bends:
            return

        met = self._hole_metrics(hole)
        goal, succeeded = met["goal"], met["succeeded"]
        bar_x = bx0 + (bx1 - bx0) * 0.62
        ya, yb = by0 + 6, by1 - 6  # blow at ya (top), draw at yb (bottom)

        def y_of(midi):
            return ya + (midi - lad.blow) / (lad.draw - lad.blow) * (yb - ya)

        c.create_line(bar_x, ya, bar_x, yb, fill="#555", width=2)
        # Bend goal lines (small ticks); goal emphasized; success -> teal.
        for midi in lad.bend_notes:
            y = y_of(midi)
            if midi == goal:
                col = SUCCESS_C if succeeded else GOLD
                ww = 8
            else:
                col = "#8a8f98"
                ww = 5
            c.create_line(bar_x - ww, y, bar_x + ww, y, fill=col, width=2)

        # Live position marker (pitch indication; works locked or not).
        if practice == hole and (self._display_midi is not None):
            col = DIM if not self._in_range else "#fff"
            y = y_of(max(lad.lo, min(lad.hi, self._display_midi)))
            c.create_polygon(bar_x + 9, y, bar_x + 16, y - 4, bar_x + 16, y + 4,
                             fill=col, outline="")

    # ----- Drawing: bend practice panel ----------------------------------

    def _draw_bend(self):
        c = self.bend_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.create_rectangle(0, 0, w, h, fill=PANEL, outline="")
        self._bend_geom = None

        hole = self._practice_hole()
        if hole is None:
            c.create_text(w / 2, h / 2 - 8, text="Play or lock a bendable hole to practice",
                          fill=MUTED, font=("Segoe UI", 13))
            c.create_text(w / 2, h / 2 + 16, text="click a hole's middle (lock button) to lock it",
                          fill="#666", font=("Segoe UI", 10))
            return

        met = self._hole_metrics(hole)
        lad = met["lad"]
        lo, hi = lad.lo, lad.hi
        natural, deepest, span = met["natural"], met["deepest"], met["span"]
        goal, succeeded = met["goal"], met["succeeded"]
        locked = self._locked_hole == hole

        margin_x = 70
        x_left, x_right = margin_x, w - margin_x
        bar_y, bar_h = h * 0.46, 30
        reset_rect = (w - 64, 10, w - 12, 32)
        self._bend_geom = {"hole": hole, "x_left": x_left, "x_right": x_right,
                           "lo": lo, "hi": hi, "reset_rect": reset_rect}

        def px(midi):
            return x_left + (midi - lo) / (hi - lo) * (x_right - x_left)

        has_live = self._live_midi_f is not None and self._silent_frames == 0
        nearest = cents = cents_abs = None
        if has_live and self._in_range:
            cand = lad.notes[1:]
            nearest = min(cand, key=lambda mm: abs(mm - self._live_midi_f))
            cents = (self._live_midi_f - nearest) * 100.0
            cents_abs = abs(cents)

        lock_txt = "(locked)" if locked else "- lock to record progress"
        c.create_text(16, 18, anchor="w",
                      text=f"Hole {hole} {lad.natural_action} {lock_txt}    goal: {midi_name(goal)}",
                      fill=SUCCESS_C if succeeded else TEXT, font=("Segoe UI", 13, "bold"))

        c.create_rectangle(*reset_rect, outline="#666", fill=PANEL2)
        c.create_text((reset_rect[0] + reset_rect[2]) / 2, (reset_rect[1] + reset_rect[3]) / 2,
                      text="reset", fill=PEAK_C, font=("Segoe UI", 9))

        c.create_rectangle(x_left, bar_y - bar_h / 2, x_right, bar_y + bar_h / 2,
                           fill=PANEL2, outline="#555")
        c.create_rectangle(x_left, bar_y - bar_h / 2, px(deepest), bar_y + bar_h / 2,
                           fill=DARK, outline="")

        # Live marker (pitch indication, always). Out of range -> frozen + dim.
        disp = self._display_midi
        if has_live and disp is not None:
            mcol = DIM if not self._in_range else accuracy_color(cents_abs if cents_abs is not None else 99)
            mx = px(max(lo, min(hi, disp)))
            c.create_rectangle(mx, bar_y - bar_h / 2, px(natural), bar_y + bar_h / 2,
                               fill=mcol if self._in_range else DARK, outline="")
            c.create_polygon(mx, bar_y - bar_h / 2 - 2, mx - 7, bar_y - bar_h / 2 - 14,
                             mx + 7, bar_y - bar_h / 2 - 14, fill=mcol, outline="")
            c.create_line(mx, bar_y - bar_h / 2, mx, bar_y + bar_h / 2, fill=mcol, width=2)

        # Recede-able peak indicator (pitch indication, always).
        if self._peak_progress > 0.01:
            pxp = px(natural - self._peak_progress * span)
            c.create_line(pxp, bar_y - bar_h / 2 - 6, pxp, bar_y + bar_h / 2 + 6,
                          fill=PEAK_C, width=3)
            c.create_text(pxp, bar_y + bar_h / 2 + 34, text="peak", fill=PEAK_C,
                          font=("Segoe UI", 8, "bold"))

        # Ticks + labels.
        for midi in lad.notes:
            x = px(midi)
            col = BLOW_C if midi == lad.blow else DRAW_C if midi == lad.draw else "#bbb"
            is_goal = midi == goal
            is_near = nearest == midi
            is_hover = midi == self._hover_note
            locked_on = is_near and cents_abs is not None and cents_abs <= LOCK_CENTS
            tcol = (SUCCESS_C if (is_goal and succeeded) else GREEN if locked_on
                    else GOLD if is_goal else HOVER_NOTE if is_hover else col)
            wln = 3 if (is_goal or is_near or is_hover) else 1
            c.create_line(x, bar_y - bar_h / 2 - 10, x, bar_y + bar_h / 2 + 10, fill=tcol, width=wln)
            if is_goal:
                c.create_oval(x - 7, bar_y - bar_h / 2 - 25, x + 7, bar_y - bar_h / 2 - 11,
                              outline=tcol, width=2)
            c.create_text(x, bar_y - bar_h / 2 - 25, text=midi_name(midi), fill=tcol,
                          font=("Segoe UI", 9, "bold" if (is_goal or is_near or is_hover) else "normal"))
            label = "blow" if midi == lad.blow else "draw" if midi == lad.draw else \
                f"bend {natural - midi}"
            c.create_text(x, bar_y + bar_h / 2 + 22, text=label, fill="#888", font=("Segoe UI", 8))

        # Status line.
        if has_live and self._in_range and cents is not None:
            sign = "+" if cents >= 0 else ""
            status = "LOCKED" if cents_abs <= LOCK_CENTS else "close" if cents_abs <= NEAR_CENTS else ""
            c.create_text(16, h - 54, anchor="w",
                          text=f"nearest: {midi_name(nearest)}   {sign}{cents:.0f} cents   {status}",
                          fill=accuracy_color(cents_abs), font=("Segoe UI", 12, "bold"))
        elif has_live:
            c.create_text(16, h - 54, anchor="w", text="(off range)", fill=DIM,
                          font=("Segoe UI", 12, "bold"))

        # Hold meter with best-hold high-water mark.
        hm_x0, hm_x1, hm_y, hm_h = 16, w - 16, h - 26, 16
        label = f"{midi_name(goal)} done!" if succeeded else (
            f"hold {midi_name(goal)} for {self._hold_seconds:.0f}s"
            + ("" if locked else "  (lock to record)"))
        c.create_text(hm_x0, hm_y - 9, anchor="w", text=label, fill=MUTED, font=("Segoe UI", 9))
        if locked and self._hold_fraction > 0 and not succeeded:
            secs_left = self._hold_seconds * (1.0 - self._hold_fraction)
            c.create_text(hm_x1, hm_y - 9, anchor="e", text=f"{secs_left:.1f}s",
                          fill=TEXT, font=("Segoe UI", 9))
        c.create_rectangle(hm_x0, hm_y, hm_x1, hm_y + hm_h, fill=PANEL2, outline="#555")
        if succeeded:
            c.create_rectangle(hm_x0, hm_y, hm_x1, hm_y + hm_h, fill=SUCCESS_C, outline="")
            c.create_text(w / 2, hm_y + hm_h / 2, text="\u2713 goal held",
                          fill="#10302a", font=("Segoe UI", 9, "bold"))
        else:
            if locked and self._hold_fraction > 0:
                c.create_rectangle(hm_x0, hm_y, hm_x0 + (hm_x1 - hm_x0) * self._hold_fraction,
                                   hm_y + hm_h, fill=GREEN, outline="")
            bh_frac = met["best_hold"]
            if bh_frac > 0.01:
                bx = hm_x0 + (hm_x1 - hm_x0) * bh_frac
                c.create_line(bx, hm_y - 2, bx, hm_y + hm_h + 2, fill="#fff", width=2)
                c.create_text(bx, hm_y - 9, text="best", fill="#fff", anchor="center",
                              font=("Segoe UI", 7))

    # ----- Settings window ------------------------------------------------

    def _open_settings(self):
        if self._settings_win is not None and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=BG)
        win.geometry("330x330")
        win.transient(self.root)
        self._settings_win = win

        def slider(label, sub, frm, to, init, setter):
            tk.Label(win, text=label, bg=BG, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
            tk.Label(win, text=sub, bg=BG, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=14)
            s = tk.Scale(win, from_=frm, to=to, orient="horizontal", bg=BG, fg=TEXT,
                         highlightthickness=0, troughcolor=PANEL2, command=setter)
            s.set(init)
            s.pack(fill="x", padx=14)

        slider("Peak inertia (noise resistance)",
               "higher = smoother/slower peak (ignores quick spikes)",
               0, 95, int(self._inertia * 100),
               lambda v: setattr(self, "_inertia", float(v) / 100.0))
        slider("Live marker smoothing", "higher = smoother but laggier needle",
               0, 90, int(self._marker_smoothing * 100),
               lambda v: setattr(self, "_marker_smoothing", float(v) / 100.0))
        slider("Hold-to-succeed (seconds)", "how long to hold the goal in tune",
               1, 6, int(self._hold_seconds),
               lambda v: setattr(self, "_hold_seconds", float(v)))

        tk.Button(win, text="Reset all bests", command=self._reset_all,
                  relief="flat", bg=PANEL, fg=PEAK_C, activebackground=PANEL2).pack(pady=14)

    # ----- Audio ----------------------------------------------------------

    def _audio_callback(self, indata, frames, time_info, status):
        try:
            self._audio_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _start_stream(self, device_index: int):
        self._stop_stream()
        try:
            self._stream = sd.InputStream(
                device=device_index, channels=1, samplerate=SAMPLERATE,
                blocksize=BLOCKSIZE, callback=self._audio_callback,
            )
            self._stream.start()
            self.status_var.set("Listening...")
        except Exception as exc:
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
        sel = self.device_combo.current()
        if 0 <= sel < len(self.devices):
            self._start_stream(self.devices[sel][0])

    # ----- Loop -----------------------------------------------------------

    def _schedule_refresh(self):
        self._refresh()
        self.root.after(REFRESH_MS, self._schedule_refresh)

    def _refresh(self):
        block = None
        while True:
            try:
                block = self._audio_q.get_nowait()
            except queue.Empty:
                break
        if block is None:
            return

        rms = float(np.sqrt(np.mean(np.square(block))))
        self._level_fraction = db_to_fraction(rms_to_dbfs(rms))

        freq = detect_pitch(block, SAMPLERATE, rms_gate=self._gate_rms())
        if freq is not None:
            self._silent_frames = 0
            self._live_midi_f = freq_to_midi(freq)
            note_name, cents, midi = freq_to_note(freq)
            self._current_midi = midi
            sign = "+" if cents >= 0 else ""
            self.note_var.set(note_name)
            self.detail_var.set(f"{freq:.1f} Hz   {sign}{cents:.0f} cents")
            self.hole_var.set(describe(midi))
            locs = note_locations(midi)
            if locs:
                self._live_hole = locs[0].hole
                self._live_action = "blow" if "blow" in locs[0].action else "draw"
            else:
                self._live_hole = self._live_action = None
        else:
            self._silent_frames += 1
            if self._silent_frames > NOTE_HOLD_FRAMES:
                self._current_midi = None
                self._live_midi_f = None
                self._live_hole = self._live_action = None
                if self._selection is None:
                    self.note_var.set("--")
                    self.detail_var.set("")
                    self.hole_var.set("play or click a note...")

        self._update_practice()

        self._draw_level()
        self._draw_bend()
        self._draw_harp()
        self._draw_piano()

    def _update_practice(self):
        hole = self._practice_hole()
        if hole != self._smooth_hole:
            self._smooth_hole = hole
            self._peak_progress = 0.0
            self._display_midi = None
            self._last_display_midi = None

        has_live = self._live_midi_f is not None and self._silent_frames == 0
        if hole is None or not has_live:
            self._hold_frames = 0
            self._hold_fraction = 0.0
            self._in_range = False
            return

        met = self._hole_metrics(hole)
        natural, span, goal, deepest = met["natural"], met["span"], met["goal"], met["deepest"]
        self._in_range = deepest - RANGE_MARGIN <= self._live_midi_f <= natural + RANGE_MARGIN

        # Pitch indication (always): smoothed display pitch + recede-able peak.
        if self._in_range:
            a = 1.0 - self._marker_smoothing
            if self._display_midi is None:
                self._display_midi = self._live_midi_f
            else:
                self._display_midi += a * (self._live_midi_f - self._display_midi)
            self._last_display_midi = self._display_midi
            depth_frac = max(0.0, min(1.0, (natural - self._live_midi_f) / span)) if span else 0.0
            self._peak_progress += (1.0 - self._inertia) * (depth_frac - self._peak_progress)
        else:
            self._display_midi = self._last_display_midi  # freeze

        # Progress (locked only, and not while a click sound is playing).
        locked = self._locked_hole == hole
        suppressed = time.monotonic() < self._suppress_until
        if not (locked and self._in_range) or suppressed:
            self._hold_frames = 0
            self._hold_fraction = 0.0
            return

        target_frames = max(1, int(self._hold_seconds * FPS_EST))
        on_goal = abs((self._live_midi_f - goal) * 100.0) <= LOCK_CENTS
        self._hold_frames = min(target_frames, self._hold_frames + 1) if on_goal else 0
        self._hold_fraction = self._hold_frames / target_frames

        key = (hole, goal)
        self._best_hold[key] = max(self._best_hold.get(key, 0.0), self._hold_fraction)
        if self._hold_fraction >= 1.0 and key not in self._succeeded:
            self._succeeded.add(key)
            if not self._muted:
                synth.play_success()
                self._suppress_until = time.monotonic() + 0.6

    def _on_close(self):
        synth.stop()
        self._stop_stream()
        self.root.destroy()


def main():
    root = tk.Tk()
    PitchVizApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
