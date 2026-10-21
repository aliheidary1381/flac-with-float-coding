#!/usr/bin/env python3
import csv
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import scipy.stats as st
import soundfile as sf

# Paths & Settings
BASE_DIR = Path(__file__).parent.resolve()
CORPUS_DIR = BASE_DIR / "corpus"
DB_PATH = BASE_DIR / "benchmark.db"
SCRATCH_DIR = BASE_DIR / "scratch"

# FLAC & Codec Binaries
FLAC_BIN = Path(
    os.path.expanduser("~/Codes/CLionProjects/flac-with-float-coding/flac")
).resolve()
FLAC_DIR = FLAC_BIN.parent
MP4ALS_BIN = FLAC_DIR / "mp4alsRM23"
OFF_BIN = FLAC_DIR / "off"

MAX_WORKERS = os.cpu_count() or 8


def detect_orig_bps(track_path: Path) -> int:
    name = track_path.name.lower()
    if "sqam" in name or "castanets" in name or "orchestra" in name:
        return 16
    if "goldberg" in name or "piano" in name:
        return 24
    if "scientific_" in name:
        return 32

    real_path = track_path.resolve()
    try:
        info = sf.info(str(real_path))
        subtype = info.subtype.upper()
        if "16" in subtype:
            return 16
        if "24" in subtype:
            return 24
        if "32" in subtype or "FLOAT" in subtype:
            return 32
    except Exception:
        pass

    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=bits_per_raw_sample,sample_fmt",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(real_path),
        ]
        out = subprocess.check_output(cmd).decode().split()
        for token in out:
            if token in ("16", "24", "32"):
                return int(token)
            if "s16" in token:
                return 16
            if "s32" in token:
                return 24
    except Exception:
        pass
    return 16


def init_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='results';")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(results);")
        columns = [row[1] for row in cur.fetchall()]
        if "orig_bps" not in columns:
            cur.execute("""
            CREATE TABLE results_new (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                tier TEXT,
                sim_type TEXT,
                orig_bps INTEGER,
                sample_rate INTEGER,
                rate_category TEXT,
                channels INTEGER,
                duration_sec REAL,
                raw_bytes INTEGER,
                codec TEXT,
                preset TEXT,
                compressed_bytes INTEGER,
                compression_ratio REAL,
                encode_time_sec REAL,
                encode_speed_mb_s REAL,
                encode_realtime_x REAL,
                decode_time_sec REAL,
                decode_speed_mb_s REAL,
                decode_realtime_x REAL,
                verified_lossless INTEGER,
                UNIQUE(file_path, sim_type, codec, preset)
            );
            """)
            cur.execute("""
            INSERT INTO results_new (
                id, file_path, tier, sim_type, sample_rate, channels, duration_sec,
                raw_bytes, codec, preset, compressed_bytes, compression_ratio,
                encode_time_sec, encode_speed_mb_s, encode_realtime_x,
                decode_time_sec, decode_speed_mb_s, decode_realtime_x, verified_lossless
            )
            SELECT
                id, file_path, tier, sim_type, sample_rate, channels, duration_sec,
                raw_bytes, codec, preset, compressed_bytes, compression_ratio,
                encode_time_sec, encode_speed_mb_s, encode_realtime_x,
                decode_time_sec, decode_speed_mb_s, decode_realtime_x, verified_lossless
            FROM results;
            """)
            cur.execute("DROP TABLE results;")
            cur.execute("ALTER TABLE results_new RENAME TO results;")
            conn.commit()

            cur.execute(
                "SELECT DISTINCT file_path, sample_rate FROM results WHERE orig_bps IS NULL;"
            )
            for fpath, srate in cur.fetchall():
                p = CORPUS_DIR / fpath
                obps = detect_orig_bps(p)
                rcat = "<=48kHz" if srate <= 48000 else ">48kHz"
                cur.execute(
                    "UPDATE results SET orig_bps = ?, rate_category = ? WHERE file_path = ?;",
                    (obps, rcat, fpath),
                )
            conn.commit()
    else:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            tier TEXT,
            sim_type TEXT,
            orig_bps INTEGER,
            sample_rate INTEGER,
            rate_category TEXT,
            channels INTEGER,
            duration_sec REAL,
            raw_bytes INTEGER,
            codec TEXT,
            preset TEXT,
            compressed_bytes INTEGER,
            compression_ratio REAL,
            encode_time_sec REAL,
            encode_speed_mb_s REAL,
            encode_realtime_x REAL,
            decode_time_sec REAL,
            decode_speed_mb_s REAL,
            decode_realtime_x REAL,
            verified_lossless INTEGER,
            UNIQUE(file_path, sim_type, codec, preset)
        );
        """)
        conn.commit()
    return conn


def get_completed_tasks(db_path: Path):
    if not os.path.exists(db_path):
        return set()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT file_path, sim_type, codec, preset FROM results")
    completed = set(cur.fetchall())
    conn.close()
    return completed


def get_wav_data_payload(filepath: str) -> bytes | None:
    """Robust RIFF parser extracting the pure 'data' chunk byte payload."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return None
            while True:
                chunk_hdr = f.read(8)
                if len(chunk_hdr) < 8:
                    break
                chunk_id = chunk_hdr[0:4]
                chunk_size = struct.unpack("<I", chunk_hdr[4:8])[0]
                if chunk_id == b"data":
                    return f.read(chunk_size)
                f.seek(chunk_size, os.SEEK_CUR)
                if chunk_size % 2 == 1:
                    f.seek(1, os.SEEK_CUR)
    except Exception:
        return None
    return None


