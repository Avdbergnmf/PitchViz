"""Input-level meter with a draggable detection-threshold marker.

A thin, reusable canvas. Set ``level_fraction`` / ``threshold_frac`` and call
``redraw()``. Dragging emits ``on_threshold(frac)`` so the owner can update the
shared audio engine.
"""

from __future__ import annotations

import tkinter as tk

from ..core import theme as T


class LevelMeterWidget(tk.Canvas):
    def __init__(self, master, on_threshold=None, height: int = 22, **kw):
        super().__init__(master, height=height, bg=T.PANEL, highlightthickness=0,
                         cursor="sb_h_double_arrow", **kw)
        self.on_threshold = on_threshold
        self.level_fraction = 0.0
        self.threshold_frac = 0.20
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)

    def _drag(self, event):
        w = self.winfo_width()
        if w > 1 and self.on_threshold:
            self.on_threshold(max(0.0, min(1.0, event.x / w)))

    def redraw(self):
        c = self
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.create_rectangle(0, 0, w, h, fill=T.PANEL, outline="")
        fill_w = int(w * self.level_fraction)
        for x in range(0, fill_w, 4):
            frac_x = x / w
            color = T.GREEN if frac_x < 0.6 else T.YELLOW if frac_x < 0.85 else T.RED
            c.create_rectangle(x, 0, x + 3, h, fill=color, outline="")
        tx = int(self.threshold_frac * w)
        c.create_line(tx, 0, tx, h, fill="#fff", width=2)
        c.create_polygon(tx, 6, tx - 5, 0, tx + 5, 0, fill="#fff", outline="")
