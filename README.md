# PitchViz

A small desktop tool to help practice **harmonica bends** by visualizing your
microphone input in real time. The goal is to show the note you're playing,
where you can bend it to, and how far along that bend you currently are.

Target instrument: standard 10-hole diatonic harmonica, key of **C**.

## Status

Built incrementally in small buckets:

- [x] **Bucket 1 — Mic capture + level meter** (`level_meter.py`)
- [x] **Bucket 2 — Pitch detection, console** (`pitch.py`, `pitch_console.py`)
- [x] **Bucket 3 — GUI assembly** (`app.py`, `harmonica.py`): level + note + piano + C-harp hole
- [x] **Bucket 4 — Harmonica bend trainer**: 10-hole diagram + live bend practice panel

## Setup

Requires Python 3.10+. Create a virtual environment and install the deps:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`tkinter` is part of the standard library, so nothing extra is needed for the GUI.

## Running Bucket 1 (level meter)

With the venv activated:

```powershell
python level_meter.py
```

Or without activating it:

```powershell
.\.venv\Scripts\python.exe level_meter.py
```

A small window opens with:

- a dropdown to pick which microphone / input device to use
- a live level bar (green/yellow/red) showing input loudness
- a numeric dBFS readout and a peak-hold marker

Pick your mic from the dropdown and make some noise — the bar should react.

## Running Bucket 2 (pitch detection, console)

Live readings from the mic (note, frequency, cents offset):

```powershell
python pitch_console.py            # default input device
python pitch_console.py --list     # list input devices and exit
python pitch_console.py --device 8 # pick a device by index
```

Verify the detection logic without a mic (synthetic sine self-test):

```powershell
python pitch.py
```

## Running the app (GUI)

The combined window shows everything:

- **Input level** bar with a **draggable detection-threshold** marker (drag it to
  set how loud a sound must be before a note is detected). The detected note is
  shown as an **outline** (piano + harmonica) so it doesn't clash with blow/draw
  colors.
- A **clickable 10-hole harmonica diagram** with blow/draw note labels. Each
  hole: **top = blow, bottom = draw, middle = lock button**. The lock button
  holds the hole number plus a small **vertical bend-bar** (blow top / draw
  bottom) that mirrors the practice panel — bend-goal lines, your live position,
  and a teal success state. Locking is the **only** way to lock/unlock and plays
  the goal note.
- The **bend practice panel** (main focus): the whole hole as a pitch ladder
  from **blow (blue)** to **draw (orange)** with the **bend targets in between**.
  A live marker tracks your pitch (locked or not); when you go out of the
  bendable range it **freezes the last pitch and fades**. A recede-able **peak**
  line shows your current bend depth. Click a target to set your **goal** (and
  hear it). The **hold meter** (with a **best-hold high-water mark**) rewards
  holding the goal in tune for a configurable number of seconds with a **chime**
  and a **success color**. Progress (hold / best-hold / success) is recorded
  **only while locked**; pitch indication works always.
- A **clickable piano keyboard**. Hovering a note anywhere (piano, harmonica, or
  a practice-panel target) **cross-highlights** it everywhere.

Clicking a note plays it; recording is paused until that sound finishes.
**reset** (in the panel) clears the current hole; **Reset all bests** lives in
settings, alongside peak inertia, marker smoothing, and hold-to-succeed seconds.
Top-right: **mute** and the **gear**.

```powershell
python app.py
```

Check the note → hole mapping and bend lanes without a mic:

```powershell
python harmonica.py
```
