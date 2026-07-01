"""Shared colors, fonts, and visual constants.

Centralized so every tool and widget looks consistent and a single edit
restyles the whole app.
"""

# --- Surfaces ---------------------------------------------------------------
BG = "#1e1e1e"
PANEL = "#2b2b2b"
PANEL2 = "#333842"
DARK = "#262a31"

# --- Accents ----------------------------------------------------------------
ACCENT = "#5468ff"        # timeline / progression accent
ACCENT_DIM = "#3a4254"

# --- Piano keys -------------------------------------------------------------
WHITE_KEY = "#f5f5f5"
WHITE_KEY_EDGE = "#cccccc"
BLACK_KEY = "#222222"

# --- Accuracy / level -------------------------------------------------------
GREEN = "#3ddc84"
YELLOW = "#f4d03f"
RED = "#e74c3c"

# --- Text -------------------------------------------------------------------
TEXT = "#e8e8e8"
MUTED = "#888888"

# --- Breath actions ---------------------------------------------------------
BLOW_C = "#5aa9e6"
DRAW_C = "#f39c5a"

# --- Markers / highlights ---------------------------------------------------
GOLD = "#ffd166"          # selection / chord emphasis
PEAK_C = "#4d96ff"
SUCCESS_C = "#39e0c0"
DETECT_OUTLINE = "#9be8b0"
DIM = "#565c66"
CHORD_MARK = "#ffffff"    # diamond marker on chord tones
HOVER_GLOW = "#ffffff"
HOVER_GLOW_SOFT = "#8fb7ff"
HOVER_GLOW_WIDTH = 3
SELECT_OUTLINE_WIDTH = 3
DETECT_OUTLINE_WIDTH = 3

# --- Tuning thresholds (cents) ---------------------------------------------
LOCK_CENTS = 15
NEAR_CENTS = 35

WHITE_SEMITONES = {0, 2, 4, 5, 7, 9, 11}

FONT = "Segoe UI"


def is_white(midi: int) -> bool:
    return (midi % 12) in WHITE_SEMITONES


def lerp_color(c1: str, c2: str, t: float) -> str:
    """Linear blend between two ``#rrggbb`` colors (t=0 -> c1, t=1 -> c2)."""
    t = max(0.0, min(1.0, float(t)))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def accuracy_color(cents_abs: float) -> str:
    if cents_abs <= LOCK_CENTS:
        return GREEN
    if cents_abs <= NEAR_CENTS:
        return YELLOW
    return RED


def draw_effect_outline(canvas, x0, y0, x1, y1, color: str, width: int = SELECT_OUTLINE_WIDTH):
    """Shared crisp selection/live-note outline for canvas controls."""
    canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=width)


def draw_hover_glow(canvas, x0, y0, x1, y1):
    """Shared hover treatment: visible even when the control is already filled."""
    canvas.create_rectangle(x0, y0, x1, y1, outline=HOVER_GLOW_SOFT, width=HOVER_GLOW_WIDTH + 2)
    canvas.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2,
                            outline=HOVER_GLOW, width=HOVER_GLOW_WIDTH)


def draw_line_glow(canvas, x0, y0, x1, y1, color: str = HOVER_GLOW):
    """Shared glow treatment for short line targets such as bend ticks."""
    canvas.create_line(x0, y0, x1, y1, fill=HOVER_GLOW_SOFT, width=7)
    canvas.create_line(x0, y0, x1, y1, fill=color, width=3)


def draw_spotlight(canvas, x0, y0, x1, y1, color: str, base: str):
    """Soft in-box live-note highlight that does not rely on edge outlines."""
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    half_w = (x1 - x0) / 2
    half_h = (y1 - y0) / 2
    for i, t in enumerate((0.18, 0.28, 0.40, 0.54)):
        scale = 1.0 - i * 0.18
        fill = lerp_color(base, color, t)
        canvas.create_oval(
            cx - half_w * scale, cy - half_h * scale,
            cx + half_w * scale, cy + half_h * scale,
            fill=fill, outline="",
        )
