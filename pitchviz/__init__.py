"""PitchViz - a small harmonica practice toolkit (key of C).

The app is a tabbed shell that hosts independent *tools*. Code is split into:

- ``pitchviz.core``    shared, GUI-free logic (audio, pitch, harmonica, music theory, theme)
- ``pitchviz.widgets`` reusable Tk canvas views (piano, harmonica diagram, level meter)
- ``pitchviz.tools``   one module per tool (each becomes a tab)
- ``pitchviz.shell``   the window + tab bar that wires tools to the shared audio engine

Run it with ``python run.py`` or ``python -m pitchviz``.
"""
