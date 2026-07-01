"""Reusable piano-keyboard canvas spanning the C-harmonica range.

A config-driven renderer: the owning tool sets highlight layers each frame and
calls ``redraw()``; the widget emits ``on_click(midi)`` / ``on_hover(midi|None)``.

Layers (priority high -> low):
    fills[midi]    -> solid key color (selection, recommended, ...)
    spotlights[midi] -> soft live-note glow inside the key
    hover_note     -> a single key gets the shared edge-glow treatment
    base           -> normal white/black key
``outlines[midi]`` draws an outline overlay on top (e.g. the live note).
"""

from __future__ import annotations

import tkinter as tk

from ..core import theme as T
from ..core.harmonica import HIGHEST_MIDI, LOWEST_MIDI, midi_name


class PianoWidget(tk.Canvas):
    def __init__(self, master, on_click=None, on_hover=None, on_leave=None,
                 height: int = 120, **kw):
        super().__init__(master, height=height, bg=T.BG, highlightthickness=0, **kw)
        self.on_click = on_click
        self.on_hover = on_hover
        self.on_leave = on_leave

        # Highlight layers, written by the tool before each redraw().
        self.fills: dict[int, str] = {}
        self.outlines: dict[int, str] = {}
        self.spotlights: dict[int, str] = {}
        self.markers: dict[int, str] = {}       # midi -> marker char (e.g. chord tone)
        self.marker_colors: dict[int, str] = {}
        self.hover_note: int | None = None

        self.whites = [m for m in range(LOWEST_MIDI, HIGHEST_MIDI + 1) if T.is_white(m)]

        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)

    # ----- geometry / events ---------------------------------------------

    def midi_at(self, x, y):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return None
        key_w = w / len(self.whites)
        bh, bw = int(h * 0.62), key_w * 0.62
        if y <= bh:  # black keys sit on top
            for midi in range(LOWEST_MIDI, HIGHEST_MIDI + 1):
                if T.is_white(midi):
                    continue
                cx = sum(1 for m in self.whites if m < midi) * key_w
                if abs(x - cx) <= bw / 2:
                    return midi
        return self.whites[max(0, min(len(self.whites) - 1, int(x // key_w)))]

    def _click(self, event):
        midi = self.midi_at(event.x, event.y)
        if midi is not None and self.on_click:
            self.on_click(midi)

    def _motion(self, event):
        if self.on_hover:
            self.on_hover(self.midi_at(event.x, event.y))

    def _leave(self, _event):
        if self.on_leave:
            self.on_leave()

    # ----- drawing --------------------------------------------------------

    def redraw(self):
        c = self
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        key_w = w / len(self.whites)

        for i, midi in enumerate(self.whites):
            x0, x1 = i * key_w, (i + 1) * key_w
            fill = self.fills.get(midi, T.WHITE_KEY)
            c.create_rectangle(x0, 0, x1, h, fill=fill, outline=T.WHITE_KEY_EDGE)
            self._draw_spotlight_if_needed(c, midi, x0, 0, x1, h, fill)
            oc = self.outlines.get(midi)
            if oc:
                T.draw_effect_outline(c, x0 + 2, 2, x1 - 2, h - 2, oc)
            mk = self.markers.get(midi)
            if mk:
                c.create_text((x0 + x1) / 2, 14, text=mk,
                              fill=self.marker_colors.get(midi, T.CHORD_MARK),
                              font=(T.FONT, 11, "bold"))
            if midi % 12 == 0:
                c.create_text((x0 + x1) / 2, h - 12, text=midi_name(midi),
                              fill="#666", font=(T.FONT, 8))

        bh, bw = int(h * 0.62), key_w * 0.62
        for midi in range(LOWEST_MIDI, HIGHEST_MIDI + 1):
            if T.is_white(midi):
                continue
            cx = sum(1 for m in self.whites if m < midi) * key_w
            fill = self.fills.get(midi, T.BLACK_KEY)
            c.create_rectangle(cx - bw / 2, 0, cx + bw / 2, bh, fill=fill, outline="#000")
            self._draw_spotlight_if_needed(c, midi, cx - bw / 2, 0, cx + bw / 2, bh, fill)
            oc = self.outlines.get(midi)
            if oc:
                T.draw_effect_outline(c, cx - bw / 2 + 1, 2, cx + bw / 2 - 1, bh - 2,
                                      oc, width=2)
            mk = self.markers.get(midi)
            if mk:
                c.create_text(cx, 12, text=mk,
                              fill=self.marker_colors.get(midi, T.CHORD_MARK),
                              font=(T.FONT, 9, "bold"))

        if self.hover_note is not None:
            self._draw_hover_glow(c, self.hover_note, key_w, h, bh, bw)

    def _draw_hover_glow(self, c, midi: int, key_w: float, h: int, bh: int, bw: float):
        if midi < LOWEST_MIDI or midi > HIGHEST_MIDI:
            return
        if T.is_white(midi):
            i = self.whites.index(midi)
            T.draw_hover_glow(c, i * key_w + 1, 1, (i + 1) * key_w - 1, h - 1)
            return
        cx = sum(1 for m in self.whites if m < midi) * key_w
        T.draw_hover_glow(c, cx - bw / 2 + 1, 1, cx + bw / 2 - 1, bh - 1)

    def _draw_spotlight_if_needed(self, c, midi: int, x0: float, y0: float,
                                  x1: float, y1: float, base: str):
        color = self.spotlights.get(midi)
        if color:
            T.draw_spotlight(c, x0 + 3, y0 + 3, x1 - 3, y1 - 3, color, base)
