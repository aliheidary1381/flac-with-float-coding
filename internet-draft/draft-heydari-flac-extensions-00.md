%%%
title = "Proposed Extensions to the FLAC Standard"
abbrev = "FLAC Extensions"
ipr = "trust200902"
area = "General"
workgroup = "Network Working Group"
submissiontype = "IETF"
keyword = ["FLAC", "floating-point", "audio coding", "lossless compression", "Rice coding"]

[seriesInfo]
name = "Internet-Draft"
value = "draft-heydari-flac-extensions-00"
stream = "IETF"
status = "standard"

[[author]]
initials = "A."
surname = "Heydari"
fullname = "Ali Heydari"
organization = "Independent"
  [author.address]
  email = "aliheidary1381@gmail.com"
%%%

.# Abstract

This document specifies extensions to the Free Lossless Audio Codec (FLAC) format defined in RFC 9639 [@!RFC9639]. These additions specify:

1. A new metadata block (`STREAMINFO_EXTENSION`, block type 7) supporting 64-bit IEEE 754 double-precision floating-point sample rates, up to 256 audio channels, standard `WAVEFORMATEXTENSIBLE` channel masks, extended bit depths up to 128 bits per sample, and explicit sample format indicators (integer PCM and IEEE 754 32-bit floating-point PCM).
2. An extended frame header operating in either Compact (8-bit payload) or Full (128-bit payload) mode, accompanied by deliberate backward-incompatibility signaling in legacy frame header fields.
3. An extended entropy coding method, `PARTITIONED_RICE3` (type code `0b10`), employing 7-bit Rice parameters and 7-bit raw escape bit lengths to accommodate residuals of ultra-high bit depths (up to 128 bits per sample).
4. A native IEEE 754 single-precision (32-bit) floating-point sample coding architecture utilizing dual subframes per audio channel, encompassing Dynamic Positional Alignment (DPA) and two's complement Fallback modes.
5. A new subframe type, `PACKED` (type code `0b000010`), providing adaptive-width bit-packed storage for lower-order mantissa bits and raw float outlier samples.

{mainmatter}

# Conventions and Requirements Language {#conventions}

The key words "**MUST**", "**MUST NOT**", "**REQUIRED**", "**SHALL**", "**SHALL NOT**", "**SHOULD**", "**SHOULD NOT**", "**RECOMMENDED**", "**NOT RECOMMENDED**", "**MAY**", and "**OPTIONAL**" in this document are to be interpreted as described in BCP 14 [@!RFC2119] [@!RFC8174] when, and only when, they appear in all capitals, as shown here.

# Deliberate Incompatibility and Signaling Strategy {#deliberate-incompatibility}

Because extended streams may contain floating-point audio, non-integer sample rates, channel configurations up to 256 channels, or extended subframe payloads, feeding such streams to legacy RFC 9639 decoders would produce severe acoustic damage or decoder crashes.

This specification adopts a two-tier deliberate breaking strategy:

1. **Stream-Level Incompatibility**: The `STREAMINFO` block signals the presence of `STREAMINFO_EXTENSION` by setting its `bits_per_sample` field to `0b00000` (representing 1 bit per sample). In [@!RFC9639], bit depths below 4 bits per sample are reserved and illegal. Compliant [@!RFC9639] decoders **MUST** refuse to decode the stream upon encountering this value.
2. **Frame-Level Incompatibility**: The legacy frame header fields for sample rate, channel assignment, and bit depth are simultaneously set to previously forbidden or reserved codes (`sample_rate = 0b1111`, `channel_assignment = 0b1111`, `bits_per_sample = 0b011`). Furthermore, the frame header extension bytes are inserted immediately before the frame header CRC-8, causing legacy decoders seeking a CRC-8 at the legacy offset to encounter bit-shifted data and fail validation immediately.

# The STREAMINFO_EXTENSION Metadata Block {#streaminfo-extension}

## Block Identification and Placement {#block-id-placement}

The `STREAMINFO_EXTENSION` metadata block is identified by block type indicator `7` in the 7-bit metadata block header:

