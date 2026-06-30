"""PitchViz - harmonica layout and note -> hole mapping (key of C).

Standard 10-hole diatonic harmonica in C (Richter tuning). Maps any MIDI
note to the hole(s) and breath action(s) that produce it, including the
common draw/blow bends.

Run directly for a self-test:
    python harmonica.py
"""

from __future__ import annotations

from dataclasses import dataclass

from pitch import NOTE_NAMES, note_to_freq

# Per hole: natural blow/draw notes (MIDI) and the chromatic bend steps
# reachable from each. Bends are listed shallowest-first (1 semitone, then 2...).
#   C4 = 60.
HARMONICA_C: dict[int, dict] = {
    1: {"blow": 60, "draw": 62, "draw_bends": [61], "blow_bends": []},
    2: {"blow": 64, "draw": 67, "draw_bends": [66, 65], "blow_bends": []},
    3: {"blow": 67, "draw": 71, "draw_bends": [70, 69, 68], "blow_bends": []},
    4: {"blow": 72, "draw": 74, "draw_bends": [73], "blow_bends": []},
    5: {"blow": 76, "draw": 77, "draw_bends": [], "blow_bends": []},
    6: {"blow": 79, "draw": 81, "draw_bends": [80], "blow_bends": []},
    7: {"blow": 84, "draw": 83, "draw_bends": [], "blow_bends": []},
    8: {"blow": 88, "draw": 86, "draw_bends": [], "blow_bends": [87]},
    9: {"blow": 91, "draw": 89, "draw_bends": [], "blow_bends": [90]},
    10: {"blow": 96, "draw": 93, "draw_bends": [], "blow_bends": [95, 94]},
}

# Playable range of the instrument, handy for drawing the piano.
LOWEST_MIDI = 60   # C4
HIGHEST_MIDI = 96  # C7


@dataclass(frozen=True)
class Location:
    """One way to produce a given note on the harmonica."""

    hole: int
    action: str        # "blow", "draw", "blow bend", "draw bend"
    bend_steps: int     # 0 for a natural note, 1+ for how deep the bend is

    @property
    def is_bend(self) -> bool:
        return self.bend_steps > 0

    @property
    def label(self) -> str:
        if self.is_bend:
            return f"{self.hole} {self.action} {self.bend_steps}"
        return f"{self.hole} {self.action}"


def _build_note_map() -> dict[int, list[Location]]:
    note_map: dict[int, list[Location]] = {}

    def add(midi: int, hole: int, action: str, steps: int):
        note_map.setdefault(midi, []).append(Location(hole, action, steps))

    for hole, spec in HARMONICA_C.items():
        add(spec["blow"], hole, "blow", 0)
        add(spec["draw"], hole, "draw", 0)
        for i, midi in enumerate(spec["draw_bends"], start=1):
            add(midi, hole, "draw bend", i)
        for i, midi in enumerate(spec["blow_bends"], start=1):
            add(midi, hole, "blow bend", i)

    for locations in note_map.values():
        locations.sort(key=lambda loc: (loc.hole, loc.bend_steps))
    return note_map


NOTE_MAP: dict[int, list[Location]] = _build_note_map()


def note_locations(midi: int) -> list[Location]:
    """All hole/action combinations that produce the given MIDI note."""
    return NOTE_MAP.get(midi, [])


def describe(midi: int) -> str:
    """Human-readable hole description, e.g. '2 draw / 3 blow' or '(off-harp)'."""
    locs = note_locations(midi)
    if not locs:
        return "(not on C harp)"
    return " / ".join(loc.label for loc in locs)


def midi_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


# --- Bend lanes: continuous pitch ranges you traverse while bending ---------

