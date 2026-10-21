#!/usr/bin/env python3
import array
import math
import subprocess
import sys
from pathlib import Path

TRACKS = [
    ("12 Something In the Way.flac", "track1"),
    ("Something In The Way (Album Version).flac", "track2"),
    ("Nirvana - Something In The Way (Remastered 2021).flac", "track3"),
]

N_POINTS = 1280


def extract_track(filename, prefix):
    print(f"Processing: {filename} ...")

    probe_cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "show_entries",
        "stream=sample_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        filename,
    ]
    try:
        sample_rate = int(subprocess.check_output(probe_cmd).decode().strip())
    except Exception:
        sample_rate = 44100

    cmd = [
        "ffmpeg",
        "-i",
        filename,
        "-ac",
        "1",
        "-f",
        "f32le",
        "-v",
        "quiet",
        "pipe:1",
    ]
    raw_bytes = subprocess.check_output(cmd)

    samples = array.array("f")
    samples.frombytes(raw_bytes)
    total_samples = len(samples)

    if total_samples == 0:
        sys.exit(f"Error reading {filename}")

    duration = total_samples / sample_rate
    bucket_size = total_samples / N_POINTS

    times, peaks, db_values = [], [], []

    for i in range(N_POINTS):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size) if i < N_POINTS - 1 else total_samples

        peak = 0.0
        for s in samples[start:end]:
            a = abs(s)
            peak = max(peak, a)

        # Strict clamp to digital full scale (1.0)
        peak = min(1.0, peak)

        db = 20.0 * math.log10(peak) if peak > 1e-5 else -80.0
        db_clamped = max(-24.0, min(0.0, db))

        t = (i / (N_POINTS - 1)) * duration
        times.append(t)
        peaks.append(peak)
        db_values.append(db_clamped)

    paper_dir = Path(__file__).parent.parent / "paper"
    out_dirs = [paper_dir, Path(".")] if paper_dir.exists() else [Path(".")]

    for out_d in set(out_dirs):
        with open(out_d / f"{prefix}.dat", "w") as f:
            f.write("t top bottom db\n")
            for t, p, db in zip(times, peaks, db_values):
                f.write(f"{t:.2f} {p:.5f} {-p:.5f} {db:.2f}\n")

        with open(out_d / f"{prefix}_poly.dat", "w") as f:
            f.write("t y\n")
            for t, p in zip(times, peaks):
                f.write(f"{t:.2f} {p:.5f}\n")
            for t, p in reversed(list(zip(times, peaks))):
                f.write(f"{t:.2f} {-p:.5f}\n")

    print(f"  -> Generated {prefix}.dat and {prefix}_poly.dat")


if __name__ == "__main__":
    for fname, pfx in TRACKS:
        extract_track(fname, pfx)
