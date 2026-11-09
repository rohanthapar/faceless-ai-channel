"""
scripts/generate_music.py — Synthesize original, copyright-free background tracks.

Every track is generated from scratch (additive synthesis in numpy), so there is
no Content ID / copyright-claim risk. Tracks are deliberately simple and sit
low under narration: soft pads, a plucked arpeggio, sub bass and optional light
drums.

Usage:  python scripts/generate_music.py            # writes assets/music/*.mp3
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "music"

NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def freq(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((NOTE[name] + 12 * (octave + 1) - 69) / 12)


def chord(root: str, minor: bool, octave: int = 3) -> list[float]:
    r = NOTE[root] + 12 * (octave + 1)
    third = 3 if minor else 4
    return [440.0 * 2 ** ((m - 69) / 12) for m in (r, r + third, r + 7, r + 12)]


def pad(freqs: list[float], dur: float, bright: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for f in freqs:
        for detune in (-0.12, 0.0, 0.12):  # chorus-like width
            ff = f * 2 ** (detune / 12)
            for h in range(1, 6):  # soft saw: few harmonics, falling fast
                out += np.sin(2 * np.pi * ff * h * t) * (bright ** (h - 1)) / h
    env = np.minimum(1, t / 0.8) * np.minimum(1, (dur - t) / 0.8)
    return out * env / (len(freqs) * 6)


def pluck(f: float, dur: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return tone * np.exp(-t * 6) * np.minimum(1, t / 0.005)


def kick(dur: float = 0.35) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    f = 50 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def hat(rng: np.random.Generator, dur: float = 0.05) -> np.ndarray:
    n = rng.standard_normal(int(dur * SR))
    n = np.diff(n, prepend=0)  # crude high-pass
    return n * np.exp(-np.arange(len(n)) / SR * 70) * 0.25


def add(buf: np.ndarray, sig: np.ndarray, start: float, gain: float) -> None:
    i = int(start * SR)
    j = min(len(buf), i + len(sig))
    if i < len(buf):
        buf[i:j] += sig[: j - i] * gain


def echo(x: np.ndarray, delay: float, feedback: float, mix: float) -> np.ndarray:
    d = int(delay * SR)
    y = x.copy()
    for k in range(1, 5):
        y[d * k:] += x[: len(x) - d * k] * (feedback ** k) * mix
    return y


def render(spec: dict, seconds: float = 64.0) -> np.ndarray:
    rng = np.random.default_rng(spec["seed"])
    bpm, prog = spec["bpm"], spec["progression"]
    beat = 60 / bpm
    bar = beat * 4
    n = int(seconds * SR)
    left, right = np.zeros(n), np.zeros(n)

    t, bar_i = 0.0, 0
    while t < seconds:
        root, minor = prog[bar_i % len(prog)]
        notes = chord(root, minor, spec["octave"])
        p = pad(notes, bar + 0.8, spec["bright"])
        add(left, p, t, 0.55)
        add(right, p, t, 0.55)

        bass = pluck(notes[0] / 2, bar) * 0.9
        add(left, bass, t, 0.5)
        add(right, bass, t, 0.5)

        if spec["arp"]:
            pattern = [0, 2, 1, 3, 2, 1, 3, 2]
            for k in range(8):
                f = notes[pattern[k]] * 2
                pan = 0.5 + 0.35 * np.sin(k)
                pl = pluck(f, beat)
                add(left, pl, t + k * beat / 2, 0.22 * (1 - pan) * 2)
                add(right, pl, t + k * beat / 2, 0.22 * pan * 2)

        if spec["drums"] and bar_i >= 1:  # drums enter after one bar
            for b in range(4):
                if b in (0, 2):
                    add(left, kick(), t + b * beat, 0.6)
                    add(right, kick(), t + b * beat, 0.6)
                for half in (0.5,):
                    h = hat(rng)
                    add(left, h, t + (b + half) * beat, 0.5)
                    add(right, h, t + (b + half) * beat, 0.4)
        t += bar
        bar_i += 1

    left = echo(left, beat * 0.75, 0.45, 0.35)
    right = echo(right, beat * 0.5, 0.45, 0.35)
    stereo = np.stack([left, right], axis=1)

    fade = int(2 * SR)
    stereo[:fade] *= np.linspace(0, 1, fade)[:, None]
    stereo[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return stereo / np.max(np.abs(stereo)) * 0.89


TRACKS = [
    {"name": "curious_pulse", "bpm": 108, "octave": 3, "bright": 0.55, "arp": True, "drums": True, "seed": 1,
     "progression": [("A", True), ("F", False), ("C", False), ("G", False)]},
    {"name": "deep_space", "bpm": 72, "octave": 3, "bright": 0.4, "arp": True, "drums": False, "seed": 2,
     "progression": [("D", True), ("A#", False), ("F", False), ("C", False)]},
    {"name": "discovery", "bpm": 116, "octave": 3, "bright": 0.6, "arp": True, "drums": True, "seed": 3,
     "progression": [("C", False), ("G", False), ("A", True), ("F", False)]},
    {"name": "ancient_echoes", "bpm": 84, "octave": 3, "bright": 0.45, "arp": True, "drums": False, "seed": 4,
     "progression": [("E", True), ("C", False), ("D", False), ("B", True)]},
    {"name": "mystery_tension", "bpm": 96, "octave": 2, "bright": 0.5, "arp": True, "drums": True, "seed": 5,
     "progression": [("F#", True), ("D", False), ("A", False), ("E", False)]},
    {"name": "wonder_drift", "bpm": 90, "octave": 3, "bright": 0.5, "arp": True, "drums": False, "seed": 6,
     "progression": [("G", False), ("D", False), ("E", True), ("C", False)]},
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in TRACKS:
        audio = render(spec)
        pcm = (audio * 32767).astype("<i2").tobytes()
        out = OUT_DIR / f"{spec['name']}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "-",
             "-c:a", "libmp3lame", "-b:a", "192k", str(out)],
            input=pcm, check=True,
        )
        print(f"✅ {out.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
