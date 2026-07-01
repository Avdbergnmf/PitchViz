"""Jam Helper tool: pick a backing-track key + scale, get notes to play.

Recommends the scale notes for the chosen key, highlights them on the shared
piano and harmonica views (including which ones need bends), tells you whether
the note you're currently playing fits, and suggests the harmonica position to
play in on your C harp.

Reuses the same widgets, colors, and hover/cross-highlight behavior as the Bend
Trainer; only the highlight logic differs.
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from ..core import music as M
from ..core.audio import NOTE_HOLD_FRAMES
from ..core.chords import Chord, ChordTracker, all_triads, chord_midis, dominant_chord
from ..core.harmonica import (
    HIGHEST_MIDI,
    LOWEST_MIDI,
    describe,
    hole_ladder,
    midi_name,
    note_locations,
)
from ..core.theme import (
    ACCENT, ACCENT_DIM, BG, BLOW_C, BLOW_HOVER, CHORD_MARK, DARK, DRAW_C, DRAW_HOVER,
    FONT, GOLD, GREEN, MUTED, PANEL, PANEL2, RED, ROOT_C, TEXT, lerp_color,
)
from ..widgets.harmonica import HarmonicaWidget
from ..widgets.piano import PianoWidget
from .base import ToolBase

DEFAULT_KEY = "G"
DEFAULT_SCALE = "Major"
DEFAULT_BEATS_PER_BAR = 4
DEFAULT_MIN_CHORD_BEATS = 4
DEFAULT_CHORD_PLAY_BEATS = 1
DEFAULT_COUNT_IN_BEATS = 0
EDIT_DOUBLE_MS = 400   # max gap between clicks to open the chord editor


@dataclass
class Seg:
    """One chord block in the progression, measured in beats."""
    start: int             # start beat (0-based)
    length: int            # length in beats
    chord: Chord | None    # None = unknown / gap


class JamHelperTool(ToolBase):
    title = "Jam Helper"

    def __init__(self, parent, engine, app):
        super().__init__(parent, engine, app)

        self._key = DEFAULT_KEY
        self._scale = DEFAULT_SCALE
        self._pcs: set[int] = set()
        self._root = 0

        self._chord_tracker = ChordTracker()

        # Progression recorder.
        self._bpm = 120
        self._bars = 4
        self._beats_per_bar = DEFAULT_BEATS_PER_BAR
        self._min_chord_beats = DEFAULT_MIN_CHORD_BEATS
        self._chord_play_beats = DEFAULT_CHORD_PLAY_BEATS
        self._count_in_beats = DEFAULT_COUNT_IN_BEATS
        self._recording = False
        self._rec_start = 0.0
        self._rec_frames: list[tuple[float, object]] = []   # (elapsed, chroma ndarray)
        self._rec_locked: list[Seg] = []                     # finalized slots during recording
        self._progression: list[Seg] = []
        self._last_snaps: list[str] = []
        self._tap_times: list[float] = []
        self._last_click_seg: int | None = None
        self._last_click_time = 0

        # Chord selection + progression playback.
        self._selected_chord: Chord | None = None
        self._selected_seg: int | None = None
        self._prog_playing = False
        self._counting_in = False
        self._count_in_start = 0.0
        self._count_in_total = 0
        self._count_in_clicked = -1
        self._prog_start = 0.0
        self._playback_beat = 0.0
        self._playback_seg_idx = -1
        self._play_chords_var = tk.BooleanVar(value=False)
        self._loop_var = tk.BooleanVar(value=False)
        self._suggest_win: tk.Toplevel | None = None
        self._chord_edit_win: tk.Toplevel | None = None
        self._edit_seg_idx: int | None = None
        self._edit_preview: list[Chord | None] = [None]
        self._edit_header: tk.Label | None = None
        self._edit_preview_lbl: tk.Label | None = None
        self._edit_body: tk.Frame | None = None
        self._edit_btn_refs: list[tuple[tk.Button, Chord]] = []

        # Live mic state.
        self._live_midi: int | None = None
        self._live_midi_f: float | None = None
        self._disp_midi_f: float | None = None   # lightly smoothed, for the bend needle
        self._silent_frames = NOTE_HOLD_FRAMES
        self._live_hole: int | None = None
        self._live_action: str | None = None

        # Hover.
        self._hover_note: int | None = None
        self._hover_hole: int | None = None
        self._hover_zone: str | None = None

        self._build_ui()
        self._recompute()

    # ----- UI -------------------------------------------------------------

    def _build_ui(self):
        f = self.frame

        controls = ttk.Frame(f)
        controls.pack(fill="x", padx=14, pady=(10, 2))
        ttk.Label(controls, text="Backing track key:").pack(side="left")
        self.key_var = tk.StringVar(value=self._key)
        self.key_combo = self._dark_menu(controls, self.key_var, M.KEY_NAMES, self._on_change)
        self.key_combo.pack(side="left", padx=(6, 16))
        ttk.Label(controls, text="Scale:").pack(side="left")
        self.scale_var = tk.StringVar(value=self._scale)
        self.scale_combo = self._dark_menu(controls, self.scale_var, M.SCALE_NAMES, self._on_change)
        self.scale_combo.pack(side="left", padx=(6, 0))
        self.chord_var = tk.StringVar(value="Backing chord: --")
        tk.Label(controls, textvariable=self.chord_var, bg=BG, fg=MUTED,
                 font=(FONT, 10, "bold")).pack(side="right")

        self.position_var = tk.StringVar()
        tk.Label(f, textvariable=self.position_var, bg=BG, fg=ROOT_C,
                 font=(FONT, 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(6, 0))
        self.recommend_var = tk.StringVar()
        tk.Label(f, textvariable=self.recommend_var, bg=BG, fg=TEXT,
                 font=(FONT, 12), anchor="w").pack(fill="x", padx=14)

        # --- Progression recorder ---
        rec = ttk.Frame(f)
        rec.pack(fill="x", padx=14, pady=(8, 0))
        self.rec_btn = tk.Button(rec, text="\u25cf Record", command=self._toggle_record,
                                 relief="flat", bg=PANEL2, fg=RED, activebackground=PANEL,
                                 font=(FONT, 10, "bold"), width=9)
        self.rec_btn.pack(side="left")
        self.play_btn = tk.Button(rec, text="\u25b6 Play", command=self._toggle_prog_play,
                                  relief="flat", bg=PANEL2, fg=GREEN, activebackground=PANEL,
                                  font=(FONT, 10, "bold"), width=8)
        self.play_btn.pack(side="left", padx=(6, 0))
        tk.Checkbutton(
            rec, text="Chord sounds", variable=self._play_chords_var,
            bg=BG, fg=TEXT, selectcolor=PANEL2, activebackground=BG,
            activeforeground=TEXT, font=(FONT, 9),
        ).pack(side="left", padx=(8, 0))
        tk.Checkbutton(
            rec, text="Loop", variable=self._loop_var,
            bg=BG, fg=TEXT, selectcolor=PANEL2, activebackground=BG,
            activeforeground=TEXT, font=(FONT, 9),
        ).pack(side="left", padx=(6, 0))
        ttk.Label(rec, text="Chord beats").pack(side="left", padx=(10, 2))
        self.chord_beats_var = tk.StringVar(value=str(self._chord_play_beats))
        chord_beats_spin = ttk.Spinbox(
            rec, from_=1, to=16, width=3, textvariable=self.chord_beats_var,
            command=self._on_chord_beats_change,
        )
        chord_beats_spin.pack(side="left")
        chord_beats_spin.bind("<Return>", self._on_chord_beats_change)
        chord_beats_spin.bind("<FocusOut>", self._on_chord_beats_change)
        ttk.Label(rec, text="Count-in").pack(side="left", padx=(10, 2))
        self.count_in_var = tk.StringVar(value=str(self._count_in_beats))
        count_in_spin = ttk.Spinbox(
            rec, from_=0, to=16, width=3, textvariable=self.count_in_var,
            command=self._on_count_in_change,
        )
        count_in_spin.pack(side="left")
        count_in_spin.bind("<Return>", self._on_count_in_change)
        count_in_spin.bind("<FocusOut>", self._on_count_in_change)
        self.suggest_btn = tk.Button(rec, text="Suggest scale", command=self._open_suggest,
                                     relief="flat", bg=PANEL2, fg=TEXT, activebackground=PANEL,
                                     font=(FONT, 9))
        self.suggest_btn.pack(side="left", padx=(6, 0))
        ttk.Label(rec, text="BPM").pack(side="left", padx=(12, 2))
        self.bpm_var = tk.StringVar(value=str(self._bpm))
        bpm_spin = ttk.Spinbox(rec, from_=40, to=240, width=5, textvariable=self.bpm_var,
                               command=self._on_grid_change)
        bpm_spin.pack(side="left")
        bpm_spin.bind("<Return>", self._on_grid_change)
        bpm_spin.bind("<FocusOut>", self._on_grid_change)
        tk.Button(rec, text="Tap", command=self._tap, relief="flat", bg=PANEL2, fg=TEXT,
                  activebackground=PANEL, width=4).pack(side="left", padx=(4, 0))
        ttk.Label(rec, text="Bars").pack(side="left", padx=(12, 2))
        self.bars_var = tk.StringVar(value=str(self._bars))
        bars_spin = ttk.Spinbox(rec, from_=1, to=32, width=4, textvariable=self.bars_var,
                                command=self._on_grid_change)
        bars_spin.pack(side="left")
        bars_spin.bind("<Return>", self._on_grid_change)
        bars_spin.bind("<FocusOut>", self._on_grid_change)

        rec2 = ttk.Frame(f)
        rec2.pack(fill="x", padx=14, pady=(4, 0))
        ttk.Label(rec2, text="Beats/bar").pack(side="left")
        self.beats_var = tk.StringVar(value=str(self._beats_per_bar))
        beats_spin = ttk.Spinbox(rec2, from_=2, to=12, width=4, textvariable=self.beats_var,
                                 command=self._on_grid_change)
        beats_spin.pack(side="left", padx=(4, 16))
        beats_spin.bind("<Return>", self._on_grid_change)
        beats_spin.bind("<FocusOut>", self._on_grid_change)
        ttk.Label(rec2, text="Min chord").pack(side="left")
        self.min_chord_var = tk.StringVar()
        self.min_chord_menu = self._dark_menu(
            rec2, self.min_chord_var, [], self._on_min_chord_change, width=10)
        self.min_chord_menu.pack(side="left", padx=(4, 0))
        self._refresh_min_chord_options()
        self.rec_status = tk.StringVar(value="set BPM + bars, then record one loop")
        tk.Label(rec2, textvariable=self.rec_status, bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(12, 0))

        self.timeline = tk.Canvas(f, height=66, bg=PANEL, highlightthickness=0, cursor="hand2")
        self.timeline.pack(fill="x", padx=14, pady=(4, 0))
        self.timeline.bind("<Configure>", lambda e: self._draw_timeline())
        self.timeline.bind("<Button-1>", self._on_timeline_click)

        readout = ttk.Frame(f)
        readout.pack(fill="x", padx=14, pady=(6, 0))
        self.note_var = tk.StringVar(value="--")
        self.status_var = tk.StringVar(value="play a note...")
        tk.Label(readout, textvariable=self.note_var, bg=BG, fg=TEXT,
                 font=(FONT, 30, "bold"), width=5, anchor="w").pack(side="left")
        self.status_lbl = tk.Label(readout, textvariable=self.status_var, bg=BG,
                                   fg=MUTED, font=(FONT, 13, "bold"), anchor="w")
        self.status_lbl.pack(side="left", padx=(14, 0), anchor="s", pady=(0, 8))

        self.harp = HarmonicaWidget(f, height=150, on_click=self._on_harp_click,
                                    on_hover=self._on_harp_hover, on_leave=self._on_harp_leave)
        self.harp.middle_renderer = self._draw_jam_middle
        self.harp.pack(fill="x", padx=14, pady=(8, 4))

        tk.Label(f, text="blue = blow   orange = draw   gold edge = scale root   "
                 "\u25c6 = chord root   \u25c7 = other chord tone   "
                 "click = play/select   double-click = edit chord",
                 bg=BG, fg="#777", font=(FONT, 8)).pack(anchor="w", padx=14)

        self.piano = PianoWidget(f, height=130, on_click=self._on_piano_click,
                                 on_hover=self._set_hover_note,
                                 on_leave=lambda: self._set_hover_note(None))
        self.piano.pack(fill="both", expand=True, padx=14, pady=(6, 8))

    def _dark_menu(self, parent, var: tk.StringVar, values: list[str], command, width: int = 0):
        """Dark-themed dropdown (readable on Windows vs default ttk Combobox)."""
        mb = tk.Menubutton(
            parent, textvariable=var, relief="flat", bg=PANEL2, fg=TEXT,
            activebackground=ACCENT_DIM, activeforeground=TEXT,
            highlightthickness=1, highlightbackground="#555", font=(FONT, 10),
            direction="below", padx=8, pady=2,
        )
        if width:
            mb.config(width=width)
        menu = tk.Menu(
            mb, tearoff=0, bg=PANEL2, fg=TEXT,
            activebackground=ACCENT_DIM, activeforeground=TEXT,
            borderwidth=0, font=(FONT, 10),
        )
        mb.config(menu=menu)
        mb._dark_menu = menu  # type: ignore[attr-defined]

        def pick(val: str):
            var.set(val)
            command()

        for val in values:
            menu.add_command(label=val, command=lambda v=val: pick(v))
        return mb

    def _set_dark_menu_values(self, mb: tk.Menubutton, var: tk.StringVar,
                              values: list[str], command):
        menu: tk.Menu = mb._dark_menu  # type: ignore[attr-defined]
        menu.delete(0, "end")

        def pick(val: str):
            var.set(val)
            command(val)

        for val in values:
            menu.add_command(label=val, command=lambda v=val: pick(v))

    def _min_chord_options(self) -> list[tuple[int, str]]:
        """Valid min-chord lengths: bar fractions and multi-bar slots."""
        bpb = self._beats_per_bar
        total = self._total_beats()
        seen: set[int] = set()
        opts: list[tuple[int, str]] = []

        def add(beats: int, label: str):
            if 1 <= beats <= total and beats not in seen:
                seen.add(beats)
                opts.append((beats, label))

        for div in range(1, bpb + 1):
            if bpb % div == 0:
                beats = bpb // div
                label = "1 bar" if div == 1 else f"1/{div} bar"
                add(beats, label)

        max_bars = total // bpb
        for n in range(2, max_bars + 1):
            add(n * bpb, f"{n} bars")

        return sorted(opts, key=lambda x: -x[0])

    def _refresh_min_chord_options(self):
        opts = self._min_chord_options()
        labels = [lab for _, lab in opts]
        valid = {b for b, _ in opts}
        if self._min_chord_beats not in valid:
            self._min_chord_beats = opts[0][0]
        label = next(lab for b, lab in opts if b == self._min_chord_beats)
        self.min_chord_var.set(label)
        self._set_dark_menu_values(
            self.min_chord_menu, self.min_chord_var, labels, self._on_min_chord_label)

    def _on_min_chord_label(self, label: str):
        for beats, lab in self._min_chord_options():
            if lab == label:
                self._min_chord_beats = beats
                break
        self._draw_timeline()

    def _on_min_chord_change(self):
        self._on_min_chord_label(self.min_chord_var.get())

    def _on_chord_beats_change(self, *_):
        try:
            self._chord_play_beats = max(1, min(16, int(float(self.chord_beats_var.get()))))
        except ValueError:
            pass
        self.chord_beats_var.set(str(self._chord_play_beats))

    def _on_count_in_change(self, *_):
        try:
            self._count_in_beats = max(0, min(16, int(float(self.count_in_var.get()))))
        except ValueError:
            pass
        self.count_in_var.set(str(self._count_in_beats))

    # ----- scale state ----------------------------------------------------

    def _on_change(self, _event=None):
        self._key = self.key_var.get()
        self._scale = self.scale_var.get()
        self._recompute()
        self._refresh_views()

    def _recompute(self):
        self._root = M.root_pc(self._key)
        self._pcs = M.scale_pitch_classes(self._root, self._scale)
        _, name, use = M.position_label(self._root)
        self.position_var.set(f"On your C harp: {name}  -  {use}")
        names = [M.NOTE_NAMES[(self._root + i) % 12] for i in M.SCALES[self._scale]]
        self.recommend_var.set(f"Play {self._key} {self._scale}:   " + "   ".join(names))

    def _in_scale(self, midi: int) -> bool:
        return (midi % 12) in self._pcs

    def _is_root(self, midi: int) -> bool:
        return (midi % 12) == self._root

    def _active_chord(self) -> Chord | None:
        return self._selected_chord

    def _chord_pcs(self) -> set[int]:
        ch = self._active_chord()
        return set(ch.notes) if ch else set()

    def _is_chord_tone(self, midi: int) -> bool:
        return (midi % 12) in self._chord_pcs()

    def _is_chord_root(self, midi: int) -> bool:
        ch = self._active_chord()
        return ch is not None and (midi % 12) == ch.root

    def _chord_marker_at(self, midi: int) -> tuple[str, int, str] | None:
        """Return (glyph, font size, color) for a chord tone, or None."""
        if not self._is_chord_tone(midi):
            return None
        if self._is_chord_root(midi):
            return ("\u25c6", 10, GOLD)
        return ("\u25c7", 8, CHORD_MARK)

    def _play_segment_chord(self, seg_idx: int | None = None):
        """Play the chord for a timeline slot for a beat-based duration (playback)."""
        if seg_idx is None:
            seg_idx = self._selected_seg
        if seg_idx is None or seg_idx < 0 or seg_idx >= len(self._progression):
            return
        seg = self._progression[seg_idx]
        if seg.chord is None:
            return
        beats = min(self._chord_play_beats, seg.length)
        dur = beats * self._beat_seconds()
        self.app.play_chord(chord_midis(seg.chord), duration=dur)

    def _select_chord(self, chord: Chord | None, seg_idx: int | None = None, play: bool = True):
        self._selected_chord = chord
        self._selected_seg = seg_idx
        if chord and play:
            self.app.play_chord(chord_midis(chord))
        self._refresh_views()
        self._draw_timeline()

    def _seg_at_beat(self, beat: float) -> tuple[int | None, Seg | None]:
        for i, seg in enumerate(self._progression):
            if seg.start <= beat < seg.start + seg.length:
                return i, seg
        return None, None

    def _chord_at_beat(self, beat: float) -> Chord | None:
        _, seg = self._seg_at_beat(beat)
        return seg.chord if seg else None

    def _jump_to_beat(self, beat: float):
        """Reposition the playback clock so the playhead sits on ``beat``."""
        self._prog_start = time.monotonic() - beat * self._beat_seconds()
        self._playback_beat = beat

    def _jump_to_segment(self, seg_idx: int, play_sound: bool | None = None):
        if seg_idx < 0 or seg_idx >= len(self._progression):
            return
        seg = self._progression[seg_idx]
        self._jump_to_beat(float(seg.start))
        self._playback_seg_idx = seg_idx
        do_play = self._play_chords_var.get() if play_sound is None else play_sound
        self._select_chord(seg.chord, seg_idx=seg_idx, play=False)
        if do_play and seg.chord is not None:
            self._play_segment_chord(seg_idx)

    # ----- recorder -------------------------------------------------------

    def _beat_seconds(self) -> float:
        return 60.0 / self._bpm

    def _total_beats(self) -> int:
        return self._bars * self._beats_per_bar

    def _segment_beats(self) -> int:
        return max(1, self._min_chord_beats)

    def _loop_seconds(self) -> float:
        return self._total_beats() * self._beat_seconds()

    def _on_grid_change(self, *_):
        try:
            self._bpm = max(40, min(240, int(float(self.bpm_var.get()))))
        except ValueError:
            pass
        try:
            self._bars = max(1, min(32, int(float(self.bars_var.get()))))
        except ValueError:
            pass
        try:
            self._beats_per_bar = max(2, min(12, int(float(self.beats_var.get()))))
        except ValueError:
            pass
        self._refresh_min_chord_options()
        self._draw_timeline()

    def _tap(self):
        now = time.monotonic()
        if self._tap_times and now - self._tap_times[-1] > 2.0:
            self._tap_times = []          # restart if you paused
        self._tap_times.append(now)
        self._tap_times = self._tap_times[-6:]
        if len(self._tap_times) >= 2:
            diffs = sorted(b - a for a, b in zip(self._tap_times, self._tap_times[1:]))
            median = diffs[len(diffs) // 2]
            if median > 0:
                self._bpm = max(40, min(240, round(60.0 / median)))
                self.bpm_var.set(str(self._bpm))
                self._draw_timeline()

    def _toggle_record(self):
        if self._recording:
            self._finish_record(manual=True)
        else:
            self._start_record()

    def _start_record(self):
        self._stop_playback()
        self._recording = True
        self._rec_start = time.monotonic()
        self._rec_frames = []
        self._rec_locked = []
        self._last_snaps = []
        self._progression = []
        self.rec_btn.config(text="\u25a0 Stop", fg=TEXT)
        self.rec_status.set(f"recording... play one {self._bars}-bar loop")
        self._draw_timeline()

    def _recording_seg_index(self, elapsed: float) -> int:
        beat_elapsed = elapsed / self._beat_seconds()
        return int(beat_elapsed // self._segment_beats())

    def _segment_chromas(self, seg_idx: int) -> list:
        beat = self._beat_seconds()
        seg_beats = self._segment_beats()
        start_b = seg_idx * seg_beats
        end_b = min(self._total_beats(), (seg_idx + 1) * seg_beats)
        t0, t1 = start_b * beat, end_b * beat
        return [ch for t, ch in self._rec_frames if t0 <= t < t1]

    def _lock_rec_segment(self, seg_idx: int):
        """Finalize one slot: dominant chord + scale snap while recording."""
        if seg_idx < len(self._rec_locked):
            return
        seg_beats = self._segment_beats()
        start_b = seg_idx * seg_beats
        end_b = min(self._total_beats(), (seg_idx + 1) * seg_beats)
        raw = dominant_chord(self._segment_chromas(seg_idx))
        ch, orig = M.snap_to_scale(raw, self._root, self._scale)
        if orig is not None and ch is not None:
            self._last_snaps.append(f"{orig.name}\u2192{ch.name}")
        self._rec_locked.append(Seg(start_b, end_b - start_b, ch))

    def _finalize_recording(self) -> list[Seg]:
        n_segs = max(1, (self._total_beats() + self._segment_beats() - 1) // self._segment_beats())
        for i in range(len(self._rec_locked), n_segs):
            self._lock_rec_segment(i)
        return list(self._rec_locked)

    def _finish_record(self, manual: bool = False):
        self._recording = False
        self.rec_btn.config(text="\u25cf Record", fg=RED)
        self._progression = self._finalize_recording()
        if manual:
            self.rec_status.set("stopped early")
        elif self._last_snaps:
            self.rec_status.set("done — snapped: " + ", ".join(self._last_snaps[:4]))
        else:
            self.rec_status.set("done — timeline full")
        self._draw_timeline()

    def _record_frame(self, state):
        elapsed = time.monotonic() - self._rec_start
        if state.level_fraction > 0.2:
            self._rec_frames.append((elapsed, state.chroma.copy()))
        seg_idx = self._recording_seg_index(elapsed)
        for i in range(len(self._rec_locked), seg_idx):
            self._lock_rec_segment(i)
        loop = self._loop_seconds()
        if elapsed >= loop:
            self._finish_record()
            return
        self._draw_timeline()

    # ----- progression playback + scale suggest ---------------------------

    def _stop_playback(self):
        self._prog_playing = False
        self._counting_in = False
        self._count_in_clicked = -1
        self.play_btn.config(text="\u25b6 Play", fg=GREEN)
        self._refresh_views()
        self._draw_timeline()

    def _start_count_in(self, beats: int):
        self._counting_in = True
        self._count_in_total = beats
        self._count_in_start = time.monotonic()
        self._count_in_clicked = -1
        self.play_btn.config(text="\u25a0 Stop", fg=TEXT)
        self.rec_status.set(f"count-in ({beats} beat{'s' if beats != 1 else ''})...")

    def _update_count_in(self):
        if not self._counting_in:
            return
        elapsed = time.monotonic() - self._count_in_start
        beat = elapsed / self._beat_seconds()
        idx = int(beat)
        while self._count_in_clicked < idx < self._count_in_total:
            self._count_in_clicked += 1
            accent = self._count_in_clicked % self._beats_per_bar == 0
            self.app.play_click(accent)
            left = self._count_in_total - self._count_in_clicked
            self.rec_status.set("go!" if left == 0 else f"count-in: {left}")
        if beat >= self._count_in_total:
            self._counting_in = False
            self._start_prog_playback()
        self._draw_timeline()

    def _start_prog_playback(self):
        self._prog_playing = True
        self._prog_start = time.monotonic()
        self._playback_beat = 0.0
        self._playback_seg_idx = -1
        self.play_btn.config(text="\u25a0 Stop", fg=TEXT)
        self.rec_status.set("playing progression...")
        self._sync_playback_selection(force=True)
        if self._play_chords_var.get():
            self._play_segment_chord(0)

    def _toggle_prog_play(self):
        if self._counting_in or self._prog_playing:
            self._stop_playback()
            return
        if not self._progression:
            self.rec_status.set("record a progression first")
            return
        self._on_count_in_change()
        if self._count_in_beats > 0:
            self._start_count_in(self._count_in_beats)
        else:
            self._start_prog_playback()

    def _sync_playback_selection(self, force: bool = False):
        """Keep timeline selection aligned with the segment under the playhead."""
        if not self._prog_playing or not self._progression:
            return
        seg_idx, seg = self._seg_at_beat(self._playback_beat)
        if seg_idx is None:
            return
        if force or seg_idx != self._playback_seg_idx:
            prev = self._playback_seg_idx
            self._playback_seg_idx = seg_idx
            self._select_chord(seg.chord, seg_idx=seg_idx, play=False)
            if (not force and prev >= 0 and self._play_chords_var.get()
                    and seg.chord is not None):
                self._play_segment_chord(seg_idx)

    def _update_playback(self):
        if not self._prog_playing:
            return
        elapsed = time.monotonic() - self._prog_start
        beat = elapsed / self._beat_seconds()
        total = self._total_beats()
        if beat >= total:
            if self._loop_var.get():
                self._jump_to_beat(0.0)
                self._playback_seg_idx = -1
                self._sync_playback_selection(force=True)
                if self._play_chords_var.get():
                    self._play_segment_chord(0)
            else:
                self._prog_playing = False
                self.play_btn.config(text="\u25b6 Play", fg=GREEN)
                self.rec_status.set("playback finished")
                self._playback_beat = 0.0
                self._playback_seg_idx = -1
        else:
            self._playback_beat = beat
            self._sync_playback_selection()
        self._draw_timeline()
        self._sync_harp()
        self._sync_piano()

    def _apply_seg_chord(self, seg_idx: int, chord: Chord):
        ch, orig = M.snap_to_scale(chord, self._root, self._scale)
        if orig is not None and ch is not None:
            self.rec_status.set(f"snapped {orig.name} \u2192 {ch.name} ({self._key} {self._scale})")
        old = self._progression[seg_idx]
        self._progression[seg_idx] = Seg(old.start, old.length, ch)
        self._select_chord(ch, seg_idx=seg_idx, play=False)
        if orig is None:
            slot = seg_idx + 1
            self.rec_status.set(f"slot {slot}: {ch.name}")

    def _chord_editor_open(self) -> bool:
        return (self._chord_edit_win is not None
                and tk.Toplevel.winfo_exists(self._chord_edit_win))

    def _open_chord_editor(self, seg_idx: int):
        if seg_idx < 0 or seg_idx >= len(self._progression):
            return
        self._edit_seg_idx = seg_idx
        if self._chord_editor_open():
            self._refresh_chord_editor()
            return
        self._build_chord_editor_shell()
        self._refresh_chord_editor()

    def _build_chord_editor_shell(self):
        win = tk.Toplevel(self.app.root)
        win.title("Change chord")
        win.configure(bg=BG)
        win.geometry("420x480")
        win.transient(self.app.root)
        self._chord_edit_win = win

        self._edit_header = tk.Label(
            win, text="", bg=BG, fg=TEXT, font=(FONT, 10, "bold"))
        self._edit_header.pack(anchor="w", padx=14, pady=(12, 4))
        self._edit_preview_lbl = tk.Label(win, text="", bg=BG, fg=MUTED, font=(FONT, 10))
        self._edit_preview_lbl.pack(anchor="w", padx=14, pady=(0, 10))

        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True, padx=14)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._edit_body = tk.Frame(canvas, bg=BG)
        self._edit_body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._edit_body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=14, pady=12)

        def apply():
            if self._edit_seg_idx is not None and self._edit_preview[0] is not None:
                self._apply_seg_chord(self._edit_seg_idx, self._edit_preview[0])
                self._refresh_chord_editor()

        def close():
            self._close_chord_editor()

        tk.Button(foot, text="Apply", command=apply, relief="flat", bg=GREEN, fg=BG,
                  font=(FONT, 10, "bold"), width=10).pack(side="right")
        tk.Button(foot, text="Close", command=close, relief="flat", bg=PANEL2, fg=TEXT,
                  width=10).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", close)

    def _close_chord_editor(self):
        if self._chord_edit_win and tk.Toplevel.winfo_exists(self._chord_edit_win):
            self._chord_edit_win.destroy()
        self._chord_edit_win = None
        self._edit_seg_idx = None

    def _refresh_chord_editor(self):
        if not self._chord_editor_open() or self._edit_seg_idx is None:
            return
        if self._edit_body is None or self._edit_header is None or self._edit_preview_lbl is None:
            return

        seg_idx = self._edit_seg_idx
        seg = self._progression[seg_idx]
        current = seg.chord
        self._edit_preview[0] = current
        slot = seg_idx + 1
        bar_num = seg.start // self._beats_per_bar + 1

        self._edit_header.config(
            text=f"Slot {slot} (bar {bar_num})  —  click a chord to preview, then Apply")
        self._select_chord(current, seg_idx=seg_idx, play=False)

        for w in self._edit_body.winfo_children():
            w.destroy()
        self._edit_btn_refs.clear()

        def refresh_preview():
            p = self._edit_preview[0]
            pname = p.name if p else "(none)"
            cname = current.name if current else "(none)"
            self._edit_preview_lbl.config(text=f"Current: {cname}    Preview: {pname}")
            for btn, ch in self._edit_btn_refs:
                sel = p is not None and ch == p
                btn.config(bg=GOLD if sel else PANEL2, fg=BG if sel else TEXT)

        def pick(ch: Chord):
            self._edit_preview[0] = ch
            self.app.play_chord(chord_midis(ch))
            refresh_preview()

        def add_section(title: str):
            tk.Label(self._edit_body, text=title, bg=BG, fg=ROOT_C,
                     font=(FONT, 9, "bold")).pack(anchor="w", pady=(10, 4))

        def add_chord_row(options: list[tuple[Chord, str]], cols: int = 4):
            row = tk.Frame(self._edit_body, bg=BG)
            row.pack(fill="x", pady=(0, 4))
            for i, (ch, lab) in enumerate(options):
                if i and i % cols == 0:
                    row = tk.Frame(self._edit_body, bg=BG)
                    row.pack(fill="x", pady=(0, 4))
                text = f"{lab}\n{ch.name}" if lab != ch.name else ch.name
                btn = tk.Button(row, text=text, width=7, relief="flat", bg=PANEL2, fg=TEXT,
                                activebackground=PANEL, font=(FONT, 9),
                                command=lambda c=ch: pick(c))
                btn.pack(side="left", padx=2, pady=2)
                self._edit_btn_refs.append((btn, ch))

        nearby = M.nearby_diatonic(current, self._root, self._scale)
        if nearby:
            add_section(f"In {self._key} {self._scale} — nearby")
            add_chord_row(nearby, cols=3)

        diatonic = M.diatonic_chord_options(self._root, self._scale)
        add_section(f"All diatonic ({self._key} {self._scale})")
        add_chord_row(diatonic, cols=4)

        add_section("All triads")
        add_chord_row([(ch, ch.name) for ch in all_triads()], cols=6)

        refresh_preview()

    def _open_suggest(self):
        chords = [s.chord for s in self._progression if s.chord]
        if not chords:
            self.rec_status.set("record a progression first")
            return
        options = M.suggest_scales(chords)
        if not options:
            self.rec_status.set("could not suggest scales")
            return
        if self._suggest_win and tk.Toplevel.winfo_exists(self._suggest_win):
            self._suggest_win.destroy()
        win = tk.Toplevel(self.app.root)
        win.title("Suggested scales")
        win.configure(bg=BG)
        win.geometry("340x280")
        win.transient(self.app.root)
        self._suggest_win = win
        tk.Label(win, text="Best key + scale matches for your recording:",
                 bg=BG, fg=TEXT, font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 8))
        for key, scale, score in options[:6]:
            row = tk.Frame(win, bg=BG)
            row.pack(fill="x", padx=14, pady=3)
            label = f"{key} {scale}  ({score:.0%} match)"
            tk.Label(row, text=label, bg=BG, fg=TEXT, font=(FONT, 10)).pack(side="left")
            tk.Button(row, text="Use", relief="flat", bg=PANEL2, fg=GREEN,
                      command=lambda k=key, s=scale: self._apply_scale(k, s, win)).pack(side="right")

    def _apply_scale(self, key: str, scale: str, win: tk.Toplevel):
        self._key, self._scale = key, scale
        self.key_var.set(key)
        self.scale_var.set(scale)
        self._recompute()
        self._refresh_views()
        win.destroy()
        self.rec_status.set(f"using {key} {scale}")

    # ----- drawing: timeline ---------------------------------------------

    def _draw_timeline(self):
        c = self.timeline
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.create_rectangle(0, 0, w, h, fill=PANEL, outline="")
        total = self._total_beats()
        if total <= 0:
            return
        beat_w = w / total

        # Beat / bar grid.
        bpb = self._beats_per_bar
        for b in range(total + 1):
            x = b * beat_w
            is_bar = b % bpb == 0
            c.create_line(x, 0, x, h, fill="#3a3f47" if not is_bar else "#555",
                          width=2 if is_bar else 1)
            if is_bar and b < total:
                c.create_text(x + 3, 8, anchor="w", text=str(b // bpb + 1),
                              fill="#666", font=(FONT, 7))

        # Min-chord segment dividers (lighter).
        seg = self._segment_beats()
        if seg > bpb:
            for b in range(seg, total, seg):
                x = b * beat_w
                c.create_line(x, 14, x, h, fill="#4a5060", width=1, dash=(3, 3))

        if self._recording:
            self._draw_live_blocks(c, w, h, beat_w)
        elif self._progression:
            for i, seg in enumerate(self._progression):
                x0, x1 = seg.start * beat_w, (seg.start + seg.length) * beat_w
                selected = (i == self._selected_seg)
                self._draw_block(c, x0, x1, h, seg.chord, selected=selected)
            if self._prog_playing:
                ph = self._playback_beat / total * w
                c.create_line(ph, 0, ph, h, fill=GOLD, width=2)
            elif self._counting_in:
                c.create_line(0, 0, 0, h, fill=GOLD, width=3)
                elapsed = time.monotonic() - self._count_in_start
                left = max(0, self._count_in_total - int(elapsed / self._beat_seconds()))
                c.create_text(w / 2, h / 2, text="GO!" if left == 0 else str(left),
                              fill=GOLD, font=(FONT, 22, "bold"))
        else:
            c.create_text(w / 2, h / 2,
                          text="Set BPM + bars, then Record one loop of the backing track",
                          fill=MUTED, font=(FONT, 10))

    def _draw_live_blocks(self, c, w, h, beat_w):
        """During recording: one block per min-chord segment (progressive chroma fit)."""
        loop = self._loop_seconds()
        beat = self._beat_seconds()
        total = self._total_beats()
        seg_beats = self._segment_beats()
        n_segs = max(1, (total + seg_beats - 1) // seg_beats)
        elapsed = self._rec_frames[-1][0] if self._rec_frames else 0.0

        for i in range(n_segs):
            start_b = i * seg_beats
            end_b = min(total, (i + 1) * seg_beats)
            t0, t1 = start_b * beat, end_b * beat
            if i < len(self._rec_locked):
                chord = self._rec_locked[i].chord
            else:
                chromas = self._segment_chromas(i)
                chord = dominant_chord(chromas) if chromas else None
            x0 = start_b / total * w
            x1 = end_b / total * w
            # Partial fill for the segment currently being recorded.
            if elapsed < t1:
                frac = max(0.0, min(1.0, (elapsed - t0) / (t1 - t0))) if t1 > t0 else 0
                x1 = x0 + (x1 - x0) * frac
            self._draw_block(c, x0, x1, h, chord)

        ph = min(1.0, elapsed / loop) * w
        c.create_line(ph, 0, ph, h, fill="#fff", width=2)

    def _draw_block(self, c, x0, x1, h, chord, selected: bool = False):
        if x1 - x0 < 1:
            return
        fill = ACCENT if selected else (ACCENT_DIM if chord is not None else "#26292f")
        outline = GOLD if selected else ACCENT
        c.create_rectangle(x0 + 1, 16, x1 - 1, h - 4, fill=fill, outline=outline, width=2 if selected else 1)
        if chord is not None and x1 - x0 > 22:
            c.create_text((x0 + x1) / 2, (16 + h - 4) / 2, text=chord.name,
                          fill="#fff", font=(FONT, 11, "bold"))

    def _on_timeline_click(self, event):
        if self._recording or not self._progression:
            return
        w = self.timeline.winfo_width()
        total = self._total_beats()
        if w <= 1 or total <= 0:
            return
        beat = event.x / w * total
        for i, seg in enumerate(self._progression):
            if seg.start <= beat < seg.start + seg.length:
                now = event.time
                is_double = (
                    i == self._last_click_seg
                    and self._last_click_time
                    and 0 < now - self._last_click_time <= EDIT_DOUBLE_MS
                )
                self._last_click_seg = i
                self._last_click_time = now

                if is_double:
                    self._open_chord_editor(i)
                    return

                if self._prog_playing:
                    self._jump_to_segment(i)
                    if self._chord_editor_open():
                        self._edit_seg_idx = i
                        self._refresh_chord_editor()
                    return
                if self._chord_editor_open():
                    self._edit_seg_idx = i
                    self._refresh_chord_editor()
                    if seg.chord:
                        self.app.play_chord(chord_midis(seg.chord))
                    return
                if seg.chord:
                    self._select_chord(seg.chord, seg_idx=i, play=True)
                else:
                    self._selected_seg = i
                    self._selected_chord = None
                    self._refresh_views()
                    self._draw_timeline()
                return

    # ----- lifecycle ------------------------------------------------------

    def on_show(self):
        self._refresh_views()
        self._draw_timeline()

    def on_hide(self):
        if self._counting_in or self._prog_playing:
            self._stop_playback()

    def on_audio(self, state):
        self._silent_frames = state.silent_frames
        self._live_midi = state.midi
        self._live_midi_f = state.midi_f
        self._live_hole = state.hole
        self._live_action = state.action

        if state.has_pitch and state.midi_f is not None:
            if self._disp_midi_f is None:
                self._disp_midi_f = state.midi_f
            else:
                self._disp_midi_f += 0.5 * (state.midi_f - self._disp_midi_f)
        elif state.silent_frames > NOTE_HOLD_FRAMES:
            self._disp_midi_f = None

        # Live backing-chord estimate (works on the polyphonic room sound).
        if state.level_fraction > 0.25:
            ch = self._chord_tracker.update(state.chroma)
            self.chord_var.set(
                f"Backing chord: {ch.name}  ({self._chord_tracker.score:.0%})"
                if ch else "Backing chord: ...")
        else:
            self.chord_var.set("Backing chord: --")

        if self._recording:
            self._record_frame(state)

        if self._counting_in:
            self._update_count_in()
        else:
            self._update_playback()

        if state.has_pitch:
            self.note_var.set(state.note_name)
            if self._in_scale(state.midi):
                deg = M.degree_of(state.midi, self._root, self._scale)
                role = "root" if self._is_root(state.midi) else f"degree {deg + 1}"
                self.status_var.set(f"in {self._key} {self._scale}  ({role})  -  {describe(state.midi)}")
                self.status_lbl.config(fg=GREEN)
            else:
                self.status_var.set(f"not in {self._key} {self._scale}")
                self.status_lbl.config(fg=RED)
        elif state.silent_frames > NOTE_HOLD_FRAMES:
            self.note_var.set("--")
            self.status_var.set("play a note...")
            self.status_lbl.config(fg=MUTED)

        self._refresh_views()

    def _refresh_views(self):
        self._sync_harp()
        self._sync_piano()

    # ----- click + hover --------------------------------------------------

    def _on_harp_click(self, hole, zone, y=None):
        lad = hole_ladder(hole)
        if zone == "blow":
            self.app.play(lad.blow)
        elif zone == "draw":
            self.app.play(lad.draw)
        elif zone == "mid" and y is not None:
            bend = self._bend_note_at(hole, y)
            if bend is not None:
                # Hand off to the Bend Trainer, locked on this bend.
                self.app.practice_bend(hole, bend)

    def _on_piano_click(self, midi):
        self.app.play(midi)

    # ----- middle bend-bar geometry (shared by render + hit-test) ---------

    def _mid_bar_bounds(self, h: float) -> tuple[float, float]:
        """The (top, bottom) y of the bend ladder inside a hole's middle box."""
        top_b, bot_b = h * 0.26, h * 0.74
        by0, by1 = top_b + 3, bot_b - 3
        return by0 + 6, by1 - 6

    def _bend_note_at(self, hole, y) -> int | None:
        lad = hole_ladder(hole)
        if not lad.has_bends:
            return None
        ya, yb = self._mid_bar_bounds(self.harp.winfo_height())
        if yb == ya:
            return None
        frac = (y - ya) / (yb - ya)
        approx = lad.blow + frac * (lad.draw - lad.blow)
        nearest = min(lad.bend_notes, key=lambda b: abs(b - approx))
        return nearest if abs(nearest - approx) <= 0.6 else None

    def _note_color(self, midi: int, dim: bool = False) -> str:
        """Blow/draw for naturals; bend notes blend draw -> blow along the hole."""
        locs = note_locations(midi)
        if not locs:
            return BLOW_C
        loc = locs[0]
        if loc.bend_steps == 0:
            col = BLOW_C if "blow" in loc.action else DRAW_C
        else:
            lad = hole_ladder(loc.hole)
            span = lad.hi - lad.lo
            # Draw (high reed) -> blow (low reed) as you bend down.
            t = (lad.hi - midi) / span if span else 0.0
            col = lerp_color(DRAW_C, BLOW_C, t)
        if dim:
            col = lerp_color(col, DARK, 0.45)
        return col

    def _hover_color(self, midi: int) -> str:
        locs = note_locations(midi)
        if locs and locs[0].bend_steps == 0:
            return BLOW_HOVER if "blow" in locs[0].action else DRAW_HOVER
        return lerp_color(DRAW_HOVER, BLOW_HOVER, 0.5)

    def _set_hover_note(self, note):
        if note != self._hover_note:
            self._hover_note = note
            self._refresh_views()

    def _on_harp_hover(self, hole, zone):
        lad = hole_ladder(hole)
        note = lad.blow if zone == "blow" else lad.draw if zone == "draw" else None
        if (hole, zone) != (self._hover_hole, self._hover_zone) or note != self._hover_note:
            self._hover_hole, self._hover_zone, self._hover_note = hole, zone, note
            self._refresh_views()

    def _on_harp_leave(self):
        self._hover_hole = self._hover_zone = self._hover_note = None
        self._refresh_views()

    # ----- drawing: harmonica --------------------------------------------

    def _sync_harp(self):
        hw = self.harp
        hw.blow_fill.clear(); hw.draw_fill.clear()
        hw.blow_label_color.clear(); hw.draw_label_color.clear()
        hw.outline.clear()
        hw.blow_markers.clear(); hw.draw_markers.clear()
        silent = self._silent_frames > 0
        chord_pcs = self._chord_pcs()
        has_chord = bool(chord_pcs)

        for hole in range(1, 11):
            lad = hole_ladder(hole)
            for note, fill_map, label_map, zone, marker_map in (
                (lad.blow, hw.blow_fill, hw.blow_label_color, "blow", hw.blow_markers),
                (lad.draw, hw.draw_fill, hw.draw_label_color, "draw", hw.draw_markers),
            ):
                pc = note % 12
                if self._in_scale(note):
                    dim = has_chord and pc not in chord_pcs
                    fill_map[hole] = self._note_color(note, dim=dim)
                    label_map[hole] = "#fff"
                    if self._is_root(note):
                        hw.outline[(hole, zone)] = GOLD
                    mk = self._chord_marker_at(note)
                    if mk is not None:
                        marker_map[hole] = "root" if self._is_chord_root(note) else "chord"
                elif self._hover_note == note or (self._hover_hole == hole and self._hover_zone == zone):
                    fill_map[hole] = self._hover_color(note)
                    label_map[hole] = "#fff"

        if not silent and self._live_midi is not None:
            col = GREEN if self._in_scale(self._live_midi) else RED
            for loc in note_locations(self._live_midi):
                if loc.bend_steps == 0:
                    hw.outline[(loc.hole, loc.action)] = col
        hw.redraw()

    def _draw_jam_middle(self, c, hole, x0, x1, top_b, bot_b):
        lad = hole_ladder(hole)
        bx0, bx1 = x0 + 5, x1 - 5
        by0, by1 = top_b + 3, bot_b - 3
        c.create_rectangle(bx0, by0, bx1, by1, outline="#3a3f47", width=1, fill=DARK)
        c.create_text(bx0 + (bx1 - bx0) * 0.24, (by0 + by1) / 2, text=str(hole),
                      fill="#dfe6ef", font=(FONT, 12, "bold"))
        if not lad.has_bends:
            return

        bar_x = bx0 + (bx1 - bx0) * 0.62
        ya, yb = by0 + 6, by1 - 6

        def y_of(midi):
            return ya + (midi - lad.blow) / (lad.draw - lad.blow) * (yb - ya)

        c.create_line(bar_x, ya, bar_x, yb, fill="#555", width=2)
        for midi in lad.bend_notes:
            y = y_of(midi)
            if self._in_scale(midi):
                col = self._note_color(midi, dim=bool(self._chord_pcs()))
                ww = 7
            else:
                col, ww = "#555", 4
            c.create_line(bar_x - ww, y, bar_x + ww, y, fill=col, width=2)
            mk = self._chord_marker_at(midi)
            if mk:
                glyph, size, mcol = mk
                c.create_text(bar_x + 12, y, text=glyph, fill=mcol,
                              font=(FONT, size, "bold"))
            elif self._is_root(midi):
                c.create_line(bar_x - 8, y, bar_x + 8, y, fill=GOLD, width=2)

        # Continuous live pitch needle: shows when you're *between* notes.
        if (self._live_hole == hole and self._silent_frames == 0
                and self._disp_midi_f is not None
                and lad.lo - 0.7 <= self._disp_midi_f <= lad.hi + 0.7):
            ym = y_of(max(lad.lo, min(lad.hi, self._disp_midi_f)))
            c.create_line(bar_x - 9, ym, bar_x + 9, ym, fill="#fff", width=1)
            c.create_polygon(bar_x + 9, ym, bar_x + 16, ym - 4, bar_x + 16, ym + 4,
                             fill="#fff", outline="")

    # ----- drawing: piano -------------------------------------------------

    def _sync_piano(self):
        pw = self.piano
        pw.fills.clear(); pw.outlines.clear()
        pw.markers.clear(); pw.marker_colors.clear()
        chord_pcs = self._chord_pcs()
        has_chord = bool(chord_pcs)

        for midi in range(LOWEST_MIDI, HIGHEST_MIDI + 1):
            pc = midi % 12
            if self._in_scale(midi):
                pw.fills[midi] = self._note_color(midi, dim=has_chord and pc not in chord_pcs)
                if self._is_root(midi):
                    pw.outlines[midi] = GOLD
                elif pc in chord_pcs:
                    pw.markers[midi] = "\u25c7"
                    pw.marker_colors[midi] = CHORD_MARK
                    pw.outlines[midi] = GOLD
        if self._silent_frames == 0 and self._live_midi is not None:
            pw.outlines[self._live_midi] = (
                GREEN if self._in_scale(self._live_midi) else RED)
        pw.hover_note = self._hover_note
        pw.redraw()
