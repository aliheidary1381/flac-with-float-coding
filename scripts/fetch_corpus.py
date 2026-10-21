#!/usr/bin/env python3
import math
import os
import random
import shutil
import struct
import subprocess
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CORPUS_DIR = BASE_DIR / "corpus"
TIER1_DIR = CORPUS_DIR / "tier1_canonical"
TIER2_DIR = CORPUS_DIR / "tier2_stratified"
TIER3_DIR = CORPUS_DIR / "tier3_scientific"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    )
}

# The 40 selected tracks for Tier 2 (Stratified Music)
TIER2_TRACKS = [
    "/home/ali/Music/Blues/Whipping Post.flac",
    "/home/ali/Music/Blues/None Of Us Are Free.flac",
    "/home/ali/Music/Blues/House Of The Rising Sun.flac",
    "/home/ali/Music/Blues/Cortez the Killer.flac",
    "/home/ali/Music/Blues/Jimi Hendrix/Voodoo Child (Slight Return) (Live At Woodstock).flac",
    "/home/ali/Music/Blues/The Beatles/11 - Let It Be/Across The Universe (2021 Mix).flac",
    "/home/ali/Music/Blues/The Beatles/10 - Abbey Road/Because (2019 Mix).flac",
    "/home/ali/Music/Electronic/Breakcore, Jungle, Witch house/Anybody Can Find Love (except you.).flac",
    "/home/ali/Music/Electronic/Chiptune, 8-bit/UNDERTALE/Ruins.flac",
    "/home/ali/Music/Electronic/Downtempo, Electronica, Trip hop/Moby/Extreme Ways.flac",
    "/home/ali/Music/Electronic/Downtempo, Electronica, Trip hop/Runaway.flac",
    "/home/ali/Music/Electronic/Hip-hop, Rap, Trap/rockstar.flac",
    "/home/ali/Music/Electronic/Hip-hop, Rap, Trap/Feel Good Inc (featuring De La Soul).flac",
    "/home/ali/Music/Electronic/New age/Vangelis/Titles (From Chariots Of Fire).flac",
    "/home/ali/Music/Electronic/Synth-pop, Electropop, Ambient, Space/Paris.flac",
    "/home/ali/Music/Electronic/Techno, House/Aleph.flac",
    "/home/ali/Music/Electronic/Techno, House/Daft Punk/Something About Us (Love Theme from Interstella 5555).flac",
    "/home/ali/Music/Funk, Philly/Super Freak (Album Version).flac",
    "/home/ali/Music/Funk, Philly/Disco, Hi-NRG, Euro/Donna Summer/I Feel Love.flac",
    "/home/ali/Music/Jazz/Miles Davis/Kind of Blue/So What.flac",
    "/home/ali/Music/Jazz/Miles Davis/Kind of Blue/Flamenco Sketches.flac",
    "/home/ali/Music/Jazz/John Coltrane/A Love Supreme/Part 1 - Acknowledgement.flac",
    "/home/ali/Music/Jazz/Dave Brubeck/Time Out/Take Five.flac",
    "/home/ali/Music/Jazz/Bill Evans/Portrait in Jazz/Autumn Leaves.flac",
    "/home/ali/Music/Jazz/Smooth/Weather Report/Heavy Weather/Birdland.flac",
    "/home/ali/Music/Jazz/Smooth/George Benson/Breezin'/Breezin'.flac",
    "/home/ali/Music/Jazz/Fusion/Return to Forever/Romantic Warrior/Medieval Overture.flac",
    "/home/ali/Music/Jazz/Fusion/Mahavishnu Orchestra/The Inner Mounting Flame/Meeting of the Spirits.flac",
    "/home/ali/Music/Pop/Abba/Gold Greatest Hits/Dancing Queen.flac",
    "/home/ali/Music/Pop/Michael Jackson/Thriller/Billie Jean.flac",
    "/home/ali/Music/Pop/Madonna/The Immaculate Collection/Like a Prayer.flac",
    "/home/ali/Music/Pop/Fleetwood Mac/Rumours/Dreams.flac",
    "/home/ali/Music/Pop/Prince/Purple Rain/When Doves Cry.flac",
    "/home/ali/Music/Progressive Rock, Art Rock/Pink Floyd/The Dark Side of the Moon/Money.flac",
    "/home/ali/Music/Progressive Rock, Art Rock/King Crimson/In the Court of the Crimson King/21st Century Schizoid Man.flac",
    "/home/ali/Music/Progressive Rock, Art Rock/Rush/Moving Pictures/Tom Sawyer.flac",
    "/home/ali/Music/Rock/Led Zeppelin/Led Zeppelin IV/Stairway to Heaven.flac",
    "/home/ali/Music/Rock/Queen/A Night at the Opera/Bohemian Rhapsody.flac",
    "/home/ali/Music/Rock/The Rolling Stones/Sticky Fingers/Brown Sugar.flac",
    "/home/ali/Music/Rock/Nirvana/Nevermind/Smells Like Teen Spirit.flac",
]