@dataclass(frozen=True)
class BendLane:
    """A single hole's bendable range, from its natural note down to the
    deepest reachable bend. `notes` is [natural, bend1, bend2, ...] in
    descending pitch order (each ~1 semitone apart)."""

    hole: int
    action: str            # "draw" or "blow"
    notes: tuple[int, ...]  # descending MIDI, notes[0] is the natural note

    @property
    def top(self) -> int:
        return self.notes[0]

    @property
    def bottom(self) -> int:
        return self.notes[-1]


def _build_lanes() -> list[BendLane]:
    lanes: list[BendLane] = []
    for hole, spec in HARMONICA_C.items():
        if spec["draw_bends"]:
            lanes.append(BendLane(hole, "draw", (spec["draw"], *spec["draw_bends"])))
        if spec["blow_bends"]:
            lanes.append(BendLane(hole, "blow", (spec["blow"], *spec["blow_bends"])))
    return lanes


LANES: list[BendLane] = _build_lanes()


@dataclass(frozen=True)
class BendState:
    """Live snapshot of where the player is within a bend lane."""

    lane: BendLane
    midi_float: float
    progress: float          # 0.0 at natural note, 1.0 at deepest bend
    nearest_target: int      # MIDI of closest note in the lane
    target_index: int        # index into lane.notes of nearest_target
    cents_off: float         # signed cents from nearest_target (+ sharp)


def active_lane(midi_float: float, margin: float = 0.6) -> BendLane | None:
    """Pick the bend lane the player is currently in, if any.

    A pitch sitting between a natural note and its bend (a value that isn't a
    natural harp note) uniquely identifies the lane, so we choose the lane
    whose notes are closest to the current pitch.
    """
    candidates = [
        lane for lane in LANES
        if lane.bottom - margin <= midi_float <= lane.top + margin
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda lane: min(abs(midi_float - n) for n in lane.notes),
    )


def bend_state_for_lane(lane: BendLane, midi_float: float) -> BendState:
    """Bend snapshot for a *specific* lane (used when a note is pinned)."""
    span = lane.top - lane.bottom
    progress = 0.0 if span == 0 else (lane.top - midi_float) / span
    progress = max(0.0, min(1.0, progress))
    target_index = min(
        range(len(lane.notes)),
        key=lambda i: abs(midi_float - lane.notes[i]),
    )
    nearest = lane.notes[target_index]
    cents_off = (midi_float - nearest) * 100.0
    return BendState(lane, midi_float, progress, nearest, target_index, cents_off)


def bend_state(midi_float: float) -> BendState | None:
    """Compute the full bend snapshot for the current pitch, or None."""
    lane = active_lane(midi_float)
    if lane is None:
        return None
    return bend_state_for_lane(lane, midi_float)


def lane_for(hole: int, action: str) -> BendLane | None:
    """Find the bend lane for a given hole + breath action ('blow'/'draw')."""
    for lane in LANES:
        if lane.hole == hole and lane.action == action:
            return lane
    return None


# --- Whole-hole ladder: blow <-> draw with the bend steps in between --------

@dataclass(frozen=True)
class HoleLadder:
    """A hole viewed as one chromatic pitch ladder from its lower reed to its
    higher reed. The bend targets are exactly the semitones in between.

    You bend *down* from the higher reed (`natural`); the lower reed (`other`)
    is shown for reference but isn't reachable by bending.
    """

    hole: int
    blow: int
    draw: int
    notes: tuple[int, ...]   # ascending pitch, lo .. hi

    @property
    def lo(self) -> int:
        return self.notes[0]

    @property
    def hi(self) -> int:
        return self.notes[-1]

    @property
    def natural(self) -> int:
        """The bendable reed (higher pitch)."""
        return self.notes[-1]

    @property
    def other(self) -> int:
        """The reference reed (lower pitch, not reachable by bending)."""
        return self.notes[0]

    @property
    def has_bends(self) -> bool:
        return len(self.notes) > 2

    @property
    def bend_notes(self) -> tuple[int, ...]:
        """The reachable bend targets (everything strictly between the reeds)."""
        return self.notes[1:-1]

    @property
    def deepest_bend(self) -> int | None:
        return self.notes[1] if self.has_bends else None

    @property
    def natural_action(self) -> str:
        return "draw" if self.draw == self.hi else "blow"

    def action_of(self, midi: int) -> str:
        if midi == self.blow:
            return "blow"
        if midi == self.draw:
            return "draw"
        return "bend"


