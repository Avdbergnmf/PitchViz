"""Reusable 10-hole harmonica diagram canvas.

Each hole has three zones: top = blow, bottom = draw, middle = a tool-defined
area (a lock button for the bend trainer, a recommendation indicator for the
jam tool). The widget owns geometry, hit-testing, and base drawing; the tool
supplies colors and an optional ``middle_renderer``.

Config written by the tool before ``redraw()``:
    blow_fill[hole] / draw_fill[hole]          -> zone fill color (or absent)
    blow_label_color[hole] / draw_label_color  -> label color (default light)
    outline[(hole, "blow"|"draw")]             -> reed outline overlay
    middle_renderer(canvas, hole, x0, x1, top_b, bot_b)  -> custom middle

Events: on_click(hole, zone, y) / on_hover(hole, zone) / on_leave(), where zone
is "blow", "draw", or "mid" and ``y`` is the click's pixel position (so a tool
can resolve which bend tick in the middle was clicked).
"""

from __future__ import annotations

import tkinter as tk

from ..core import theme as T
from ..core.harmonica import hole_ladder, midi_name

LABEL_COLOR = "#cfd8e3"


class HarmonicaWidget(tk.Canvas):
    def __init__(self, master, on_click=None, on_hover=None, on_leave=None,
                 height: int = 140, **kw):
        super().__init__(master, height=height, bg=T.BG, highlightthickness=0, **kw)
        self.on_click = on_click
        self.on_hover = on_hover
        self.on_leave = on_leave
        self.middle_renderer = None

        self.blow_fill: dict[int, str] = {}
        self.draw_fill: dict[int, str] = {}
        self.blow_label_color: dict[int, str] = {}
        self.draw_label_color: dict[int, str] = {}
        self.outline: dict[tuple[int, str], str] = {}
        self.blow_markers: dict[int, str] = {}   # "root" | "chord"
        self.draw_markers: dict[int, str] = {}

        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)

    # ----- geometry / events ---------------------------------------------

    def zone_at(self, x, y):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return None, None
        hole = max(1, min(10, int(x // (w / 10)) + 1))
        if y < h * 0.26:
            zone = "blow"
        elif y > h * 0.74:
            zone = "draw"
        else:
            zone = "mid"
        return hole, zone

    def _click(self, event):
        hole, zone = self.zone_at(event.x, event.y)
        if hole and self.on_click:
            self.on_click(hole, zone, event.y)

    def _motion(self, event):
        hole, zone = self.zone_at(event.x, event.y)
        if hole and self.on_hover:
            self.on_hover(hole, zone)

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
        cw = w / 10
        top_b, bot_b = h * 0.26, h * 0.74

        for hole in range(1, 11):
            lad = hole_ladder(hole)
            x0, x1 = (hole - 1) * cw + 3, hole * cw - 3
            cx = (x0 + x1) / 2
            c.create_rectangle(x0, 0, x1, h, fill=T.PANEL, outline="#444")

            bf = self.blow_fill.get(hole)
            if bf:
                c.create_rectangle(x0, 0, x1, top_b, fill=bf, outline="")
            c.create_text(cx, 11, text=midi_name(lad.blow),
                          fill=self.blow_label_color.get(hole, LABEL_COLOR), font=(T.FONT, 8))
            self._draw_zone_marker(c, self.blow_markers.get(hole), x1 - 6, 12)

            df = self.draw_fill.get(hole)
            if df:
                c.create_rectangle(x0, bot_b, x1, h, fill=df, outline="")
            c.create_text(cx, h - 11, text=midi_name(lad.draw),
                          fill=self.draw_label_color.get(hole, LABEL_COLOR), font=(T.FONT, 8))
            self._draw_zone_marker(c, self.draw_markers.get(hole), x1 - 6, h - 12)

            if self.middle_renderer:
                self.middle_renderer(c, hole, x0, x1, top_b, bot_b)
            else:
                c.create_text(cx, (top_b + bot_b) / 2, text=str(hole),
                              fill="#dfe6ef", font=(T.FONT, 12, "bold"))

            ob = self.outline.get((hole, "blow"))
            if ob:
                c.create_rectangle(x0 + 1, 1, x1 - 1, top_b, outline=ob, width=2)
            od = self.outline.get((hole, "draw"))
            if od:
                c.create_rectangle(x0 + 1, bot_b, x1 - 1, h - 1, outline=od, width=2)

    @staticmethod
    def _draw_zone_marker(c, kind: str | None, x: float, y: float):
        """Chord-tone marker on a blow/draw reed (filled root, outline third/fifth)."""
        if kind == "root":
            c.create_text(x, y, text="\u25c6", fill=T.GOLD, font=(T.FONT, 11, "bold"))
        elif kind == "chord":
            c.create_text(x, y, text="\u25c7", fill=T.CHORD_MARK, font=(T.FONT, 9, "bold"))
