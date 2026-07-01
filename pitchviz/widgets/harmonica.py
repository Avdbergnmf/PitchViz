"""Reusable 10-hole harmonica diagram canvas.

Each hole has three zones: top = blow, bottom = draw, middle = a tool-defined
area (a lock button for the bend trainer, a recommendation indicator for the
jam tool). The widget owns geometry, hit-testing, and base drawing; the tool
supplies colors and an optional ``middle_renderer``.

Config written by the tool before ``redraw()``:
    blow_fill[hole] / draw_fill[hole]          -> zone fill color (or absent)
    blow_label_color[hole] / draw_label_color  -> label color (default light)
    outline[(hole, "blow"|"draw")]             -> reed outline overlay
    spotlights[(hole, "blow"|"draw")]          -> soft live-note in-box glow
    middle_renderer(canvas, hole, x0, x1, top_b, bot_b)  -> custom middle

Events: on_click(hole, zone, y) / on_hover(hole, zone, y) / on_leave(), where zone
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
        self.spotlights: dict[tuple[int, str], str] = {}
        self.blow_markers: dict[int, str] = {}   # "root" | "chord"
        self.draw_markers: dict[int, str] = {}
        self.hover_zone: tuple[int, str] | None = None
        self.hover_zones: set[tuple[int, str]] = set()

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
        self.hover_zone = (hole, zone) if hole and zone else None
        if hole and self.on_hover:
            self.on_hover(hole, zone, event.y)
        elif hole:
            self.redraw()

    def _leave(self, _event):
        self.hover_zone = None
        if self.on_leave:
            self.on_leave()
        else:
            self.redraw()

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
            self._draw_spotlight_if_needed(c, hole, "blow", x0, 0, x1, top_b,
                                           bf or T.PANEL)
            c.create_text(cx, 11, text=midi_name(lad.blow),
                          fill=self.blow_label_color.get(hole, LABEL_COLOR), font=(T.FONT, 8))
            self._draw_zone_marker(c, self.blow_markers.get(hole), cx, 29)

            df = self.draw_fill.get(hole)
            if df:
                c.create_rectangle(x0, bot_b, x1, h, fill=df, outline="")
            self._draw_spotlight_if_needed(c, hole, "draw", x0, bot_b, x1, h,
                                           df or T.PANEL)
            c.create_text(cx, h - 11, text=midi_name(lad.draw),
                          fill=self.draw_label_color.get(hole, LABEL_COLOR), font=(T.FONT, 8))
            self._draw_zone_marker(c, self.draw_markers.get(hole), cx, h - 29)

            if self.middle_renderer:
                self.middle_renderer(c, hole, x0, x1, top_b, bot_b)
            else:
                c.create_text(cx, (top_b + bot_b) / 2, text=str(hole),
                              fill="#dfe6ef", font=(T.FONT, 12, "bold"))

            ob = self.outline.get((hole, "blow"))
            if ob:
                T.draw_effect_outline(c, x0 + 1, 1, x1 - 1, top_b, ob, width=2)
            od = self.outline.get((hole, "draw"))
            if od:
                T.draw_effect_outline(c, x0 + 1, bot_b, x1 - 1, h - 1, od, width=2)

            if self._is_hovered(hole, "blow"):
                T.draw_hover_glow(c, x0 + 1, 1, x1 - 1, top_b)
            elif self._is_hovered(hole, "draw"):
                T.draw_hover_glow(c, x0 + 1, bot_b, x1 - 1, h - 1)
            elif self._is_hovered(hole, "mid"):
                T.draw_hover_glow(c, x0 + 4, top_b + 3, x1 - 4, bot_b - 3)

    def _is_hovered(self, hole: int, zone: str) -> bool:
        return self.hover_zone == (hole, zone) or (hole, zone) in self.hover_zones

    def _draw_spotlight_if_needed(self, c, hole: int, zone: str,
                                  x0: float, y0: float, x1: float, y1: float,
                                  base: str):
        color = self.spotlights.get((hole, zone))
        if color:
            T.draw_spotlight(c, x0 + 3, y0 + 3, x1 - 3, y1 - 3, color, base)

    @staticmethod
    def _draw_zone_marker(c, kind: str | None, x: float, y: float):
        """Chord-tone marker on a blow/draw reed (filled root, outline third/fifth)."""
        if kind == "root":
            c.create_text(x, y, text="\u25c6", fill=T.GOLD, font=(T.FONT, 17, "bold"))
        elif kind == "chord":
            c.create_text(x, y, text="\u25c7", fill=T.CHORD_MARK, font=(T.FONT, 15, "bold"))
