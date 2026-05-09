#!/usr/bin/env python3
"""Generate a countermelody in the same key as an input melody.

Reads a MIDI file containing a melody, detects its key using
Krumhansl-Schmuckler key analysis, and writes a new MIDI file with
the original melody plus a generated countermelody voice.

The countermelody is built with simple species-counterpoint principles:
    - Stay diatonic to the detected key.
    - Prefer imperfect consonances (3rds and 6ths) against the melody.
    - Prefer stepwise motion within the countermelody itself.
    - Prefer contrary motion against the melody.
    - Avoid parallel 5ths and octaves.
"""

import argparse
import sys
from pathlib import Path

try:
    from music21 import converter, note, stream, meter, tempo, instrument
except ImportError:
    sys.stderr.write(
        "music21 is required. Install with:  pip install music21\n"
    )
    raise


IMPERFECT_CONSONANCES = {3, 4, 8, 9}   # m3, M3, m6, M6
PERFECT_CONSONANCES = {0, 7}           # unison/octave, P5
DISSONANT_P4 = {5}                     # P4 treated as dissonant in 2-voice


def detect_key(score):
    """Run Krumhansl-Schmuckler key analysis on the score."""
    return score.analyze("key")


def diatonic_pitch_classes(detected_key):
    """Return the set of pitch classes in the key's diatonic scale."""
    sc = detected_key.getScale()
    return {p.pitchClass for p in sc.getPitches()}


def melody_elements(score):
    """Yield notes and rests from the first part (or the flat score)."""
    if score.parts:
        source = score.parts[0].flatten()
    else:
        source = score.flatten()
    for el in source.notesAndRests:
        if isinstance(el, note.Note):
            yield el
        elif isinstance(el, note.Rest):
            yield el
        elif hasattr(el, "pitches") and el.pitches:
            top = max(el.pitches, key=lambda p: p.midi)
            n = note.Note()
            n.pitch = top
            n.duration = el.duration
            yield n


def score_candidate(candidate, melody_midi, prev_counter, prev_melody):
    """Heuristic score for a candidate countermelody pitch."""
    score = 0
    interval_semi = abs(candidate - melody_midi) % 12

    if interval_semi in IMPERFECT_CONSONANCES:
        score += 15
    elif interval_semi in PERFECT_CONSONANCES:
        score += 5
    elif interval_semi in DISSONANT_P4:
        score -= 5
    else:
        score -= 25

    if prev_counter is not None:
        step = abs(candidate - prev_counter)
        if step == 0:
            score -= 3
        elif step <= 2:
            score += 8
        elif step <= 4:
            score += 3
        elif step > 7:
            score -= 8

        if prev_melody is not None:
            melody_dir = melody_midi - prev_melody
            counter_dir = candidate - prev_counter
            if melody_dir * counter_dir < 0:
                score += 5
            elif melody_dir == 0 or counter_dir == 0:
                score += 2
            else:
                score -= 2

            prev_int = abs(prev_melody - prev_counter) % 12
            if (
                prev_int in PERFECT_CONSONANCES
                and interval_semi == prev_int
                and melody_dir != 0
                and counter_dir != 0
            ):
                score -= 30

    return score


def generate_countermelody(score, detected_key, place_below=None):
    """Build a countermelody Part for the given melody and key."""
    elements = list(melody_elements(score))
    melody_notes = [e for e in elements if isinstance(e, note.Note)]
    if not melody_notes:
        return stream.Part()

    if place_below is None:
        avg_midi = sum(n.pitch.midi for n in melody_notes) / len(melody_notes)
        place_below = avg_midi >= 64

    scale_pcs = diatonic_pitch_classes(detected_key)

    counter = stream.Part()
    counter.id = "Countermelody"
    counter.partName = "Countermelody"
    counter.insert(0, instrument.Clarinet() if place_below else instrument.Flute())

    flat = score.flatten()
    for ts in flat.getElementsByClass(meter.TimeSignature):
        counter.insert(0, meter.TimeSignature(ts.ratioString))
        break
    for tm in flat.getElementsByClass(tempo.MetronomeMark):
        counter.insert(0, tempo.MetronomeMark(number=tm.number))
        break

    prev_counter_midi = None
    prev_melody_midi = None

    for el in elements:
        if isinstance(el, note.Rest):
            r = note.Rest()
            r.duration = el.duration
            counter.append(r)
            prev_counter_midi = None
            continue

        melody_midi = el.pitch.midi

        if place_below:
            low, high = melody_midi - 14, melody_midi - 2
        else:
            low, high = melody_midi + 2, melody_midi + 14
        low = max(36, low)
        high = min(88, high)

        candidates = [p for p in range(low, high + 1) if p % 12 in scale_pcs]
        if not candidates:
            fallback = melody_midi - 12 if place_below else melody_midi + 12
            candidates = [fallback]

        best = max(
            candidates,
            key=lambda c: score_candidate(
                c, melody_midi, prev_counter_midi, prev_melody_midi
            ),
        )

        n = note.Note()
        n.pitch.midi = best
        n.duration = el.duration
        counter.append(n)

        prev_counter_midi = best
        prev_melody_midi = melody_midi

    return counter


def build_output_score(original, counter_part):
    """Combine original melody and countermelody into one Score."""
    out = stream.Score()
    if original.metadata:
        out.metadata = original.metadata
    if original.parts:
        out.insert(0, original.parts[0])
    else:
        melody_part = stream.Part()
        melody_part.partName = "Melody"
        for el in original.flatten().notesAndRests:
            melody_part.append(el)
        out.insert(0, melody_part)
    out.insert(0, counter_part)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a countermelody in the same key as the input melody."
    )
    parser.add_argument("input", help="Path to input MIDI file (.mid/.midi)")
    parser.add_argument(
        "-o", "--output",
        default="countermelody_output.mid",
        help="Output MIDI file path (default: countermelody_output.mid)",
    )
    parser.add_argument(
        "--above", action="store_true",
        help="Force countermelody above the melody",
    )
    parser.add_argument(
        "--below", action="store_true",
        help="Force countermelody below the melody",
    )
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        parser.error(f"Input file not found: {in_path}")
    if args.above and args.below:
        parser.error("Choose only one of --above or --below")

    place_below = None
    if args.above:
        place_below = False
    elif args.below:
        place_below = True

    score = converter.parse(str(in_path))
    detected_key = detect_key(score)
    print(f"Detected key: {detected_key}")

    counter = generate_countermelody(score, detected_key, place_below=place_below)
    output = build_output_score(score, counter)

    out_path = Path(args.output)
    output.makeNotation(inPlace=True)
    output.write("midi", fp=str(out_path))
    print(f"Countermelody written to: {out_path}")


if __name__ == "__main__":
    main()
