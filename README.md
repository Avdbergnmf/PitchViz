# PitchViz

A small desktop app for practicing **harmonica bends**. It listens to your mic
and shows the note you're playing, the bends you can reach, and how close you
are to hitting and holding them.

Target instrument: standard 10-hole diatonic harmonica, key of **C**.

![PitchViz screenshot](assets/screenshot.png)

## Quick start

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

(`tkinter` ships with Python, so there's nothing else to install.)

## How to use it

- **Input level** (top): drag the marker to set the detection threshold (how
  loud a sound must be before it's detected).
- **Harmonica diagram**: click a hole's **top** to hear its blow note, **bottom**
  for its draw note, or the **middle (lock button)** to lock that hole for
  practice. The lock button shows the hole number and a mini bend-bar.
- **Bend practice panel** (main view): the hole shown as a ladder from blow
  (blue) to draw (orange) with the bend targets in between.
  - A marker follows your pitch; the **peak** line shows your current bend depth.
  - Click a target to set it as your **goal** and hear it.
  - Hold the goal in tune for a few seconds to score a **success** (chime +
    teal). The **hold bar** tracks your best attempt. Progress is recorded only
    while a hole is **locked**.
- **Piano**: shows the detected note (outline) and the selected hole's blow/draw
  keys. Hovering a note anywhere highlights it everywhere.
- **Top-right**: **mute** playback, or open **settings** (peak inertia, marker
  smoothing, hold-to-succeed seconds, reset all).

## Project layout

| File | Purpose |
| --- | --- |
| `app.py` | The full GUI app |
| `pitch.py` | Pitch detection + note math |
| `harmonica.py` | C-harmonica note/hole/bend mapping |
| `synth.py` | Tone + success-chime playback |
| `level_meter.py` | Audio capture + level helpers |

## Extra tools (optional)

```powershell
python pitch_console.py    # live pitch readings in the terminal
python pitch.py            # pitch-detection self-test (no mic)
python harmonica.py        # note/hole/bend mapping self-test
python level_meter.py      # standalone mic level meter
```