def hole_ladder(hole: int) -> HoleLadder:
    spec = HARMONICA_C[hole]
    lo, hi = sorted((spec["blow"], spec["draw"]))
    return HoleLadder(hole, spec["blow"], spec["draw"], tuple(range(lo, hi + 1)))


def _self_test() -> int:
    checks = [
        (60, "1 blow"),
        (62, "1 draw"),
        (61, "1 draw bend 1"),
        (67, "2 draw / 3 blow"),
        (66, "2 draw bend 1"),
        (65, "2 draw bend 2"),
        (68, "3 draw bend 3"),
        (87, "8 blow bend 1"),
        (95, "10 blow bend 1"),
        (94, "10 blow bend 2"),
        (96, "10 blow"),
        (50, "(not on C harp)"),
    ]
    print(f"{'MIDI':>5} {'note':>5} {'expected':>22} {'got':>22}")
    failures = 0
    for midi, expected in checks:
        got = describe(midi)
        ok = got == expected
        flag = "" if ok else "  <-- MISMATCH"
        print(f"{midi:>5} {midi_name(midi):>5} {expected:>22} {got:>22}{flag}")
        if not ok:
            failures += 1
    # Bend-lane checks.
    print("\nBend-lane checks:")
    lane_checks = [
        (74.0, 4, "draw", 0.0, "D5"),     # natural hole-4 draw
        (73.6, 4, "draw", 0.4, "D5"),     # partway into the bend
        (73.0, 4, "draw", 1.0, "C#5"),    # fully bent
        (70.2, 3, "draw", None, "A#4"),   # mid bend on hole 3
    ]
    for mf, exp_hole, exp_action, exp_prog, exp_near in lane_checks:
        st = bend_state(mf)
        if st is None:
            print(f"  {mf:6.2f} -> (no lane)  <-- expected hole {exp_hole}")
            failures += 1
            continue
        near_name = midi_name(st.nearest_target)
        ok = st.lane.hole == exp_hole and st.lane.action == exp_action and near_name == exp_near
        if exp_prog is not None:
            ok = ok and abs(st.progress - exp_prog) < 0.05
        flag = "" if ok else "  <-- MISMATCH"
        print(f"  {mf:6.2f} -> hole {st.lane.hole} {st.lane.action}, "
              f"prog {st.progress:.2f}, near {near_name} ({st.cents_off:+.0f}c){flag}")
        if not ok:
            failures += 1

    # Hole-ladder checks.
    print("\nHole-ladder checks:")
    ladder_checks = [
        (4, (72, 73, 74), (73,), 74, 72),     # blow C5, draw D5, bend C#5
        (3, (67, 68, 69, 70, 71), (68, 69, 70), 71, 67),
        (10, (93, 94, 95, 96), (94, 95), 96, 93),  # blow bends
        (5, (76, 77), (), 77, 76),            # no bend
    ]
    for hole, exp_notes, exp_bends, exp_nat, exp_other in ladder_checks:
        lad = hole_ladder(hole)
        ok = (lad.notes == exp_notes and lad.bend_notes == exp_bends
              and lad.natural == exp_nat and lad.other == exp_other)
        flag = "" if ok else "  <-- MISMATCH"
        print(f"  hole {hole}: notes={lad.notes} bends={lad.bend_notes} "
              f"natural={midi_name(lad.natural)} ({lad.natural_action}){flag}")
        if not ok:
            failures += 1

    print("\nAll good." if failures == 0 else f"\n{failures} mismatch(es).")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
