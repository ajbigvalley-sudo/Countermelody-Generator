#!/usr/bin/env python3
"""Generate countermelodies in the same key as an input melody.

Reads a MIDI file containing a melody, detects its key using
Krumhansl-Schmuckler key analysis, and writes one or more new MIDI
files with the original melody plus a generated countermelody voice.

Multiple named "styles" produce distinct countermelodies for variety:
    harmonic     - traditional parallel 3rds/6ths harmony part
    contrary     - independent line favoring contrary motion
    stepwise     - smooth, mostly-conjunct line
    arpeggiated  - leap-friendly, triadic line emphasizing 5ths/octaves

All styles stay diatonic to the detected key and avoid parallel 5ths/8ves.
"""

import argparse
import sys
from dataclasses import dataclass, field
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


@dataclass
class Style:
    """Tunable weights that shape a countermelody's character."""
    name: str
    description: str

    imperfect_consonance: float = 15
    perfect_consonance: float = 5
    p4_score: float = -5
    dissonance_score: float = -25

    repeat_score: float = -3
    step_score: float = 8        # 1-2 semitones in counter line
    third_score: float = 3       # 3-4 semitones
    big_leap_score: float = -8   # > 7 semitones

    contrary_score: float = 5
    oblique_score: float = 2
    parallel_score: float = -2
    parallel_perfect_score: float = -30


STYLES: dict[str, Style] = {
    "harmonic": Style(
        name="harmonic",
        description="Traditional parallel 3rds/6ths harmony.",
        imperfect_consonance=22,
        perfect_consonance=0,
        contrary_score=1,
        oblique_score=2,
        parallel_score=4,
        step_score=10,
        third_score=4,
    ),
    "contrary": Style(
        name="contrary",
        description="Independent line favoring contrary motion.",
        imperfect_consonance=12,
        perfect_consonance=6,
        contrary_score=15,
        oblique_score=4,
        parallel_score=-8,
        step_score=6,
    ),
    "stepwise": Style(
        name="stepwise",
        description="Smooth, mostly-conjunct line.",
        imperfect_consonance=14,
        perfect_consonance=4,
        step_score=20,
        third_score=2,
        big_leap_score=-25,
        repeat_score=-1,
        contrary_score=4,
    ),
    "arpeggiated": Style(
        name="arpeggiated",
        description="Leap-friendly, triadic line.",
        imperfect_consonance=8,
        perfect_consonance=14,
        p4_score=-2,
        step_score=2,
        third_score=10,
        big_leap_score=2,
        repeat_score=-8,
        contrary_score=3,
    ),
}


def detect_key(score):
    return score.analyze("key")


def diatonic_pitch_classes(detected_key):
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


def score_candidate(candidate, melody_midi, prev_counter, prev_melody, style):
    """Heuristic score for a candidate countermelody pitch under a style."""
    score = 0
    interval_semi = abs(candidate - melody_midi) % 12

    if interval_semi in IMPERFECT_CONSONANCES:
        score += style.imperfect_consonance
    elif interval_semi in PERFECT_CONSONANCES:
        score += style.perfect_consonance
    elif interval_semi in DISSONANT_P4:
        score += style.p4_score
    else:
        score += style.dissonance_score

    if prev_counter is not None:
        step = abs(candidate - prev_counter)
        if step == 0:
            score += style.repeat_score
        elif step <= 2:
            score += style.step_score
        elif step <= 4:
            score += style.third_score
        elif step > 7:
            score += style.big_leap_score

        if prev_melody is not None:
            melody_dir = melody_midi - prev_melody
            counter_dir = candidate - prev_counter
            if melody_dir * counter_dir < 0:
                score += style.contrary_score
            elif melody_dir == 0 or counter_dir == 0:
                score += style.oblique_score
            else:
                score += style.parallel_score

            prev_int = abs(prev_melody - prev_counter) % 12
            if (
                prev_int in PERFECT_CONSONANCES
                and interval_semi == prev_int
                and melody_dir != 0
                and counter_dir != 0
            ):
                score += style.parallel_perfect_score

    return score


def generate_countermelody(score, detected_key, style, place_below=None):
    """Build a countermelody Part for the given melody and key under a style."""
    elements = list(melody_elements(score))
    melody_notes = [e for e in elements if isinstance(e, note.Note)]
    if not melody_notes:
        return stream.Part()

    if place_below is None:
        avg_midi = sum(n.pitch.midi for n in melody_notes) / len(melody_notes)
        place_below = avg_midi >= 64

    scale_pcs = diatonic_pitch_classes(detected_key)

    counter = stream.Part()
    counter.id = f"Countermelody-{style.name}"
    counter.partName = f"Countermelody ({style.name})"
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
                c, melody_midi, prev_counter_midi, prev_melody_midi, style,
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


def output_path_for_style(base: Path, style_name: str) -> Path:
    """Insert a style suffix before the extension: out.mid -> out_harmonic.mid"""
    return base.with_name(f"{base.stem}_{style_name}{base.suffix}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate countermelodies in the same key as the input melody.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Available styles:\n"
               + "\n".join(f"  {s.name:<12} {s.description}" for s in STYLES.values()),
    )
    parser.add_argument("input", help="Path to input MIDI file (.mid/.midi)")
    parser.add_argument(
        "-o", "--output",
        default="countermelody_output.mid",
        help="Output MIDI file path (default: countermelody_output.mid). "
             "When --all-variants is used, the style name is inserted before the extension.",
    )
    parser.add_argument(
        "--style", choices=list(STYLES), default="harmonic",
        help="Countermelody style to generate (default: harmonic).",
    )
    parser.add_argument(
        "--all-variants", action="store_true",
        help="Generate one MIDI file per style for variety.",
    )
    parser.add_argument(
        "--above", action="store_true",
        help="Force countermelody above the melody.",
    )
    parser.add_argument(
        "--below", action="store_true",
        help="Force countermelody below the melody.",
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

    base_out = Path(args.output)
    style_names = list(STYLES) if args.all_variants else [args.style]

    for style_name in style_names:
        style = STYLES[style_name]
        counter = generate_countermelody(
            score, detected_key, style, place_below=place_below
        )
        output = build_output_score(score, counter)
        out_path = (
            output_path_for_style(base_out, style_name)
            if args.all_variants else base_out
        )
        output.makeNotation(inPlace=True)
        output.write("midi", fp=str(out_path))
        print(f"  [{style_name:<12}] {out_path}")


if __name__ == "__main__":
    main()
