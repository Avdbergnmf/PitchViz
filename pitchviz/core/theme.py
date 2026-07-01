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
WHITE_KEY_HOVER = "#e6edf5"
BLACK_KEY_HOVER = "#3a3f47"

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
BLOW_HOVER = "#3d5a73"
DRAW_HOVER = "#73553d"

# --- Markers / highlights ---------------------------------------------------
GOLD = "#ffd166"          # selection / chord emphasis
PEAK_C = "#4d96ff"
SUCCESS_C = "#39e0c0"
DETECT_OUTLINE = "#9be8b0"
DIM = "#565c66"
HOVER_NOTE = "#ffffff"
CHORD_MARK = "#ffffff"    # diamond marker on chord tones

ROOT_C = "#ffab40"        # position hint text

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