"""Bend Trainer tool: practice hitting and holding harmonica bends.

Ported onto the shared piano/harmonica widgets. The bend practice panel and the
per-hole lock widget (drawn into the harmonica's middle zone) are specific to
this tool. Pitch indication works whether or not a hole is locked; progress
(hold / best-hold / success) is recorded only while a hole is locked.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..core.audio import FPS_EST, NOTE_HOLD_FRAMES
from ..core.harmonica import (
    HARMONICA_C,
    describe,
    hole_ladder,
    midi_name,
    note_locations,
)
from ..core.theme import (
    BG, BLOW_C, DARK, DETECT_OUTLINE, DIM, DRAW_C,
    FONT, GOLD, GREEN, HOVER_GLOW, LOCK_CENTS, MUTED, NEAR_CENTS, PANEL,
    PANEL2, PEAK_C, SUCCESS_C, TEXT, accuracy_color, draw_hover_glow, draw_line_glow,
)
from ..widgets.harmonica import HarmonicaWidget
from ..widgets.piano import PianoWidget
from .base import ToolBase

RANGE_MARGIN = 0.6
DEFAULT_INERTIA = 0.85
DEFAULT_MARKER_SMOOTHING = 0.3
DEFAULT_HOLD_SECONDS = 3.0


class BendTrainerTool(ToolBase):
    title = "Bend Trainer"

    def __init__(self, parent, engine, app):
        super().__init__(parent, engine, app)

        # Live mic state (mirrored from AudioState each frame).
        self._current_midi: int | None = None
        self._live_midi_f: float | None = None
        self._silent_frames = NOTE_HOLD_FRAMES
        self._live_hole: int | None = None
        self._live_action: str | None = None

        # Selection (playback highlight) + locked practice hole.
        self._selection: tuple | None = None
        self._locked_hole: int | None = None

        # Per-hole persistent progress.
        self._goal_by_hole: dict[int, int] = {}
        self._best_hold: dict[tuple[int, int], float] = {}
        self._succeeded: set[tuple[int, int]] = set()

        # Active-hole transient (pitch indication).
        self._smooth_hole: int | None = None
        self._display_midi: float | None = None
        self._last_display_midi: float | None = None
        self._in_range = False
        self._peak_progress = 0.0
        self._hold_frames = 0
        self._hold_fraction = 0.0

        # Hover.
        self._hover_hole: int | None = None
        self._hover_zone: str | None = None
        self._hover_note: int | None = None

        # Settings.
        self._inertia = DEFAULT_INERTIA
        self._marker_smoothing = DEFAULT_MARKER_SMOOTHING
        self._hold_seconds = DEFAULT_HOLD_SECONDS
        self._settings_win: tk.Toplevel | None = None

        self._bend_geom: dict | None = None

        self._build_ui()

    # ----- UI construction ------------------------------------------------

    def _build_ui(self):
        f = self.frame

        readout = ttk.Frame(f)
        readout.pack(fill="x", padx=14, pady=(10, 0))
        self.note_var = tk.StringVar(value="--")
        self.detail_var = tk.StringVar(value="")
        self.hole_var = tk.StringVar(value="play or click a note...")
        tk.Label(readout, textvariable=self.note_var, bg=BG, fg=TEXT,
                 font=(FONT, 34, "bold"), width=5, anchor="w").pack(side="left")
        mid = ttk.Frame(readout)
        mid.pack(side="left", padx=(16, 0), anchor="s", pady=(0, 10))
        tk.Label(mid, textvariable=self.detail_var, bg=BG, fg=MUTED,
                 font=(FONT, 12)).pack(anchor="w")
        tk.Label(mid, textvariable=self.hole_var, bg=BG, fg=GREEN,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Button(readout, text="\u2699", command=self._open_settings, width=3,
                  relief="flat", bg=PANEL, fg=TEXT, activebackground=PANEL2).pack(side="right")

        self.harp = HarmonicaWidget(f, height=140, on_click=self._on_harp_click,
                                    on_hover=self._on_harp_hover, on_leave=self._on_harp_leave)
        self.harp.middle_renderer = self._draw_lock_widget
        self.harp.pack(fill="x", padx=14, pady=(8, 8))

        tk.Label(f, text="Bend practice", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=14)
        self.bend_canvas = tk.Canvas(f, height=220, bg=PANEL, highlightthickness=0)
        self.bend_canvas.pack(fill="x", padx=14, pady=(2, 8))
        self.bend_canvas.bind("<Configure>", lambda e: self._draw_bend())
        self.bend_canvas.bind("<Button-1>", self._on_bend_click)
        self.bend_canvas.bind("<Motion>", self._on_bend_motion)
        self.bend_canvas.bind("<Leave>", self._on_bend_leave)

        self.piano = PianoWidget(f, height=120, on_click=self._on_piano_click,
                                 on_hover=self._set_hover_note, on_leave=lambda: self._set_hover_note(None))
        self.piano.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    # ----- lifecycle ------------------------------------------------------

    def on_show(self):
        self._refresh_views()

    def on_audio(self, state):
        self._silent_frames = state.silent_frames
        self._current_midi = state.midi
        self._live_midi_f = state.midi_f
        self._live_hole = state.hole
        self._live_action = state.action

        if state.has_pitch:
            sign = "+" if state.cents >= 0 else ""
            self.note_var.set(state.note_name)
            self.detail_var.set(f"{state.freq:.1f} Hz   {sign}{state.cents:.0f} cents")
            self.hole_var.set(describe(state.midi))
        elif state.silent_frames > NOTE_HOLD_FRAMES and self._selection is None:
            self.note_var.set("--")
            self.detail_var.set("")
            self.hole_var.set("play or click a note...")

        self._update_practice()
        self._refresh_views()

    def _refresh_views(self):
        self._sync_harp()
        self._sync_piano()
        self._draw_bend()

    # ----- metrics --------------------------------------------------------

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

    # ----- selection + playback ------------------------------------------

    def _select_play(self, hole, action, steps, midi):
        self._selection = (hole, action, steps, midi)
        self.app.play(midi)

    def _toggle_lock(self, hole):
        lad = hole_ladder(hole)
        if not lad.has_bends:
            return
        if self._locked_hole == hole:
            self._locked_hole = None
            return
        self._locked_hole = hole
        self._selection = (hole, lad.natural_action, 0, lad.natural)
        self.app.play(self._goal_of(hole))

    def _reset_hole(self, hole):
        goal = self._goal_of(hole)
        self._best_hold.pop((hole, goal), None)
        self._succeeded.discard((hole, goal))

    def _reset_all(self):
        self._best_hold.clear()
        self._succeeded.clear()
        self._peak_progress = 0.0

    # ----- click handlers -------------------------------------------------

    def _on_harp_click(self, hole, zone, y=None):
        lad = hole_ladder(hole)
        if zone == "blow":
            self._select_play(hole, "blow", 0, lad.blow)
        elif zone == "draw":
            self._select_play(hole, "draw", 0, lad.draw)
        else:
            self._toggle_lock(hole)
        self._refresh_views()

    def practice(self, hole, goal):
        """Lock a hole and set a goal from another tool (e.g. the Jam Helper)."""
        lad = hole_ladder(hole)
        if not lad.has_bends:
            return
        self._goal_by_hole[hole] = goal
        self._locked_hole = hole
        self._selection = (hole, lad.natural_action, 0, lad.natural)
        self.app.play(goal)
        self._refresh_views()

    def _on_piano_click(self, midi):
        locs = note_locations(midi)
        if locs:
            loc = locs[0]
            action = "blow" if "blow" in loc.action else "draw"
            if loc.bend_steps > 0:
                self._goal_by_hole[loc.hole] = midi
            self._select_play(loc.hole, action, loc.bend_steps, midi)
        else:
            self._select_play(0, "none", 0, midi)
        self._refresh_views()

    def _on_bend_click(self, event):
        geom = self._bend_geom
        if not geom:
            return
        rb = geom.get("reset_rect")
        if rb and rb[0] <= event.x <= rb[2] and rb[1] <= event.y <= rb[3]:
            self._reset_hole(geom["hole"])
            self._refresh_views()
            return
        lo, hi = geom["lo"], geom["hi"]
        x_left, x_right = geom["x_left"], geom["x_right"]
        if x_right <= x_left:
            return
        midi = max(lo, min(hi, round(lo + (event.x - x_left) / (x_right - x_left) * (hi - lo))))
        hole = geom["hole"]
        if midi in hole_ladder(hole).bend_notes:
            self._goal_by_hole[hole] = midi
        self.app.play(midi)
        self._refresh_views()

    # ----- hover handlers -------------------------------------------------

    def _set_hover_note(self, note):
        if note != self._hover_note:
            self._hover_note = note
            self._refresh_views()

    def _on_harp_hover(self, hole, zone, y=None):
        lad = hole_ladder(hole)
        note = (
            lad.blow if zone == "blow"
            else lad.draw if zone == "draw"
            else self._bend_note_at_harp(hole, y) if y is not None
            else None
        )
        if (hole, zone) != (self._hover_hole, self._hover_zone) or note != self._hover_note:
            self._hover_hole, self._hover_zone, self._hover_note = hole, zone, note
            self._refresh_views()

    def _on_harp_leave(self):
        self._hover_hole = self._hover_zone = self._hover_note = None
        self._refresh_views()

    def _on_bend_motion(self, event):
        geom = self._bend_geom
        if not geom:
            return
        lo, hi = geom["lo"], geom["hi"]
        x_left, x_right = geom["x_left"], geom["x_right"]
        if x_right <= x_left:
            return
        midi = round(lo + (event.x - x_left) / (x_right - x_left) * (hi - lo))
        self._set_hover_note(midi if lo <= midi <= hi else None)

    def _on_bend_leave(self, _e):
        self._set_hover_note(None)

    def _bend_note_at_harp(self, hole, y) -> int | None:
        lad = hole_ladder(hole)
        if y is None or not lad.has_bends:
            return None
        h = self.harp.winfo_height()
        top_b, bot_b = h * 0.26, h * 0.74
        ya, yb = top_b + 9, bot_b - 9
        if yb <= ya:
            return None
        approx = lad.blow + (y - ya) / (yb - ya) * (lad.draw - lad.blow)
        nearest = min(lad.bend_notes, key=lambda b: abs(b - approx))
        return nearest if abs(nearest - approx) <= 0.6 else None

    # ----- drawing: harmonica (config + lock widget) ----------------------

    def _sync_harp(self):
        hw = self.harp
        hw.blow_fill.clear(); hw.draw_fill.clear()
        hw.blow_label_color.clear(); hw.draw_label_color.clear()
        hw.outline.clear()
        hw.spotlights.clear()
        hw.hover_zones.clear()
        silent = self._silent_frames > 0
        sel = self._selection
        for hole in range(1, 11):
            lad = hole_ladder(hole)
            sel_here = sel is not None and sel[0] == hole
            if sel_here and sel[1] == "blow":
                hw.blow_fill[hole] = BLOW_C
                hw.blow_label_color[hole] = "#fff"
            if self._hover_note == lad.blow:
                hw.hover_zones.add((hole, "blow"))
            if sel_here and sel[1] == "draw":
                hw.draw_fill[hole] = DRAW_C
                hw.draw_label_color[hole] = "#fff"
            if self._hover_note == lad.draw:
                hw.hover_zones.add((hole, "draw"))
            if not silent and hole == self._live_hole and self._live_action in ("blow", "draw"):
                hw.spotlights[(hole, self._live_action)] = DETECT_OUTLINE
                hw.outline[(hole, self._live_action)] = DETECT_OUTLINE
        hw.redraw()

    def _draw_lock_widget(self, c, hole, x0, x1, top_b, bot_b):
        lad = hole_ladder(hole)
        practice = self._practice_hole()
        locked = self._locked_hole == hole
        hover_mid = self._hover_hole == hole and self._hover_zone == "mid"
        bx0, bx1 = x0 + 5, x1 - 5
        by0, by1 = top_b + 3, bot_b - 3
        outline = GOLD if locked else (HOVER_GLOW if hover_mid else "#3a3f47")
        c.create_rectangle(bx0, by0, bx1, by1, outline=outline,
                           width=2 if (locked or hover_mid) else 1, fill=DARK)
        if hover_mid:
            draw_hover_glow(c, bx0, by0, bx1, by1)
        c.create_text(bx0 + (bx1 - bx0) * 0.24, (by0 + by1) / 2, text=str(hole),
                      fill="#dfe6ef", font=(FONT, 12, "bold"))

        if not lad.has_bends:
            return

        met = self._hole_metrics(hole)
        goal, succeeded = met["goal"], met["succeeded"]
        bar_x = bx0 + (bx1 - bx0) * 0.62
        ya, yb = by0 + 6, by1 - 6

        def y_of(midi):
            return ya + (midi - lad.blow) / (lad.draw - lad.blow) * (yb - ya)

        c.create_line(bar_x, ya, bar_x, yb, fill="#555", width=2)
        for midi in lad.bend_notes:
            y = y_of(midi)
            if midi == goal:
                col = SUCCESS_C if succeeded else GOLD
                ww = 8
            else:
                col = "#8a8f98"
                ww = 5
            c.create_line(bar_x - ww, y, bar_x + ww, y, fill=col, width=2)
            if self._hover_note == midi:
                draw_line_glow(c, bar_x - ww - 2, y, bar_x + ww + 2, y, HOVER_GLOW)

        if practice == hole and self._display_midi is not None:
            col = DIM if not self._in_range else "#fff"
            y = y_of(max(lad.lo, min(lad.hi, self._display_midi)))
            c.create_polygon(bar_x + 9, y, bar_x + 16, y - 4, bar_x + 16, y + 4,
                             fill=col, outline="")

    # ----- drawing: piano -------------------------------------------------

    def _sync_piano(self):
        pw = self.piano
        pw.fills.clear(); pw.outlines.clear()
        pw.spotlights.clear()
        sel_blow = sel_draw = sel_midi = None
        if self._selection:
            hole = self._selection[0]
            if hole != 0:
                sel_blow = HARMONICA_C[hole]["blow"]
                sel_draw = HARMONICA_C[hole]["draw"]
            sel_midi = self._selection[3]
        # Priority blow > draw > goal: assign least-specific first.
        if sel_midi is not None:
            pw.fills[sel_midi] = GOLD
        if sel_draw is not None:
            pw.fills[sel_draw] = DRAW_C
        if sel_blow is not None:
            pw.fills[sel_blow] = BLOW_C
        if self._silent_frames == 0 and self._current_midi is not None:
            pw.spotlights[self._current_midi] = DETECT_OUTLINE
            pw.outlines[self._current_midi] = DETECT_OUTLINE
        pw.hover_note = self._hover_note
        pw.redraw()

    # ----- drawing: bend practice panel ----------------------------------

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
                          fill=MUTED, font=(FONT, 13))
            c.create_text(w / 2, h / 2 + 16, text="click a hole's middle (lock button) to lock it",
                          fill="#666", font=(FONT, 10))
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
                      fill=SUCCESS_C if succeeded else TEXT, font=(FONT, 13, "bold"))

        c.create_rectangle(*reset_rect, outline="#666", fill=PANEL2)
        c.create_text((reset_rect[0] + reset_rect[2]) / 2, (reset_rect[1] + reset_rect[3]) / 2,
                      text="reset", fill=PEAK_C, font=(FONT, 9))

        c.create_rectangle(x_left, bar_y - bar_h / 2, x_right, bar_y + bar_h / 2,
                           fill=PANEL2, outline="#555")
        c.create_rectangle(x_left, bar_y - bar_h / 2, px(deepest), bar_y + bar_h / 2,
                           fill=DARK, outline="")

        disp = self._display_midi
        if has_live and disp is not None:
            mcol = DIM if not self._in_range else accuracy_color(cents_abs if cents_abs is not None else 99)
            mx = px(max(lo, min(hi, disp)))
            c.create_rectangle(mx, bar_y - bar_h / 2, px(natural), bar_y + bar_h / 2,
                               fill=mcol if self._in_range else DARK, outline="")
            c.create_polygon(mx, bar_y - bar_h / 2 - 2, mx - 7, bar_y - bar_h / 2 - 14,
                             mx + 7, bar_y - bar_h / 2 - 14, fill=mcol, outline="")
            c.create_line(mx, bar_y - bar_h / 2, mx, bar_y + bar_h / 2, fill=mcol, width=2)

        if self._peak_progress > 0.01:
            pxp = px(natural - self._peak_progress * span)
            c.create_line(pxp, bar_y - bar_h / 2 - 6, pxp, bar_y + bar_h / 2 + 6,
                          fill=PEAK_C, width=3)
            c.create_text(pxp, bar_y + bar_h / 2 + 34, text="peak", fill=PEAK_C,
                          font=(FONT, 8, "bold"))

        for midi in lad.notes:
            x = px(midi)
            col = BLOW_C if midi == lad.blow else DRAW_C if midi == lad.draw else "#bbb"
            is_goal = midi == goal
            is_near = nearest == midi
            is_hover = midi == self._hover_note
            locked_on = is_near and cents_abs is not None and cents_abs <= LOCK_CENTS
            tcol = (SUCCESS_C if (is_goal and succeeded) else GREEN if locked_on
                    else GOLD if is_goal else HOVER_GLOW if is_hover else col)
            wln = 3 if (is_goal or is_near or is_hover) else 1
            c.create_line(x, bar_y - bar_h / 2 - 10, x, bar_y + bar_h / 2 + 10, fill=tcol, width=wln)
            if is_goal:
                c.create_oval(x - 7, bar_y - bar_h / 2 - 25, x + 7, bar_y - bar_h / 2 - 11,
                              outline=tcol, width=2)
            c.create_text(x, bar_y - bar_h / 2 - 25, text=midi_name(midi), fill=tcol,
                          font=(FONT, 9, "bold" if (is_goal or is_near or is_hover) else "normal"))
            label = "blow" if midi == lad.blow else "draw" if midi == lad.draw else \
                f"bend {natural - midi}"
            c.create_text(x, bar_y + bar_h / 2 + 22, text=label, fill="#888", font=(FONT, 8))

        if has_live and self._in_range and cents is not None:
            sign = "+" if cents >= 0 else ""
            status = "LOCKED" if cents_abs <= LOCK_CENTS else "close" if cents_abs <= NEAR_CENTS else ""
            c.create_text(16, h - 54, anchor="w",
                          text=f"nearest: {midi_name(nearest)}   {sign}{cents:.0f} cents   {status}",
                          fill=accuracy_color(cents_abs), font=(FONT, 12, "bold"))
        elif has_live:
            c.create_text(16, h - 54, anchor="w", text="(off range)", fill=DIM,
                          font=(FONT, 12, "bold"))

        hm_x0, hm_x1, hm_y, hm_h = 16, w - 16, h - 26, 16
        label = f"{midi_name(goal)} done!" if succeeded else (
            f"hold {midi_name(goal)} for {self._hold_seconds:.0f}s"
            + ("" if locked else "  (lock to record)"))
        c.create_text(hm_x0, hm_y - 9, anchor="w", text=label, fill=MUTED, font=(FONT, 9))
        if locked and self._hold_fraction > 0 and not succeeded:
            secs_left = self._hold_seconds * (1.0 - self._hold_fraction)
            c.create_text(hm_x1, hm_y - 9, anchor="e", text=f"{secs_left:.1f}s",
                          fill=TEXT, font=(FONT, 9))
        c.create_rectangle(hm_x0, hm_y, hm_x1, hm_y + hm_h, fill=PANEL2, outline="#555")
        if succeeded:
            c.create_rectangle(hm_x0, hm_y, hm_x1, hm_y + hm_h, fill=SUCCESS_C, outline="")
            c.create_text(w / 2, hm_y + hm_h / 2, text="\u2713 goal held",
                          fill="#10302a", font=(FONT, 9, "bold"))
        else:
            if locked and self._hold_fraction > 0:
                c.create_rectangle(hm_x0, hm_y, hm_x0 + (hm_x1 - hm_x0) * self._hold_fraction,
                                   hm_y + hm_h, fill=GREEN, outline="")
            bh_frac = met["best_hold"]
            if bh_frac > 0.01:
                bx = hm_x0 + (hm_x1 - hm_x0) * bh_frac
                c.create_line(bx, hm_y - 2, bx, hm_y + hm_h + 2, fill="#fff", width=2)
                c.create_text(bx, hm_y - 9, text="best", fill="#fff", anchor="center",
                              font=(FONT, 7))

    # ----- settings -------------------------------------------------------

    def _open_settings(self):
        if self._settings_win is not None and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = tk.Toplevel(self.app.root)
        win.title("Bend Trainer settings")
        win.configure(bg=BG)
        win.geometry("330x330")
        win.transient(self.app.root)
        self._settings_win = win

        def slider(label, sub, frm, to, init, setter):
            tk.Label(win, text=label, bg=BG, fg=TEXT,
                     font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
            tk.Label(win, text=sub, bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", padx=14)
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

    # ----- practice update loop ------------------------------------------

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

        locked = self._locked_hole == hole
        if not (locked and self._in_range) or self.app.suppressed():
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
            self.app.play_success()