def write_float_wav(filepath: Path, data_bytes: bytes, sr: int, channels: int):
    n_samples = len(data_bytes) // (4 * channels)
    byte_rate = sr * channels * 4
    block_align = channels * 4

    # Build Header: RIFF (44) + data
    header = bytearray()
    header.extend(b"RIFF")
    header.extend(struct.pack("<I", 36 + len(data_bytes)))
    header.extend(b"WAVE")
    header.extend(b"fmt ")
    header.extend(struct.pack("<I", 16))
    header.extend(struct.pack("<H", 3))  # IEEE Float
    header.extend(struct.pack("<H", channels))
    header.extend(struct.pack("<I", sr))
    header.extend(struct.pack("<I", byte_rate))
    header.extend(struct.pack("<H", block_align))
    header.extend(struct.pack("<H", 32))  # Bits per sample
    header.extend(b"data")
    header.extend(struct.pack("<I", len(data_bytes)))

    with open(filepath, "wb") as f:
        f.write(header)
        f.write(data_bytes)


def setup_tier1():
    print("\n[1/3] Setting up Tier 1: Canonical Public Benchmark Sets...")
    TIER1_DIR.mkdir(parents=True, exist_ok=True)

    # 1. EBU SQAM (Tech 3253)
    existing_sqam = list(TIER1_DIR.glob("sqam_*.wav"))
    if len(existing_sqam) < 70:
        print("  Downloading EBU SQAM archive (Tech 3253)...")
        sqam_zip = BASE_DIR / "sqam.zip"
        url = "https://tech.ebu.ch/files/live/sites/tech/files/shared/testmaterial/sqam.zip"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req) as resp, open(sqam_zip, "wb") as out:
                shutil.copyfileobj(resp, out)
            print("  Extracting SQAM tracks...")
            with zipfile.ZipFile(sqam_zip, "r") as z:
                for member in z.namelist():
                    if member.lower().endswith(".wav"):
                        filename = Path(member).name
                        z.extract(member, TIER1_DIR)
                        extracted = TIER1_DIR / member
                        dest = TIER1_DIR / f"sqam_{filename}"
                        if extracted.is_file():
                            shutil.move(extracted, dest)
            sqam_zip.unlink(missing_ok=True)
            for d in list(TIER1_DIR.iterdir()):
                if d.is_dir():
                    shutil.rmtree(d)
        except Exception as e:
            print(f"  [WARN] Failed to fetch SQAM: {e}")
    else:
        print(f"  Tier 1 SQAM already prepared ({len(existing_sqam)} tracks).")

    # 2. Open Goldberg Variations (Kimiko Ishizaka, 24-bit Studio Masters)
    existing_ogv = list(TIER1_DIR.glob("goldberg_*.wav"))
    if len(existing_ogv) < 30:
        print("  Downloading Open Goldberg Variations (24-bit Studio Masters)...")
        ogv_zip = BASE_DIR / "goldberg.zip"
        url = "https://archive.org/download/OpenGoldbergVariations-24bitWav/OpenGoldbergVariations-24bitWav_archive.zip"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req) as resp, open(ogv_zip, "wb") as out:
                shutil.copyfileobj(resp, out)
            print("  Extracting Goldberg Variations...")
            with zipfile.ZipFile(ogv_zip, "r") as z:
                for member in z.namelist():
                    if member.lower().endswith(".wav"):
                        filename = Path(member).name
                        z.extract(member, TIER1_DIR)
                        extracted = TIER1_DIR / member
                        dest = TIER1_DIR / f"goldberg_{filename}"
                        if extracted.is_file():
                            shutil.move(extracted, dest)
            ogv_zip.unlink(missing_ok=True)
            for d in list(TIER1_DIR.iterdir()):
                if d.is_dir():
                    shutil.rmtree(d)
        except Exception as e:
            print(f"  [WARN] Failed to fetch Goldberg Variations: {e}")
    else:
        print(
            "  Tier 1 Goldberg Variations already prepared"
            f" ({len(existing_ogv)} tracks)."
        )


