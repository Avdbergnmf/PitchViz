"""PitchViz - Bucket 2: live pitch detection in the console.

Opens the microphone and prints the detected note, frequency, and cents
offset a few times per second. Use this to sanity-check detection against
your harmonica before we wire it into the GUI (Bucket 3).

Run:
    python pitch_console.py            # use default input device
    python pitch_console.py --list     # list input devices and exit
    python pitch_console.py --device 8 # use a specific device index
"""

import argparse
import queue
import sys

import numpy as np
import sounddevice as sd

from pitchviz.core.audio import list_input_devices
from pitchviz.core.pitch import detect_pitch, freq_to_note

SAMPLERATE = 44100
# Larger block = better low-frequency resolution at the cost of latency.
# ~46 ms at 44.1 kHz, plenty fast for following a sustained note.
BLOCKSIZE = 2048


def _cents_bar(cents: float, width: int = 21) -> str:
    """A little [----|----] meter showing flat (left) vs sharp (right)."""
    center = width // 2
    pos = int(round(center + (cents / 50.0) * center))
    pos = max(0, min(width - 1, pos))
    chars = ["-"] * width
    chars[center] = "|"
    chars[pos] = "#"
    return "[" + "".join(chars) + "]"


def main():
    parser = argparse.ArgumentParser(description="Live pitch detection (console).")
    parser.add_argument("--list", action="store_true", help="list input devices and exit")
    parser.add_argument("--device", type=int, default=None, help="input device index")
    args = parser.parse_args()

    devices = list_input_devices()
    if args.list:
        print("Input devices:")
        for idx, label in devices:
            print(f"  {label}")
        return

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=16)

    def callback(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        try:
            audio_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    print("Listening... play a sustained note. Press Ctrl+C to stop.\n")
    try:
        with sd.InputStream(
            device=args.device,
            channels=1,
            samplerate=SAMPLERATE,
            blocksize=BLOCKSIZE,
            callback=callback,
        ):
            while True:
                block = audio_q.get()
                freq = detect_pitch(block, SAMPLERATE)
                if freq is None:
                    # Carriage return keeps it on one tidy line.
                    print(f"\r{'(listening...)':<48}", end="", flush=True)
                    continue
                note, cents, _ = freq_to_note(freq)
                line = f"{note:>4}  {freq:7.1f} Hz  {cents:+6.1f}c  {_cents_bar(cents)}"
                print(f"\r{line:<48}", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"\nAudio error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