def read_audio(track_path: Path) -> tuple[np.ndarray, int, int]:
    """Read any audio input format into 32-bit float NumPy arrays."""
    try:
        data, sample_rate = sf.read(str(track_path), dtype="float32", always_2d=False)
        channels = 1 if data.ndim == 1 else data.shape[1]
        return data, sample_rate, channels
    except Exception:
        probe_cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(track_path),
        ]
        out = subprocess.check_output(probe_cmd).decode().split()
        sample_rate = int(out[0])
        channels = int(out[1])

        dec_cmd = [
            "ffmpeg",
            "-vn",
            "-threads",
            "1",
            "-i",
            str(track_path),
            "-f",
            "f32le",
            "-c:a",
            "pcm_f32le",
            "-v",
            "quiet",
            "pipe:1",
        ]
        raw_pcm = subprocess.check_output(dec_cmd)
        data = np.frombuffer(raw_pcm, dtype=np.float32)
        if channels > 1:
            data = data.reshape(-1, channels)
        return data, sample_rate, channels


def write_float_wav(filepath: str, samples: np.ndarray, sample_rate: int):
    """Write standardized IEEE 32-bit float WAV."""
    sf.write(filepath, samples, sample_rate, subtype="FLOAT", format="WAV")


def simulate_adc(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Vectorized simulation of dual-stage ADC gain ranging."""
    out = samples.copy()
    chunk_frames = sample_rate * 10
    n = len(out)
    rng = random.Random(42)

    for i in range(0, n, chunk_frames):
        if rng.choice([True, False]):
            out[i : min(i + chunk_frames, n)] *= np.float32(32.0)
    return out


def simulate_daw(samples: np.ndarray) -> np.ndarray:
    """Vectorized DAW power-curve waveshaping: sgn(x) * |x|^0.9."""
    return (np.sign(samples) * (np.abs(samples) ** np.float32(0.9))).astype(np.float32)


def build_codec_commands(
    codec_name: str, preset: str, inp: str, comp_file: str, dec_file: str
) -> tuple[list[str], list[str]]:
    """Builds CLI commands deterministically without unpicklable lambdas."""
    flac_variants = {
        "flac_float": FLAC_BIN,
        "flac_fallback": FLAC_BIN,
        "flac_cyclic_shift": FLAC_DIR / "flac_cyclic_shift",
        "flac_dpa_snp": FLAC_DIR / "flac_dpa_snp",
        "flac_raw": FLAC_DIR / "flac_raw",
        "flac_1subframe": FLAC_DIR / "flac_1subframe",
        "flac_bit_manipulation": FLAC_DIR / "flac_bit_manipulation",
    }

    if codec_name in flac_variants:
        bin_path = str(flac_variants[codec_name])
        flags = []
        if "-8" in preset:
            flags.append("-8")
        elif "-5" in preset:
            flags.append("-5")
        if "fallback" in preset:
            flags.append("--force-fallback-mode")

        enc_cmd = [bin_path] + flags + ["--force", inp, "-o", comp_file]
        dec_cmd = [bin_path, "-d", "--force", comp_file, "-o", dec_file]
        return enc_cmd, dec_cmd

    if codec_name == "wavpack":
        if "-hh -x6" in preset:
            enc_cmd = [
                "wavpack",
                "-hh",
                "-x6",
                "--threads=1",
                "-y",
                inp,
                "-o",
                comp_file,
            ]
        else:
            enc_cmd = ["wavpack", "--threads=1", "-y", inp, "-o", comp_file]
        dec_cmd = ["wvunpack", "-y", comp_file, "-o", dec_file]
        return enc_cmd, dec_cmd

    if codec_name == "monkeys_audio":
        enc_cmd = ["mac", inp, comp_file, preset, "-threads=1"]
        dec_cmd = ["mac", comp_file, dec_file, "-d", "-threads=1"]
        return enc_cmd, dec_cmd

    if codec_name == "mp4als":
        bin_path = str(MP4ALS_BIN)
        if "-7 -f5" in preset:
            enc_cmd = [bin_path, "-7", "-f5", inp, comp_file]
        else:
            enc_cmd = [bin_path, inp, comp_file]
        dec_cmd = [bin_path, "-x", comp_file, dec_file]
        return enc_cmd, dec_cmd

    if codec_name == "optimfrog":
        bin_path = str(OFF_BIN)
        if "--mode highnew-light --optimize best" in preset:
            enc_cmd = [
                bin_path,
                "--encode",
                "--mode",
                "highnew-light",
                "--optimize",
                "best",
                inp,
                "--output",
                comp_file,
            ]
        else:
            enc_cmd = [bin_path, "--encode", inp, "--output", comp_file]
        dec_cmd = [bin_path, "--decode", comp_file, "--output", dec_file]
        return enc_cmd, dec_cmd

    raise ValueError(f"Unknown codec configuration: {codec_name} ({preset})")


def get_codec_test_matrix():
    """Returns pure serializable tuples: (codec_name, preset, extension)."""
    return [
        # Main modified FLAC
        ("flac_float", "-8", ".flac"),
        ("flac_fallback", "-8 (fallback)", ".flac"),
        ("flac_float", "-5", ".flac"),
        # Optimal compression baselines
        ("wavpack", "-hh -x6", ".wv"),
        ("monkeys_audio", "-c5000", ".ape"),
        ("mp4als", "-7 -f5", ".als"),
        ("optimfrog", "--mode highnew-light --optimize best", ".ofr"),
        # Throughput baselines
        ("wavpack", "default", ".wv"),
        ("monkeys_audio", "-c2000", ".ape"),
        ("mp4als", "default", ".als"),
        ("optimfrog", "default", ".ofr"),
        # FLAC algorithm ablations
        ("flac_cyclic_shift", "-8", ".flac"),
        ("flac_dpa_snp", "-8", ".flac"),
        ("flac_raw", "-8", ".flac"),
        ("flac_1subframe", "-8", ".flac"),
        ("flac_bit_manipulation", "-8", ".flac"),
    ]


def prepare_track_simulations(track_info):
    """Pre-renders ADC and DAW variations once to scratch disk."""
    tier_label, track_path = track_info
    rel_path = str(track_path.relative_to(CORPUS_DIR))

    try:
        samples, sample_rate, channels = read_audio(track_path)
    except Exception:
        return []

    orig_bps = detect_orig_bps(track_path)
    rate_category = "<=48kHz" if sample_rate <= 48000 else ">48kHz"

    track_hash = f"{rel_path.replace('/', '_')}"
    track_scratch = SCRATCH_DIR / "staged" / track_hash
    track_scratch.mkdir(parents=True, exist_ok=True)

    items = []

    if tier_label in ("Tier 3: Scientific", "Tier 4: Scientific"):
        items.append(
            (
                rel_path,
                tier_label,
                "sci",
                orig_bps,
                sample_rate,
                rate_category,
                channels,
                str(track_path),
            )
        )
        return items

    # Pre-render ADC simulation
    adc_wav = track_scratch / "adc.wav"
    if not adc_wav.exists():
        adc_samples = simulate_adc(samples, sample_rate)
        write_float_wav(str(adc_wav), adc_samples, sample_rate)
    items.append(
        (
            rel_path,
            tier_label,
            "adc",
            orig_bps,
            sample_rate,
            rate_category,
            channels,
            str(adc_wav),
        )
    )

    # Pre-render DAW simulation
    daw_wav = track_scratch / "daw.wav"
    if not daw_wav.exists():
        daw_samples = simulate_daw(samples)
        write_float_wav(str(daw_wav), daw_samples, sample_rate)
    items.append(
        (
            rel_path,
            tier_label,
            "daw",
            orig_bps,
            sample_rate,
            rate_category,
            channels,
            str(daw_wav),
        )
    )

    return items


def run_single_codec_task(task_args):
    """Executes a single (codec, preset) against a single pre-staged WAV file."""
    sim_info, codec_def = task_args
    (
        rel_path,
        tier,
        sim_type,
        orig_bps,
        sample_rate,
        rate_category,
        channels,
        wav_path,
    ) = sim_info
    codec_name, preset, ext = codec_def

    raw_payload = get_wav_data_payload(wav_path)
    if not raw_payload:
        return None

    raw_bytes = len(raw_payload)
    duration = raw_bytes / (sample_rate * channels * 4)

    with tempfile.TemporaryDirectory(dir=SCRATCH_DIR) as tmpdir:
        comp_file = os.path.join(tmpdir, f"out{ext}")
        dec_file = os.path.join(tmpdir, "decoded.wav")

        try:
            enc_cmd, dec_cmd = build_codec_commands(
                codec_name, preset, wav_path, comp_file, dec_file
            )
        except ValueError:
            return None

        # Encode pass
        t0 = time.perf_counter()
        try:
            subprocess.run(
                enc_cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            t_enc = time.perf_counter() - t0
            comp_bytes = os.path.getsize(comp_file)
        except Exception:
            return None

        # Decode pass
        t0 = time.perf_counter()
        try:
            subprocess.run(
                dec_cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            t_dec = time.perf_counter() - t0
        except Exception:
            if os.path.exists(comp_file):
                os.unlink(comp_file)
            return None

        dec_payload = get_wav_data_payload(dec_file)
        lossless = 1 if (dec_payload and dec_payload == raw_payload) else 0

        ratio = comp_bytes / raw_bytes
        raw_mb = raw_bytes / (1024 * 1024)
        enc_speed = raw_mb / t_enc if t_enc > 0 else 0
        dec_speed = raw_mb / t_dec if t_dec > 0 else 0
        enc_rt = duration / t_enc if t_enc > 0 else 0
        dec_rt = duration / t_dec if t_dec > 0 else 0

        if os.path.exists(comp_file):
            os.unlink(comp_file)
        if os.path.exists(dec_file):
            os.unlink(dec_file)

        return (
            rel_path,
            tier,
            sim_type,
            orig_bps,
            sample_rate,
            rate_category,
            channels,
            duration,
            raw_bytes,
            codec_name,
            preset,
            comp_bytes,
            ratio,
            t_enc,
            enc_speed,
            enc_rt,
            t_dec,
            dec_speed,
            dec_rt,
            lossless,
        )


def compute_summary_stats(values):
    """Compute mean, min, max, count, and 90% confidence interval margin."""
    if not values:
        return 0.0, 0.0, 0.0, 0, 0.0
    mean_val = float(np.mean(values))
    min_val = float(np.min(values))
    max_val = float(np.max(values))
    n = len(values)
    if n > 1 and st is not None:
        se = float(st.sem(values))
        margin = float(st.t.interval(0.90, n - 1, loc=0.0, scale=se)[1])
    else:
        margin = 0.0
    return mean_val, min_val, max_val, n, margin


def compute_paired_stats(a_vals, b_vals):
    """Compute paired difference stats: mean difference, 90% CI, t-statistic, p-value."""
    if not a_vals or not b_vals or len(a_vals) != len(b_vals):
        return 0.0, 0.0, 0.0, 0.0, 0.0, 1.0
    diffs = [a - b for a, b in zip(a_vals, b_vals)]
    n = len(diffs)
    diff_mean = float(np.mean(diffs))
    if n > 1 and st is not None:
        diff_se = float(st.sem(diffs))
        ci_low, ci_high = st.t.interval(0.90, n - 1, loc=diff_mean, scale=diff_se)
        diff_ci_margin = float((ci_high - ci_low) / 2.0)
        t_res = st.ttest_rel(a_vals, b_vals)
        t_stat = float(t_res.statistic)
        p_val = float(t_res.pvalue)
    else:
        diff_ci_margin = 0.0
        ci_low, ci_high = diff_mean, diff_mean
        t_stat = 0.0
        p_val = 1.0
    return diff_mean, diff_ci_margin, float(ci_low), float(ci_high), t_stat, p_val


def write_csv_files(filename: str, rows: list):
    """Save CSV to paper/ directory (and local scripts/ directory)."""
    paper_dir = BASE_DIR.parent / "paper"
    if paper_dir.exists():
        with open(paper_dir / filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
    dest_path = BASE_DIR / filename
    with open(dest_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def export_csv_tables(db_path=DB_PATH):
    """Export SQLite metrics directly into CSV files for pgfplotstable with 90% CI and paired t-tests."""
    if not os.path.exists(db_path):
        print("Database does not exist yet. Run benchmark first.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    strata_comp = [
        ("Tier 1: Canonical", "16-bit PCM", "<=48kHz", "adc", "Dual-Stage ADC"),
        ("Tier 1: Canonical", "16-bit PCM", "<=48kHz", "daw", "Complex DAW"),
        ("Tier 1: Canonical", "16-bit PCM", ">48kHz", "adc", "Dual-Stage ADC"),
        ("Tier 1: Canonical", "16-bit PCM", ">48kHz", "daw", "Complex DAW"),
        ("Tier 1: Canonical", "24-bit PCM", ">48kHz", "adc", "Dual-Stage ADC"),
        ("Tier 1: Canonical", "24-bit PCM", ">48kHz", "daw", "Complex DAW"),
        (
            "Tier 2: Stratified Music",
            "16-bit PCM",
            "<=48kHz",
            "adc",
            "Dual-Stage ADC",
        ),
        (
            "Tier 2: Stratified Music",
            "16-bit PCM",
            "<=48kHz",
            "daw",
            "Complex DAW",
        ),
        (
            "Tier 2: Stratified Music",
            "24-bit PCM",
            "<=48kHz",
            "adc",
            "Dual-Stage ADC",
        ),
        (
            "Tier 2: Stratified Music",
            "24-bit PCM",
            "<=48kHz",
            "daw",
            "Complex DAW",
        ),
        (
            "Tier 2: Stratified Music",
            "24-bit PCM",
            ">48kHz",
            "adc",
            "Dual-Stage ADC",
        ),
        (
            "Tier 2: Stratified Music",
            "24-bit PCM",
            ">48kHz",
            "daw",
            "Complex DAW",
        ),
    ]

    # -------------------------------------------------------------------------
    # 1. table_compression.csv
    # -------------------------------------------------------------------------
    cur.execute("""
    SELECT
      file_path,
      tier,
      orig_bps || '-bit' AS format,
      rate_category,
      sim_type,
      codec,
      compression_ratio * 100
    FROM results
    WHERE preset IN ('-8', '-hh -x6', '-c5000', '-7 -f5', '--mode highnew-light --optimize best')
    ORDER BY file_path, codec;
    """)
    raw_comp = cur.fetchall()

    grouped_comp = {}
    tier2_comp = {}
    for fp, tier, fmt, rate, sim, codec, ratio in raw_comp:
        key = (tier, fmt, rate, sim)
        grouped_comp.setdefault(key, {}).setdefault(fp, {})[codec] = ratio
        if tier == "Tier 2: Stratified Music":
            tier2_comp.setdefault(sim, {}).setdefault(fp, {})[codec] = ratio

    comp_rows = [
        [
            "Tier",
            "Format",
            "Rate",
            "Simulation",
            "FLAC",
            "FLAC_CI90_Margin",
            "WavPack",
            "Monkeys",
            "MP4ALS",
            "OptimFROG",
            "Diff_vs_WavPack",
            "Diff_CI90_Margin",
            "Diff_CI90_Low",
            "Diff_CI90_High",
            "t_stat",
            "p_val",
            "FLAC_Min",
            "FLAC_Max",
        ]
    ]

    for tier, fmt_label, rate, sim, sim_label in strata_comp:
        fmt_key = "16-bit" if "16-bit" in fmt_label else "24-bit"
        files = grouped_comp.get((tier, fmt_key, rate, sim), {})
        flac_vals = [m["flac_float"] for m in files.values() if "flac_float" in m]
        wv_vals = [m["wavpack"] for m in files.values() if "wavpack" in m]
        mac_vals = [m["monkeys_audio"] for m in files.values() if "monkeys_audio" in m]
        als_vals = [m["mp4als"] for m in files.values() if "mp4als" in m]
        ofr_vals = [m["optimfrog"] for m in files.values() if "optimfrog" in m]

        flac_m, flac_min, flac_max, n, flac_ci_m = compute_summary_stats(flac_vals)
        wv_m = float(np.mean(wv_vals)) if wv_vals else 0.0
        mac_m = float(np.mean(mac_vals)) if mac_vals else 0.0
        als_m = float(np.mean(als_vals)) if als_vals else 0.0
        ofr_m = float(np.mean(ofr_vals)) if ofr_vals else 0.0

        pairs = [
            (m["flac_float"], m["wavpack"])
            for m in files.values()
            if "flac_float" in m and "wavpack" in m
        ]
        diff_m, diff_ci_m, diff_low, diff_high, t_stat, p_val = compute_paired_stats(
            [p[0] for p in pairs], [p[1] for p in pairs]
        )

        comp_rows.append(
            [
                f"{{{tier}}}",
                f"{{{fmt_label}}}",
                f"{{{rate}}}",
                f"{{{sim_label}}}",
                f"{flac_m:.2f}",
                f"{flac_ci_m:.2f}",
                f"{wv_m:.2f}",
                f"{mac_m:.2f}",
                f"{als_m:.2f}",
                f"{ofr_m:.2f}",
                f"{diff_m:+.2f}",
                f"{diff_ci_m:.2f}",
                f"{diff_low:+.2f}",
                f"{diff_high:+.2f}",
                f"{t_stat:+.2f}",
                f"{p_val:.4e}",
                f"{flac_min:.2f}",
                f"{flac_max:.2f}",
            ]
        )

    for sim, sim_label in [
        ("adc", "Dual-Stage ADC"),
        ("daw", "Complex DAW"),
    ]:
        t2_files = tier2_comp.get(sim, {})
        flac_vals = [m["flac_float"] for m in t2_files.values() if "flac_float" in m]
        wv_vals = [m["wavpack"] for m in t2_files.values() if "wavpack" in m]
        mac_vals = [
            m["monkeys_audio"] for m in t2_files.values() if "monkeys_audio" in m
        ]
        als_vals = [m["mp4als"] for m in t2_files.values() if "mp4als" in m]
        ofr_vals = [m["optimfrog"] for m in t2_files.values() if "optimfrog" in m]

        flac_m, flac_min, flac_max, n, flac_ci_m = compute_summary_stats(flac_vals)
        wv_m = float(np.mean(wv_vals)) if wv_vals else 0.0
        mac_m = float(np.mean(mac_vals)) if mac_vals else 0.0
        als_m = float(np.mean(als_vals)) if als_vals else 0.0
        ofr_m = float(np.mean(ofr_vals)) if ofr_vals else 0.0

        pairs = [
            (m["flac_float"], m["wavpack"])
            for m in t2_files.values()
            if "flac_float" in m and "wavpack" in m
        ]
        diff_m, diff_ci_m, diff_low, diff_high, t_stat, p_val = compute_paired_stats(
            [p[0] for p in pairs], [p[1] for p in pairs]
        )

        comp_rows.append(
            [
                "{Tier 2: Stratified Music}",
                "{Tier 2 Macro-Mean}",
                "{All Rates}",
                f"{{{sim_label}}}",
                f"{flac_m:.2f}",
                f"{flac_ci_m:.2f}",
                f"{wv_m:.2f}",
                f"{mac_m:.2f}",
                f"{als_m:.2f}",
                f"{ofr_m:.2f}",
                f"{diff_m:+.2f}",
                f"{diff_ci_m:.2f}",
                f"{diff_low:+.2f}",
                f"{diff_high:+.2f}",
                f"{t_stat:+.2f}",
                f"{p_val:.4e}",
                f"{flac_min:.2f}",
                f"{flac_max:.2f}",
            ]
        )

    write_csv_files("table_compression.csv", comp_rows)

    # -------------------------------------------------------------------------
    # 2. table_ablation.csv
    # -------------------------------------------------------------------------
    cur.execute("""
    SELECT
      file_path,
      tier,
      orig_bps || '-bit' AS format,
      rate_category,
      sim_type,
      codec,
      compression_ratio * 100
    FROM results
    WHERE preset IN ('-8', '-8 (fallback)')
    ORDER BY file_path, codec;
    """)
    raw_abl = cur.fetchall()

    grouped_abl = {}
    tier2_abl = {}
    for fp, tier, fmt, rate, sim, codec, ratio in raw_abl:
        key = (tier, fmt, rate, sim)
        grouped_abl.setdefault(key, {}).setdefault(fp, {})[codec] = ratio
        if tier == "Tier 2: Stratified Music":
            tier2_abl.setdefault(sim, {}).setdefault(fp, {})[codec] = ratio

    ablation_columns = [
        ("flac_fallback", "Fallback"),
        ("flac_raw", "Raw"),
        ("flac_1subframe", "OneSubframe"),
        ("flac_cyclic_shift", "CyclicShift"),
        ("flac_bit_manipulation", "BitManip"),
        ("flac_dpa_snp", "DPA_SNP"),
        ("flac_float", "DPA"),
    ]

    abl_rows = [
        ["Tier", "Format", "Rate", "Simulation"]
        + [label for _, label in ablation_columns]
        + ["DPA_CI90_Margin"]
    ]

    for tier, fmt_label, rate, sim, sim_label in strata_comp:
        fmt_key = "16-bit" if "16-bit" in fmt_label else "24-bit"
        files = grouped_abl.get((tier, fmt_key, rate, sim), {})
        row = [
            f"{{{tier}}}",
            f"{{{fmt_label}}}",
            f"{{{rate}}}",
            f"{{{sim_label}}}",
        ]
        val_map = {}
        for codec_key, _ in ablation_columns:
            vals = [m[codec_key] for m in files.values() if codec_key in m]
            val = float(np.mean(vals)) if vals else 0.0
            val_map[codec_key] = val
            row.append(f"{val:.2f}")

        dpa_vals = [m["flac_float"] for m in files.values() if "flac_float" in m]
        _, _, _, _, dpa_ci_m = compute_summary_stats(dpa_vals)
        row.append(f"{dpa_ci_m:.2f}")
        abl_rows.append(row)

    for sim, sim_label in [
        ("adc", "Dual-Stage ADC"),
        ("daw", "Complex DAW"),
    ]:
        t2_files = tier2_abl.get(sim, {})
        row = [
            "{Tier 2: Stratified Music}",
            "{Tier 2 Macro-Mean}",
            "{All Rates}",
            f"{{{sim_label}}}",
        ]
        for codec_key, _ in ablation_columns:
            vals = [m[codec_key] for m in t2_files.values() if codec_key in m]
            val = float(np.mean(vals)) if vals else 0.0
            row.append(f"{val:.2f}")

        dpa_vals = [m["flac_float"] for m in t2_files.values() if "flac_float" in m]
        _, _, _, _, dpa_ci_m = compute_summary_stats(dpa_vals)
        row.append(f"{dpa_ci_m:.2f}")
        abl_rows.append(row)

    write_csv_files("table_ablation.csv", abl_rows)

    # -------------------------------------------------------------------------
    # 3. table_throughput.csv
    # -------------------------------------------------------------------------
    cur.execute("""
    SELECT
      sim_type,
      codec,
      preset,
      ROUND(AVG(encode_realtime_x), 1) AS enc_x,
      ROUND(AVG(decode_realtime_x), 1) AS dec_x
    FROM results
    WHERE preset IN ('-5', 'default', '-c2000')
    GROUP BY sim_type, codec, preset
    ORDER BY sim_type, enc_x DESC;
    """)
    tp_data = cur.fetchall()

    tp_rows = [["Simulation", "Codec", "EncSpeed", "DecSpeed"]]
    for sim, codec, preset, enc_x, dec_x in tp_data:
        sim_label = "Dual-Stage ADC" if sim == "adc" else "Complex DAW Master"
        if codec == "flac_float":
            codec_name = "FLAC Float (Ours, -5)"
        elif codec == "wavpack":
            codec_name = "WavPack (Default)"
        elif codec == "monkeys_audio":
            codec_name = "Monkey's Audio (-c2000)"
        elif codec == "mp4als":
            codec_name = "MPEG-4 ALS (Default)"
        elif codec == "optimfrog":
            codec_name = "OptimFROG (Default)"
        else:
            codec_name = f"{codec} ({preset})"

        tp_rows.append(
            [
                f"{{{sim_label}}}",
                f"{{{codec_name}}}",
                f"{enc_x:.1f}",
                f"{dec_x:.1f}",
            ]
        )
    write_csv_files("table_throughput.csv", tp_rows)

    # -------------------------------------------------------------------------
    # 4. table_scientific.csv
    # -------------------------------------------------------------------------
    cur.execute("""
    SELECT
        file_path,
        codec,
        preset,
        ROUND(compression_ratio * 100, 2),
        verified_lossless
    FROM results
    WHERE sim_type = 'sci' AND preset IN ('-8', '-8 (fallback)', '-hh -x6', '-c5000', '-7 -f5', '--mode highnew-light --optimize best')
    ORDER BY file_path, codec;
    """)

    sci_rows = cur.fetchall()
    sci_data = {}
    tracks = set()
    for fpath, codec, preset, ratio, lossless in sci_rows:
        track_name = Path(fpath).stem.replace("scientific_", "")
        tracks.add(track_name)
        key = codec if codec != "flac_fallback" else "flac_fallback"
        sci_data[(track_name, key)] = (ratio, lossless)

    sci_csv_rows = [
        [
            "Signal",
            "FLAC_DPA",
            "FLAC_Fallback",
            "WavPack",
            "Monkeys",
            "MP4ALS",
            "OptimFROG",
            "Diff_vs_WavPack",
            "Diff_CI90_Margin",
            "Diff_CI90_Low",
            "Diff_CI90_High",
            "t_stat",
            "p_val",
        ]
    ]

    col_keys = [
        "flac_float",
        "flac_fallback",
        "wavpack",
        "monkeys_audio",
        "mp4als",
        "optimfrog",
    ]

    sorted_tracks = sorted(list(tracks))
    flac_list = []
    wv_list = []
    for trk in sorted_tracks:
        row = [f"{{{trk}}}"]
        for ck in col_keys:
            val, ok = sci_data.get((trk, ck), (0.0, 0))
            val_str = f"{val:.2f}" if ok == 1 else "FAILED"
            row.append(val_str)

        flac_val = sci_data.get((trk, "flac_float"), (0.0, 0))[0]
        wv_val = sci_data.get((trk, "wavpack"), (0.0, 0))[0]
        flac_list.append(flac_val)
        wv_list.append(wv_val)
        diff_val = flac_val - wv_val
        row.extend(
            [
                f"{diff_val:+.2f}",
                "0.00",
                f"{diff_val:+.2f}",
                f"{diff_val:+.2f}",
                "0.00",
                "1.0000e+00",
            ]
        )
        sci_csv_rows.append(row)

    cur.execute("""
    SELECT
        codec,
        ROUND(AVG(compression_ratio) * 100, 2)
    FROM results
    WHERE sim_type = 'sci' AND verified_lossless = 1
        AND preset IN ('-8', '-8 (fallback)', '-hh -x6', '-c5000', '-7 -f5', '--mode highnew-light --optimize best')
    GROUP BY codec;
    """)
    mean_map = dict(cur.fetchall())

    diff_m, diff_ci_m, diff_low, diff_high, t_stat, p_val = compute_paired_stats(
        flac_list, wv_list
    )
    macro_row = [
        "{Macro-Mean}",
        f"{mean_map.get('flac_float', 0.0):.2f}",
        f"{mean_map.get('flac_fallback', 0.0):.2f}",
        f"{mean_map.get('wavpack', 0.0):.2f}",
        f"{mean_map.get('monkeys_audio', 0.0):.2f}",
        f"{mean_map.get('mp4als', 0.0):.2f}",
        f"{mean_map.get('optimfrog', 0.0):.2f}",
        f"{diff_m:+.2f}",
        f"{diff_ci_m:.2f}",
        f"{diff_low:+.2f}",
        f"{diff_high:+.2f}",
        f"{t_stat:+.2f}",
        f"{p_val:.4e}",
    ]
    sci_csv_rows.append(macro_row)

    write_csv_files("table_scientific.csv", sci_csv_rows)
    conn.close()
    print(
        "Successfully generated pgfplotstable CSV files with 90% CI & paired t-tests."
    )


def run_parallel_benchmark():
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db(DB_PATH)
    completed_set = get_completed_tasks(DB_PATH)

    tier_dirs = [
        ("Tier 1: Canonical", CORPUS_DIR / "tier1_canonical"),
        ("Tier 2: Stratified Music", CORPUS_DIR / "tier2_stratified"),
        ("Tier 3: Scientific", CORPUS_DIR / "tier3_scientific"),
    ]

    all_tracks = []
    for tier_label, path in tier_dirs:
        if not path.exists():
            continue
        files = sorted(list(path.glob("*.wav")) + list(path.glob("*.flac")))
        for f in files:
            all_tracks.append((tier_label, f))

    all_tracks.sort(key=lambda t: t[1].stat().st_size, reverse=True)
    total_tracks = len(all_tracks)
    print(f"1. Pre-staging {total_tracks} tracks into scratch directory...")

    staged_simulations = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as stage_pool:
        for items in stage_pool.map(prepare_track_simulations, all_tracks):
            staged_simulations.extend(items)

    codec_matrix = get_codec_test_matrix()
    print(
        f"2. Beginning isolated benchmark across {len(codec_matrix)} codec configurations."
    )

    try:
        # OUTER LOOP: Benchmark exactly one codec configuration at a time
        for codec_def in codec_matrix:
            codec_name, preset, _ = codec_def

            tasks = []
            for sim in staged_simulations:
                rel_path, sim_type = sim[0], sim[2]
                if (rel_path, sim_type, codec_name, preset) not in completed_set:
                    tasks.append((sim, codec_def))

            if not tasks:
                print(f"[{codec_name} {preset}] All tasks already completed. Skipping.")
                continue

            print(
                f"\n>>> Running [{codec_name} | {preset}] across {len(tasks)} tasks on {MAX_WORKERS} cores..."
            )

            # INNER POOL: Saturated on current codec exclusively
            with ProcessPoolExecutor(max_workers=MAX_WORKERS) as codec_pool:
                futures = {
                    codec_pool.submit(run_single_codec_task, task): task
                    for task in tasks
                }
                for future in as_completed(futures):
                    res = future.result()
                    if res:
                        cur = conn.cursor()
                        cur.execute(
                            """
                        INSERT OR REPLACE INTO results (
                            file_path, tier, sim_type, orig_bps, sample_rate, rate_category,
                            channels, duration_sec, raw_bytes, codec, preset, compressed_bytes,
                            compression_ratio, encode_time_sec, encode_speed_mb_s, encode_realtime_x,
                            decode_time_sec, decode_speed_mb_s, decode_realtime_x, verified_lossless
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            res,
                        )
                        conn.commit()

            print(f">>> Finished [{codec_name} | {preset}]. Synchronized.")

    except KeyboardInterrupt:
        print("\nBenchmark interrupted. Cleaning up scratch space...")
        shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
        sys.exit(0)

    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
    conn.close()

    export_csv_tables(DB_PATH)
    print("\nBenchmark complete and tables exported to CSV!")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        export_csv_tables()
    else:
        run_parallel_benchmark()