def setup_tier2():
    print("\n[2/3] Linking Tier 2: Selected Real-World Music Tracks...")
    TIER2_DIR.mkdir(parents=True, exist_ok=True)

    # Clean existing links
    for item in TIER2_DIR.glob("*"):
        if item.is_symlink() or item.is_file():
            item.unlink()

    linked = 0
    missing = 0
    for path_str in TIER2_TRACKS:
        src = Path(path_str)
        if not src.exists():
            print(f"  [MISSING] {src}")
            missing += 1
            continue

        parts = src.parts
        genre = parts[4] if len(parts) > 4 else "Music"
        link_name = f"{genre}_{src.stem}.flac".replace(" ", "_").replace(",", "")
        dest = TIER2_DIR / link_name
        dest.symlink_to(src)
        linked += 1

    print(f"  Successfully linked {linked} tracks into Tier 2 (Missing: {missing}).")


def setup_tier3():
    print("\n[3/3] Synthesizing Tier 3: Scientific & Pathological Dynamic Range...")
    TIER3_DIR.mkdir(parents=True, exist_ok=True)
    sr = 44100
    dur = 5.0
    n_samples = int(sr * dur)
    frame_size = 4096

    # 1. Seismic Shock / Exponential Ringdown (Decays 48 powers-of-two per frame)
    seismic_path = TIER3_DIR / "scientific_seismic_ringdown.wav"
    if not seismic_path.exists():
        print("   Generating seismic exponential ringdowns (48 octaves/frame)...")
        samples = []
        rng = random.Random(42)
        decay_factor = math.pow(2.0, -48.0 / frame_size)
        w = 2.0 * math.pi * 440.0 / sr

        for i in range(n_samples):
            frame_idx = i % frame_size
            amp = math.pow(decay_factor, frame_idx)
            val = amp * (0.8 * math.sin(w * i) + 0.2 * rng.uniform(-1.0, 1.0))
            samples.append(val)

        data = struct.pack(f"<{n_samples}f", *samples)
        write_float_wav(seismic_path, data, sr, 1)

    # 2. Monotonic Exponent Staircase (64 blocks of 64 samples = 4096, exp 0 down to -63)
    staircase_path = TIER3_DIR / "scientific_exponent_staircase.wav"
    if not staircase_path.exists():
        print("   Generating wide-range exponent staircase (64 octaves/frame)...")
        samples = []
        for i in range(n_samples):
            frame_idx = i % frame_size
            block_idx = frame_idx // 64
            exp_unbiased = -(block_idx % 64)
            scale = math.ldexp(1.0, exp_unbiased)
            mantissa = 0.5 + 0.4 * math.sin(2.0 * math.pi * i / 64.0)
            samples.append(scale * mantissa)

        data = struct.pack(f"<{n_samples}f", *samples)
        write_float_wav(staircase_path, data, sr, 1)

    # 3. Sensor Telemetry Dropout with Identical Sentinel NaNs (50% missing data)
    dropout_path = TIER3_DIR / "scientific_sensor_dropout_nans.wav"
    if not dropout_path.exists():
        print("   Generating sensor telemetry with 50% sentinel NaNs...")
        samples_bytes = bytearray()
        w = 2.0 * math.pi * 220.0 / sr
        canonical_nan_bytes = struct.pack("<I", 0x7FC00000)

        for i in range(n_samples):
            if i % 2 == 0:
                val = 0.75 * math.sin(w * i)
                samples_bytes.extend(struct.pack("<f", val))
            else:
                samples_bytes.extend(canonical_nan_bytes)

        write_float_wav(dropout_path, samples_bytes, sr, 1)


if __name__ == "__main__":
    setup_tier1()
    setup_tier2()
    setup_tier3()
    print("\nCorpus setup complete! Run benchmark.py next.")
