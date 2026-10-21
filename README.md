# FLAC extension

This is the main page for the proposed changes on FLAC.

The implementation is a fork of the reference xiph/flac. You can find the code
[in the `float-sample-coding` branch](https://github.com/aliheidary1381/flac-with-float-coding/tree/float-sample-coding).

The proposed changes to the standard can be found in the [`internet-draft/`](internet-draft/) directory:

- [Markdown (`draft-heydari-flac-extensions-00.md`)](internet-draft/draft-heydari-flac-extensions-00.md)
- [xml2rfc v3 XML (`draft-heydari-flac-extensions-00.xml`)](internet-draft/draft-heydari-flac-extensions-00.xml)
- [Formatted plain text (`draft-heydari-flac-extensions-00.txt`)](internet-draft/draft-heydari-flac-extensions-00.txt)
- [HTML (`draft-heydari-flac-extensions-00.html`)](internet-draft/draft-heydari-flac-extensions-00.html)
- [PDF (`draft-heydari-flac-extensions-00.pdf`)](internet-draft/draft-heydari-flac-extensions-00.pdf)

The benchmarks SQLite database ([`scripts/benchmark.db`](scripts/benchmark.db)), a short paper [preprint PDF](paper/main.pdf)
explaining the design decisions, reproducible setup instructions, and licensing details are available below.

## 8 Bits to End the Loudness War: Lossless Floating-Point Compression for Audio Streams

This repository contains the complete experimental suite, benchmarking pipeline, corpus management scripts, Internet-Draft specification, and LaTeX manuscript for the paper:

> **8 Bits to End the Loudness War: Lossless Floating-Point Compression for Audio Streams**  
> Reference Implementation & Source: [flac-with-float-coding (`float-sample-coding` branch)](https://github.com/aliheidary1381/flac-with-float-coding/tree/float-sample-coding)

The compiled paper PDF is available in this repository as [`paper/main.pdf`](paper/main.pdf).

---

### Environment Setup

This project uses [direnv](https://direnv.net/) and [Nix](https://nixos.org/) for declarative, reproducible dependency management via [`.envrc`](.envrc) and [`shell.nix`](shell.nix).

If you have `direnv` and Nix installed:

```bash
direnv allow
```

Alternatively, enter the nix-shell directly:

```bash
nix-shell
```

This environment provides:

- Python 3 with `numpy`, `scipy`, and `soundfile`
- `ffmpeg` & `ffprobe`
- `wavpack`
- `monkeys-audio` (`mac`)
- `sqlite3`
- Full `texlive` distribution (`xelatex`, `biber`, `fontawesome5`, `pgfplots`, etc.)
- `mmark` (v2 markdown processor for RFCs)
- `xml2rfc` & `weasyprint` (for text, HTML, and PDF document generation)

#### Proprietary / Unfree Codecs

For evaluating proprietary codecs referenced in the paper:

- **OptimFROG (`off`)**
- **MPEG-4 ALS Reference Software (`mp4alsRM23`)**

You must download the binaries yourself according to their respective licenses and place them where [`scripts/benchmark.py`](scripts/benchmark.py) expects them (or update the binary paths in `scripts/benchmark.py`).

---

### Repository Structure & Workflow

```text
├── internet-draft/          # IETF Internet-Draft source (Markdown) and rendered formats
│   ├── draft-heydari-flac-extensions-00.md
│   ├── draft-heydari-flac-extensions-00.xml
│   ├── draft-heydari-flac-extensions-00.html
│   ├── draft-heydari-flac-extensions-00.txt
│   ├── draft-heydari-flac-extensions-00.pdf
│   └── LICENSE              # IETF Trust provisions (trust200902 / BCP 78)
├── paper/                   # LaTeX manuscript, TikZ plots, tables, and compiled PDF
│   ├── main.tex
│   ├── main.pdf
│   ├── references.bib
│   ├── track*.dat           # Waveform data points for pgfplots
│   ├── table_*.csv          # Exported summary tables
│   └── LICENSE              # Academic manuscript draft license (All rights reserved)
├── scripts/                 # Benchmarking pipeline, corpus tools, and SQLite database
│   ├── benchmark.db         # SQLite benchmark database
│   ├── benchmark.py         # Benchmarking pipeline & CSV export script
│   ├── extract_waveforms.py # Nirvana waveform extractor
│   ├── fetch_corpus.py      # Corpus downloader and synthesizer
│   └── corpus/              # Evaluation corpus (organized into tiers)
│       ├── tier1_canonical/
│       ├── tier2_stratified/
│       └── tier3_scientific/
├── shell.nix                # Declarative Nix environment definition
├── LICENSE                  # BSD 3-Clause License (code & scripts)
└── README.md
```

#### 1. Exporting Internet-Draft Formats (mmark & xml2rfc)

The Internet-Draft specification is authored in mmark markdown (`internet-draft/draft-heydari-flac-extensions-00.md`).

To convert the markdown source to xml2rfc v3 XML, and then compile into plain text, HTML, and PDF:

```bash
cd internet-draft

# Step 1: Render Markdown to xml2rfc v3 XML
mmark draft-heydari-flac-extensions-00.md > draft-heydari-flac-extensions-00.xml

# Step 2: Render XML to text, HTML, and PDF using xml2rfc
xml2rfc draft-heydari-flac-extensions-00.xml --text --html --pdf
```

Output files will be generated in `internet-draft/`:

- `draft-heydari-flac-extensions-00.xml`
- `draft-heydari-flac-extensions-00.txt`
- `draft-heydari-flac-extensions-00.html`
- `draft-heydari-flac-extensions-00.pdf`

#### 2. Compiling the Paper

The paper is authored in LaTeX and compiled inside the `paper/` directory using `xelatex` and `biber`:

```bash
cd paper
xelatex main.tex
biber main
xelatex main.tex
```

#### 3. Extracting Nirvana Waveforms

To extract and process the dynamic loudness comparison waveforms (1991 Original CD, 2011 Remaster, and 2021 Atmos / Audiophile Master) for TikZ/pgfplots visualization in the paper:

```bash
cd scripts
python3 extract_waveforms.py
```

This generates `track1.dat`, `track1_poly.dat`, `track2.dat`, `track2_poly.dat`, `track3.dat`, and `track3_poly.dat` in both `scripts/` and `paper/`.

#### 4. Fetching the Evaluation Corpus

The test corpus consists of canonical sets (EBU SQAM, Open Goldberg Variations), stratified commercial music recordings across multiple genres and sampling rates, and high-precision scientific measurement data. To download and construct the full corpus:

```bash
cd scripts
python3 fetch_corpus.py
```

Tracks are organized under the `scripts/corpus/` directory:

- `tier1_canonical/`: EBU SQAM (70 tracks, 16-bit / 44.1 kHz) and Open Goldberg Variations (31 tracks, 24-bit studio masters).
- `tier2_stratified/`: 40 diverse production tracks across various musical genres and sampling rates.
- `tier3_scientific/`: IEEE 754 float time-series signals and sensor data (wide-range exponent staircases, exponential seismic decay, and sentinel NaN dropouts).

#### 5. Benchmarking & Statistical Reporting

To export the formatted tables with 90% confidence intervals and paired difference t-tests directly from [`scripts/benchmark.db`](scripts/benchmark.db) to CSV without re-running the full benchmark:

```bash
cd scripts
python3 benchmark.py export
```

Exported tables are written directly to `paper/` (for LaTeX compilation) and `scripts/`:

- `table_compression.csv` (includes 90% CI for FLAC and paired difference t-test vs. WavPack)
- `table_ablation.csv` (includes 90% CI for DPA)
- `table_throughput.csv`
- `table_scientific.csv` (includes paired differences vs. WavPack)

To run the complete benchmark suite across all codecs and corpus tiers from scratch:

```bash
cd scripts
python3 benchmark.py
```

---

### Licensing Notice

This repository contains distinct components governed by different licenses to address specific legal, standard-track, and academic publication requirements:

- **Software, Benchmarking Pipeline & Tools ([`LICENSE`](LICENSE))**:  
  All benchmarking scripts, corpus retrieval tools, waveform extraction code, and nix build environment files located in [`scripts/`](scripts/) and repository root are licensed under the **BSD 3-Clause License**.
- **Academic Manuscript Draft ([`paper/LICENSE`](paper/LICENSE))**:  
  The academic paper manuscript, LaTeX source files, TikZ graphics, and compiled preprint PDF located in [`paper/`](paper/) are **Copyright &copy; 2026 Ali Heydari. All rights reserved.** Permission is granted to view, download, and review the draft for personal, evaluation, and peer-review purposes. Redistribution, reproduction, or re-hosting in whole or in part without prior written permission is prohibited.
- **IETF Internet-Draft ([`internet-draft/LICENSE`](internet-draft/LICENSE))**:  
  The specification documents in [`internet-draft/`](internet-draft/) are submitted as an Internet-Draft to the Internet Engineering Task Force (IETF) and are subject to **BCP 78** and the **IETF Trust Legal Provisions (trust200902)**. Code components extracted from the draft are licensed under the **Revised BSD (3-Clause) License** as described in Section 4.e of the IETF Trust Legal Provisions.
