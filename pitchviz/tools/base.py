"""Base class every tool implements.

A tool builds its UI into ``self.frame`` (a ttk.Frame the shell adds as a
notebook tab) and reacts to mic updates via ``on_audio``. The shell only feeds
audio to the *active* tab. Shared services (mute, playback, suppression) live on
the shell and are reached through ``self.app``.
"""

from __future__ import annotations

from tkinter import ttk


class ToolBase:
    #: Tab label shown in the toolkit.
    title = "Tool"

    def __init__(self, parent, engine, app):
        self.engine = engine          # core.audio.AudioEngine (shared)
        self.app = app                # the ToolkitApp shell
        self.frame = ttk.Frame(parent)

    # ----- lifecycle (override as needed) --------------------------------

    def on_audio(self, state):
        """Called once per processed audio block while this tab is active."""

    def on_show(self):
        """Called when this tool's tab becomes visible."""

    def on_hide(self):
        """Called when the user switches away from this tool's tab."""
