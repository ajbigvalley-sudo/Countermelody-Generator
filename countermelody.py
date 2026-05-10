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
from dataclasses import dataclass
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

    chord_tone_score: float = 10        # candidate pitch is in the active chord
    chord_clash_score: float = -12      # candidate is a semitone away from a chord tone
    non_chord_tone_score: float = -2    # diatonic but not in the active chord


STYLES: dict[str, Style] = {
    "harmonic": Style(
        name="harmonic",
        description="Traditional parallel 3rds/6ths harmony, chord-locked.",
        imperfect_consonance=22,
        perfect_consonance=0,
        contrary_score=1,
        oblique_score=2,
        parallel_score=4,
        step_score=10,
        third_score=4,
        chord_tone_score=14,
        chord_clash_score=-15,
        non_chord_tone_score=-4,
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
        chord_tone_score=8,
        chord_clash_score=-12,
        non_chord_tone_score=-2,
    ),
    "stepwise": Style(
        name="stepwise",
        description="Smooth, mostly-conjunct line; tolerates passing tones.",
        imperfect_consonance=14,
        perfect_consonance=4,
        step_score=20,
        third_score=2,
        big_leap_score=-25,
        repeat_score=-1,
        contrary_score=4,
        chord_tone_score=5,
        chord_clash_score=-8,
        non_chord_tone_score=0,
    ),
    "arpeggiated": Style(
        name="arpeggiated",
        description="Leap-friendly, triadic line, heavily chord-locked.",
        imperfect_consonance=8,
        perfect_consonance=14,
        p4_score=-2,
        step_score=2,
        third_score=10,
        big_leap_score=2,
        repeat_score=-8,
        contrary_score=3,
        chord_tone_score=16,
        chord_clash_score=-12,
        non_chord_tone_score=-6,
    ),
}


def detect_key(score):
    return score.analyze("key")


def diatonic_pitch_classes(detected_key):
    sc = detected_key.getScale()
    return {p.pitchClass for p in sc.getPitches()}


def melody_elements_with_offsets(score, part_index=0):
    """Yield (element, absolute_offset) pairs from the chosen part."""
    if score.parts:
        source = score.parts[part_index].flatten()
    else:
        source = score.flatten()
    for el in source.notesAndRests:
        if isinstance(el, note.Note):
            yield el, float(el.offset)
        elif isinstance(el, note.Rest):
            yield el, float(el.offset)
        elif hasattr(el, "pitches") and el.pitches:
            top = max(el.pitches, key=lambda p: p.midi)
            n = note.Note()
            n.pitch = top
            n.duration = el.duration
            yield n, float(el.offset)


def melody_elements(score, part_index=0):
    """Yield melody notes and rests (no offsets) for backward compatibility."""
    for el, _off in melody_elements_with_offsets(score, part_index):
        yield el


# Diatonic triad scale-degree intervals (semitones from tonic).
# Major key uses natural diatonic; minor uses natural minor here.
MAJOR_TRIADS = [(0, 4, 7), (2, 5, 9), (4, 7, 11), (5, 9, 0),
                (7, 11, 2), (9, 0, 4), (11, 2, 5)]
MINOR_TRIADS = [(0, 3, 7), (2, 5, 8), (3, 7, 10), (5, 8, 0),
                (7, 10, 2), (8, 0, 3), (10, 2, 5)]
# Functional preference bonus: I, IV, V most common
FUNCTION_BONUS = [0.6, 0.0, 0.0, 0.4, 0.5, 0.1, -0.2]


def _diatonic_triads(detected_key):
    """Return list of pitch-class sets for the seven diatonic triads."""
    tonic_pc = detected_key.tonic.pitchClass
    is_minor = detected_key.mode == "minor"
    rel = MINOR_TRIADS if is_minor else MAJOR_TRIADS
    return [set((s + tonic_pc) % 12 for s in tri) for tri in rel]


def _read_harmony_from_score(score, query_offsets, melody_part_index=0):
    """For each query offset, return the pitch-class set sounding in
    OTHER parts at that moment. Returns None if no other parts exist."""
    if not score.parts or len(score.parts) <= 1:
        return None

    other = stream.Score()
    for i, p in enumerate(score.parts):
        if i != melody_part_index:
            other.insert(0, p)

    if not list(other.flatten().notes):
        return None

    chordified = other.chordify()
    chord_events = []
    for ch in chordified.flatten().notes:
        start = float(ch.offset)
        end = start + float(ch.quarterLength)
        pcs = {p.pitchClass for p in ch.pitches}
        chord_events.append((start, end, pcs))

    contexts = []
    for off in query_offsets:
        active = set()
        for start, end, pcs in chord_events:
            if start <= off < end:
                active = pcs
                break
        contexts.append(active)
    return contexts