| Bits    | Field Name               | Description                                           |
| :------ | :----------------------- | :---------------------------------------------------- |
| `0`     | Last-Metadata-Block Flag | `1` if this is the last metadata block, `0` otherwise |
| `1..7`  | Block Type               | `0b0000111` (7): `STREAMINFO_EXTENSION` block         |
| `8..31` | Length                   | `16` (`0x000010`): Payload length in bytes (128 bits) |
Table: STREAMINFO_EXTENSION Block Header {#tbl-streaminfo-ext-header}

If present in a FLAC stream, the `STREAMINFO_EXTENSION` block **MUST** immediately follow the `STREAMINFO` block (i.e., at metadata block index 1). A stream **MUST NOT** contain more than one `STREAMINFO_EXTENSION` block.

## Signaling in STREAMINFO {#signaling-streaminfo}

The presence of the `STREAMINFO_EXTENSION` block **MUST** be indicated in the preceding `STREAMINFO` block by encoding the 5-bit `bits_per_sample` field as `0b00000` (meaning 1 bit per sample).

If the channel count is higher than `STREAMINFO`'s capacity (greater than 8 channels), `STREAMINFO` **MUST** store `0b000` bits (representing 1 channel). Otherwise, `STREAMINFO` and `STREAMINFO_EXTENSION` **MUST** both store the same channel count.

`STREAMINFO`'s sample rate **MUST** be a rounded integer version of the `STREAMINFO_EXTENSION` sample rate (capped at 1,048,575 = 2^20 - 1 for higher rates).

## Block Syntax and Bit Layout {#block-syntax-bit-layout}

The payload of the `STREAMINFO_EXTENSION` block is exactly 128 bits (16 octets) in length:

| Bit Range  | Field Name              | Description                                                                          |
| :--------- | :---------------------- | :----------------------------------------------------------------------------------- |
| `0..63`    | Sample Rate             | 64 bits representing sample rate in Hz in IEEE 754 binary64 format                   |
| `64..71`   | Channel Count Minus 1   | 8 bits for `(number of channels) - 1` (values 0..255 for 1..256 channels)            |
| `72..103`  | Channel Mask            | 32 bits for standard `WAVEFORMATEXTENSIBLE` channel mask ([@!RFC9639] Section 8.6.2) |
| `104..106` | Special Mask Hints      | 3 bits reserved for channel bitmap use case hints; **MUST** be `0b000`               |
| `107..108` | Ignore Mask Flag        | 2 bits indicating whether to ignore channel mask; **MUST** be `0b00`                 |
| `109..112` | Sample Format           | 4 bits: `0b0000` = integer PCM, `0b0001` = float PCM ([@!IEEE754] single precision)  |
| `113..119` | Bits Per Sample Minus 1 | 7 bits for `(bit depth) - 1` (values 0..127 for 1..128 bits per sample)              |
| `120..127` | Reserved                | 8 bits reserved for future development; **MUST** be `0b00000000`                     |
Table: STREAMINFO_EXTENSION Payload Bit Layout {#tbl-streaminfo-ext-payload}

## Field Definitions {#field-definitions}

1. **Sample Rate** (64 bits): Sample rate in Hz represented in the [@!IEEE754] standard binary64 format (`double`).
   * Special numbers (subnormal numbers, positive zero, negative zero, infinities, and NaNs) and negative values are strictly FORBIDDEN.
   * The sample rate **MAY** be `0.0` (all 64 bits zero) to indicate that the information is only stored in `STREAMINFO`.
2. **Channel Count Minus 1** (8 bits): Encodes `(number of channels) - 1`. Values range from `0` (1 channel) to `255` (256 channels).
3. **Channel Mask** (32 bits): Standard 32-bit channel position mask conforming to the `WAVEFORMATEXTENSIBLE` specification as described in Section 8.6.2 of [@!RFC9639].
4. **Special Mask Hints** (3 bits): Reserved for hinting special use cases of the channel bitmap (e.g., standard stereo vs. headphone stereo). All values are currently reserved; encoders **MUST** set these bits to `0b000` and decoders **MUST** ignore them.
5. **Ignore Mask Flag** (2 bits): Indicates whether the decoder should ignore the `WAVEFORMATEXTENSIBLE` bits and use another custom channel mask. All values are currently reserved; encoders **MUST** set these bits to `0b00` and decoders **MUST** ignore them.
6. **Sample Format** (4 bits): An enumeration defining sample arithmetic:
   * `0b0000` (0): Normal integer PCM.
   * `0b0001` (1): Float PCM ([@!IEEE754] single-precision 32-bit floating-point).
   * Other values are reserved for future development.
7. **Bits Per Sample Minus 1** (7 bits): Encodes `(bit depth) - 1`. If the sample format is float PCM, the bit depth **MUST** be 32 (encoded as value `31`).
8. **Reserved** (8 bits): Reserved for future development. Encoders **MUST** set these bits to zero. Decoders **MUST** reject streams containing non-zero bits in this field.

## Parameter Resolution and Compatibility Rules {#parameter-resolution}

When `STREAMINFO_EXTENSION` is present:

1. New decoders **MUST** replace the corresponding legacy info from the `STREAMINFO` block with every valid piece of information from `STREAMINFO_EXTENSION`, and ignore the invalid ones.
2. If the `sample_rate` field in `STREAMINFO_EXTENSION` is `0.0`, or contains a forbidden IEEE 754 special representation, the decoder **MUST** fall back to the `sample_rate` stored in `STREAMINFO`.
3. Only the extension header stream parameters are considered during audio decoding when the extension is present.

# Frame Header Extensions {#frame-header-extensions}

## Signaling in the Legacy Frame Header {#signaling-legacy-frame-header}

The encoder **MUST** use the extended frame header if and only if `STREAMINFO_EXTENSION` is present.

To ensure legacy decoders reject the frame, the legacy frame header fields **MUST** be set to the following indicator values:

| Field Name              | Bit Range in Header           | Required Value | Legacy Interpretation |
| :---------------------- | :---------------------------- | :------------- | :-------------------- |
| Sample Rate             | `raw_header[2] & 0x0F`        | `0b1111` (15)  | Forbidden             |
| Channel Assignment      | `raw_header[3] >> 4`          | `0b1111` (15)  | Reserved              |
| Sample Size (Bit Depth) | `(raw_header[3] & 0x0E) >> 1` | `0b011` (3)    | Reserved              |
Table: Legacy Frame Header Signaling Values {#tbl-legacy-header-signaling}

Uncommon sample rates (sample rate hint codes 12, 13, and 14 requiring trailing octets) **MUST NOT** be used together with the frame header extension. Uncommon sample rates are deprecated; the frame extension **MUST** be used instead.

## Format Selection (Compact vs. Full) {#format-selection}

Bit 0 of byte 3 of the legacy frame header (`raw_header[3] & 0x01`), which was defined as a reserved zero-bit in [@!RFC9639], indicates the extension format:

* `0`: **Compact Mode**. The extension payload is 8 bits (1 octet). Audio parameters (sample rate, channel count, channel mask, bit depth, and sample format) are inherited from `STREAMINFO_EXTENSION`.
* `1`: **Full Mode**. The extension payload is 128 bits (16 octets), explicitly defining all stream parameters inline.

## Framing and CRC-8 Coverage {#framing-crc8-coverage}

The extension bits **MUST** immediately follow the main frame header bits (the frame/sample number and any blocksize hint) and directly precede the frame header CRC-8.

The frame header CRC-8 covers the extension bits as well as the preceding header bytes.

## Compact Mode Syntax {#compact-mode-syntax}

In Compact Mode, the extension payload is 8 bits (1 octet):

| Bits   | Field Name            | Description                                                                 |
| :----- | :-------------------- | :-------------------------------------------------------------------------- |
| `0..2` | Decorrelation Mode ID | 3 bits specifying inter-channel decorrelation (see (#decorrelation-stereo)) |
| `3`    | Float Fallback Flag   | `0` = Dynamic Positional Alignment (DPA) mode, `1` = Fallback mode          |
| `4..7` | Reserved              | 4 bits reserved; **MUST** be `0b0000`                                       |
Table: Compact Mode Extension Payload {#tbl-compact-mode-syntax}

## Full Mode Syntax {#full-mode-syntax}

In Full Mode, the extension payload is 128 bits (16 octets):

| Bit Range  | Field Name              | Description                                                                          |
| :--------- | :---------------------- | :----------------------------------------------------------------------------------- |
| `0..63`    | Sample Rate             | 64 bits IEEE 754 binary64 sample rate in Hz (forbidden values trigger fallback)      |
| `64..71`   | Channel Count Minus 1   | 8 bits for `(number of channels) - 1` (0..255)                                       |
| `72..103`  | Channel Mask            | 32 bits standard `WAVEFORMATEXTENSIBLE` channel mask ([@!RFC9639] Section 8.6.2)     |
| `104..106` | Special Mask Hints      | 3 bits reserved; **MUST** be `0b000`                                                 |
| `107..108` | Ignore Mask Flag        | 2 bits reserved; **MUST** be `0b00`                                                  |
| `109..111` | Decorrelation Mode ID   | 3 bits specifying inter-channel decorrelation (see (#decorrelation-stereo))          |
| `112..118` | Bits Per Sample Minus 1 | 7 bits for `(bit depth) - 1` (**MUST** be 31 for float PCM)                          |
| `119`      | Float Fallback Flag     | `0` = Dynamic Positional Alignment (DPA) mode, `1` = Fallback mode                   |
| `120..123` | Reserved                | 4 bits reserved; **MUST** be `0b0000`                                                |
| `124..127` | Sample Format           | 4 bits: `0b0000` = integer PCM, `0b0001` = float PCM                                 |
Table: Full Mode Extension Payload {#tbl-full-mode-syntax}

## Inter-channel Decorrelation Descriptors {#decorrelation-stereo}

The interpretation of the 3-bit inter-channel decorrelation mode descriptor ID depends on the active channel configuration and channel mask.

For standard 2-channel stereo (`SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT`, or channel mask `0x00000003`):

| Value          | Mode Name         | Channel Assignment                  |
| :------------- | :---------------- | :---------------------------------- |
| `0b000`        | Independent       | Channel 0 = Left, Channel 1 = Right |
| `0b001`        | Left-Side Stereo  | Channel 0 = Left, Channel 1 = Side  |
| `0b010`        | Side-Right Stereo | Channel 0 = Side, Channel 1 = Right |
| `0b011`        | Mid-Side Stereo   | Channel 0 = Mid, Channel 1 = Side   |
| `0b100..0b111` | Reserved          | Reserved for future use             |
Table: Stereo Inter-channel Decorrelation Modes {#tbl-decorrelation-stereo}

For all other channel configurations, all 3-bit values other than `0b000` (Independent channels) are currently reserved for future multi-channel decorrelation schemes.

# Extended Entropy Coding: PARTITIONED_RICE3 {#partitioned-rice3}

## Method Type Allocation {#method-type-allocation}

Section 9.2.7 of [@!RFC9639] defines the 2-bit residual coding method field. The value `0b10` was previously reserved. This specification allocates `0b10` as `PARTITIONED_RICE3`:

| Value  | Method Name         | Description                               |
| :----- | :------------------ | :---------------------------------------- |
| `0b00` | `PARTITIONED_RICE`  | 4-bit Rice parameter, 5-bit escape length |
| `0b01` | `PARTITIONED_RICE2` | 5-bit Rice parameter, 5-bit escape length |
| `0b10` | `PARTITIONED_RICE3` | 7-bit Rice parameter, 7-bit escape length |
| `0b11` | Reserved            | Reserved for future use                   |
Table: Residual Coding Methods {#tbl-residual-coding-methods}

## Coding Syntax and Parameters {#coding-syntax-parameters}

`PARTITIONED_RICE3` expands the Rice parameter and unencoded escape bit fields:

| Parameter        | Length | Values / Semantics                                                   |
| :--------------- | :----- | :------------------------------------------------------------------- |
| Method Type      | 2 bits | `0b10`: `PARTITIONED_RICE3`                                          |
| Partition Order  | 4 bits | 0 <= order <= 15 (2^order partitions)                                |
| Rice Parameter   | 7 bits | 0 <= k <= 126 (Rice parameter)                                       |
| Escape Parameter | 7 bits | `0b1111111` (127): Signals unencoded raw residual partition          |
| Raw Bits Length  | 7 bits | 1 <= raw_bits <= 127: Unencoded signed two's complement width      |
Table: PARTITIONED_RICE3 Parameters {#tbl-rice3-parameters}

## Escape Mechanism and Raw Sample Coding {#escape-mechanism-raw-coding}

If the 7-bit Rice parameter read for a partition is strictly less than `127`, the partition residual samples are decoded using signed Rice coding with parameter k.

If the Rice parameter equals `127`:

1. The decoder reads a 7-bit unsigned integer `raw_bits`.
2. The decoder reads each residual sample in the partition as an unencoded signed two's complement integer of width `raw_bits`.

# Floating-Point Sample Coding {#floating-point-coding}

## Dual-Subframe Per Channel Architecture {#dual-subframe-architecture}

When `sample_type` is float PCM (`0b0001`), each logical audio channel ch is represented using two subframes:

| Subframe Index | Subframe Designation | Purpose                                                    |
| :------------- | :------------------- | :--------------------------------------------------------- |
| `2 * ch`       | Upper Subframe       | Primary integer signal (FIXED, LPC, VERBATIM, or CONSTANT) |
| `2 * ch + 1`   | Lower Subframe       | Packed lower mantissa bits (PACKED) or significand         |
Table: Dual-Subframe Channel Designation {#tbl-dual-subframe-arch}

A frame containing C channels contains `2 * C` subframes.

## Dynamic Positional Alignment (DPA) Mode {#dpa-mode}

DPA mode is active when the Float Fallback Flag is `0`.

### Mathematical Formulation {#dpa-math-formulation}

In [@!IEEE754] single-precision format, normalized non-zero numbers are given by:

``` ascii-art
x = (-1)^s * (1 + mant * 2^(-23)) * 2^(exp - 127)
```

The active bit position bounds across a frame for each channel are:

``` ascii-art
highest_pos = max_i(exp_i)
lowest_pos = min_i(exp_i - 23 + ctz((1 << 23) | mant_i))
span = highest_pos - lowest_pos + 2
```

Given candidate upper bit depth B_upper (e.g., 24 bits) and lower bit depth B_lower = min(23, max(0, span - B_upper)):

``` ascii-art
eff_width = B_upper + B_lower
base_pos = highest_pos - (eff_width - 2)
```

### Sample Reconstruction {#dpa-sample-reconstruction}

To reconstruct IEEE 754 32-bit float samples:

``` python
mask = (1 << lower_bits) - 1 if lower_bits > 0 else 0
base_exp = highest_exp - ((upper_bits + lower_bits) - 2)

for i in range(blocksize):
    up = upper_signal[i]
    low_val = lower_signal[i]

    if up == 0:
        dest[i] = uint32_to_float(low_val)
    elif lower_bits == 0:
        sign = 1 if up < 0 else 0
        uval = -up if up < 0 else up
        lead = ilog2(uval)
        shift = lead - 23
        orig_exp = base_exp + lead
        explicit_mant = (uval >> shift if shift >= 0
                         else uval << (-shift))
        dest[i] = compose_float(sign, orig_exp,
                                explicit_mant & 0x7FFFFF)
    else:
        full_x = (up << lower_bits) | (low_val & mask)
        sign = 1 if full_x < 0 else 0
        uval = -full_x if full_x < 0 else full_x
        lead = ilog2(uval)
        shift = lead - 23
        orig_exp = base_exp + lead
        explicit_mant = (uval >> shift if shift >= 0
                         else uval << (-shift))
        dest[i] = compose_float(sign, orig_exp,
                                explicit_mant & 0x7FFFFF)
```

## Fallback Mode {#fallback-mode}

Fallback mode is active when the Float Fallback Flag is `1`. It accommodates frames where the compressed bit count in DPA mode yields a compression ratio worse than or equal to 75% of uncompressed audio bits.

1. **Exponent Subframe** (Subframe `2 * ch`, 8 bits per sample):

   ``` ascii-art
   signal[s] = exp_s - 128    (range [-128, 127])
   ```

2. **Significand Subframe** (Subframe `2 * ch + 1`, 24 bits per sample):
   * Positive samples: `+mantissa`.
   * Negative samples: `-mantissa`.
   * Negative zero (`-0.0f`): encoded as `-2^23 = -8388608`.

Reconstruction in Fallback mode:

``` python
orig_exp = (exp_signal[i] + 128) & 0xFF
sm = sign_mant_signal[i]

if sm == -8388608:
    sign = 1
    mant = 0
elif sm < 0:
    sign = 1
    mant = (-sm) & 0x7FFFFF
else:
    sign = 0
    mant = sm & 0x7FFFFF

dest[i] = (sign << 31) | (orig_exp << 23) | mant
```

## Inter-channel Decorrelation in Floating-Point Frames {#decorrelation-float-frames}

When inter-channel decorrelation (Left-Side, Right-Side, or Mid-Side) is active in a stereo floating-point frame:

* In **DPA Mode**, decorrelation is applied exclusively to the Upper Subframes (Subframes 0 and 2). The side subframe receives bps + 1 bits resolution. The Lower Subframes (Subframes 1 and 3) remain independent.
* In **Fallback Mode**, decorrelation is applied exclusively to the Exponent Subframes (Subframes 0 and 2).

# The PACKED Subframe {#packed-subframe}

## Subframe Header Code Allocation {#subframe-header-packed}

Section 9.2.1 of [@!RFC9639] defines the 6-bit subframe type indicator. The code `0b000010` was previously reserved. This specification allocates `0b000010` to `SUBFRAME_PACKED`:

| Bits   | Field Name       | Description                                                 |
| :----- | :--------------- | :---------------------------------------------------------- |
| `0`    | Zero Padding     | **MUST** be `0`                                             |
| `1..6` | Subframe Type    | `0b000010`: `SUBFRAME_PACKED`                               |
| `7`    | Wasted Bits Flag | **MUST** be `0` (wasted bits are not used for PACKED subframes) |
Table: SUBFRAME_PACKED Header Format {#tbl-subframe-packed-header}

## PACKED Subframe Structure {#packed-subframe-structure}

The payload of a `PACKED` subframe directly follows the subframe header octet:

| Bit Range | Field Name           | Description                                          |
| :-------- | :------------------- | :--------------------------------------------------- |
| `0..7`    | Exponent Zero Offset | 8-bit channel highest exponent / DC offset value     |
| `8..12`   | Lower Bits Parameter | 5 bits encoding lower_bits (`0 <= k <= 31`)          |
| `13..15`  | Reserved             | 3 bits reserved; **MUST** be `0b000`                 |
| Variable  | Sample Payload       | Sequence of blocksize variable-width bit sequences   |
Table: PACKED Subframe Payload Structure {#tbl-packed-payload-structure}

## Variable-Width Bit Truncation and Decoding Algorithm {#packed-decoding-algorithm}

The `PACKED` subframe carries lower-order mantissa bits associated with the integer signal decoded from the paired upper subframe (`upper_subframe[i]`).

For each sample i from 0 to blocksize - 1:

``` python
up = upper_subframe[i]

if up == 0:
    # Outlier sample: 32 raw bits representing the IEEE 754 float
    raw_val = bitreader.read_raw_uint32(32)
    lower_signal[i] = raw_val
elif lower_bits > 0:
    if up == -1:
        d = 0
    elif up > 0:
        lead_u = ilog2(up)
        lead_full = lead_u + lower_bits
        d = (lead_full - 23) if (lead_full > 23) else 0
    else:
        lead_u = ilog2(~up)
        lead_full = lead_u + lower_bits
        d = (lead_full - 23) if (lead_full > 23) else 0

    width = (lower_bits - d) if (lower_bits > d) else 0
    if width > 0:
        read_val = bitreader.read_raw_uint64(width)
        restored = read_val << d
    else:
        restored = 0
    lower_signal[i] = restored
else:
    lower_signal[i] = 0
```

# Ecosystem and Container Considerations {#ecosystem-container}

## Ogg FLAC Encapsulation {#ogg-flac-encapsulation}

When encapsulating FLAC streams containing `STREAMINFO_EXTENSION` in an Ogg container [@?RFC3533] as specified in Section 10.1 of [@!RFC9639]:

* The `STREAMINFO_EXTENSION` block constitutes an additional metadata header packet.
* The 2-byte header packet count field in the first Ogg FLAC packet **MUST** account for `STREAMINFO_EXTENSION` in its total metadata packet count.

## Cuesheets and CD-DA Constraints {#cuesheets-cdda}

In accordance with Compact Disc Digital Audio (CD-DA) constraints:

* The CD-DA subset flag in cuesheet metadata blocks **MUST NOT** be set if the audio stream uses floating-point sample format or has a sample rate other than 44,100 Hz.
* For non-CD-DA cuesheet tracks on streams with fractional sample rates, sample offsets **MUST** be rounded to the nearest integer index using `rint()` rounding.

## Seek Table Calculations with Float Sample Rates {#seektable-calculations}

Seek table target sample offsets and time-to-sample calculations **MUST** be computed using 64-bit IEEE 754 double-precision floating-point arithmetic when `STREAMINFO_EXTENSION` sample rate is present.

## ReplayGain for Floating-Point Streams {#replaygain-float}

ReplayGain analysis on 32-bit float streams operates directly on normalized floating-point samples (with reference level matching full-scale 1.0). During playback synthesis, gain adjustments are applied via direct linear float multiplication without integer quantization or hard clipping.

# Security Considerations {#security-considerations}

Implementations processing extended FLAC bitstreams must account for security considerations associated with untrusted audio decoding:

1. **Memory Allocation Validation**: Channel counts up to 256 and bit depths up to 128 imply larger decoded frame buffers. Implementations **MUST** ensure that channel counts, block sizes, and bit depths declared in `STREAMINFO_EXTENSION` or Full Frame Headers do not cause integer overflows during buffer allocation calculations.
2. **IEEE 754 Special Value Validation**: Decoders **MUST** validate the 64-bit floating-point sample rate field in `STREAMINFO_EXTENSION` and frame headers. De-serialized double-precision values **MUST** be checked for subnormals, infinities, and NaN payloads to prevent undefined floating-point behavior, division-by-zero errors in timeline calculations, or denial-of-service conditions during seek operations.
3. **Entropy Decoder Buffer Bounds**: In `PARTITIONED_RICE3`, escape parameters signal unencoded raw bit lengths up to 127 bits. Bitstream readers **MUST** strictly verify buffer boundaries prior to variable-width bit consumption to prevent out-of-bounds memory reads.
4. **Header CRC-8 Verification**: Implementations **MUST** verify the frame header CRC-8 over the full extended header payload before attempting to interpret extended frame properties or allocate subframe resources.

# IANA Considerations {#iana-considerations}

This document has no IANA actions.

{backmatter}

<reference anchor="IEEE754" target="https://doi.org/10.1109/IEEESTD.2008.4610935">
  <front>
    <title>IEEE Standard for Floating-Point Arithmetic</title>
    <author>
      <organization>IEEE</organization>
    </author>
    <date year="2008"/>
  </front>
  <seriesInfo name="IEEE Std" value="754-2008"/>
  <seriesInfo name="DOI" value="10.1109/IEEESTD.2008.4610935"/>
</reference>