def _infer_chords_from_melody(melody_data, detected_key, beats_per_chord=2.0):
    """Infer a diatonic chord per melody note by sliding a beat-window.

    melody_data: list of (offset, midi_pitch) for note onsets only.
    Returns one pitch-class set per item in melody_data.
    """
    if not melody_data:
        return []

    triads = _diatonic_triads(detected_key)
    contexts = []
    for off, _midi in melody_data:
        window_start = (off // beats_per_chord) * beats_per_chord
        window_end = window_start + beats_per_chord
        window_pcs = [m % 12 for o, m in melody_data
                      if window_start <= o < window_end]

        def triad_score(idx):
            tri = triads[idx]
            hits = sum(1 for pc in window_pcs if pc in tri)
            return hits + FUNCTION_BONUS[idx]

        best_idx = max(range(len(triads)), key=triad_score)
        contexts.append(triads[best_idx])
    return contexts


def compute_chord_contexts(score, melody_pairs, detected_key, melody_part_index=0):
    """Return one chord pitch-class set per melody element.

    melody_pairs: list of (element, offset) for ALL melody elements (notes+rests).
    Reads harmony from accompanying parts when present; otherwise infers
    chords from the melody itself. Always returns a list aligned with
    melody_pairs.
    """
    note_data = [(off, el.pitch.midi) for el, off in melody_pairs
                 if isinstance(el, note.Note)]
    note_offsets = [d[0] for d in note_data]

    harmony = _read_harmony_from_score(score, note_offsets, melody_part_index)
    if harmony is None or not any(harmony):
        note_contexts = _infer_chords_from_melody(note_data, detected_key)
        source = "inferred"
    else:
        inferred = _infer_chords_from_melody(note_data, detected_key)
        note_contexts = [h if h else i for h, i in zip(harmony, inferred)]
        source = "read"

    # Map back to full melody_pairs (notes get a chord, rests get empty set)
    full = []
    note_iter = iter(note_contexts)
    for el, _off in melody_pairs:
        if isinstance(el, note.Note):
            full.append(next(note_iter))
        else:
            full.append(set())
    return full, source


def score_candidate(candidate, melody_midi, prev_counter, prev_melody,
                    style, chord_pcs=None):
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

    if chord_pcs:
        cand_pc = candidate % 12
        if cand_pc in chord_pcs:
            score += style.chord_tone_score
        else:
            score += style.non_chord_tone_score
            for pc in chord_pcs:
                semitone_dist = min((cand_pc - pc) % 12, (pc - cand_pc) % 12)
                if semitone_dist == 1:
                    score += style.chord_clash_score
                    break

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
    """Build a countermelody Part for the given melody and key under a style.

    Returns (counter_part, chord_source) where chord_source is 'read',
    'inferred', or 'none'.
    """
    melody_pairs = list(melody_elements_with_offsets(score))
    melody_notes = [el for el, _ in melody_pairs if isinstance(el, note.Note)]
    if not melody_notes:
        return stream.Part(), "none"

    if place_below is None:
        avg_midi = sum(n.pitch.midi for n in melody_notes) / len(melody_notes)
        place_below = avg_midi >= 64

    scale_pcs = diatonic_pitch_classes(detected_key)
    chord_contexts, chord_source = compute_chord_contexts(
        score, melody_pairs, detected_key
    )

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

    for (el, _off), chord_pcs in zip(melody_pairs, chord_contexts):
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
                c, melody_midi, prev_counter_midi, prev_melody_midi,
                style, chord_pcs,
            ),
        )

        n = note.Note()
        n.pitch.midi = best
        n.duration = el.duration
        counter.append(n)

        prev_counter_midi = best
        prev_melody_midi = melody_midi

    return counter, chord_source


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

    chord_source_label = {"read": "read from input", "inferred": "inferred from melody",
                          "none": "(no chord context)"}
    first = True
    for style_name in style_names:
        style = STYLES[style_name]
        counter, chord_source = generate_countermelody(
            score, detected_key, style, place_below=place_below
        )
        if first:
            print(f"Chord context: {chord_source_label.get(chord_source, chord_source)}")
            first = False
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
