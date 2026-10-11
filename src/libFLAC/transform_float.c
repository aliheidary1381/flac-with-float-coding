/* libFLAC - Free Lossless Audio Codec library
 * Copyright (C) 2025-2026 Xiph.Org Foundation
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * - Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 *
 * - Redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution.
 *
 * - Neither the name of the Xiph.org Foundation nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION OR
 * CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
 * PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
 * LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/* Bit manipulation utilities used for conversion between IEEE 754 32-bit float samples
 * and compressible integer samples (up to 63-bit)
 */

/*
To compress float samples, one must understand how they are generated in order to
design an algorithm that is tailored to exploit the specific signal structure.

There are some audio recording equipment coming to the market that can record 32-bit floats.
They utilise two (or rarely more) ADCs working in tandem to create a single audio file.
A low-gain ADC is optimised for loud sections, while the other high-gain ADC is optimised for quieter ones.
The recorder switches between the two on the fly.

With that in mind, we see that although these recordings are floating-point,
their dynamic range does not merely reach the DR of 32-bit floats (between 2^-126 and 2^127),
especially considering that these loud and quiet sections are long enough to be split into
different FLAC frames, each having a normal int-like DR.
Moreover, the majority of the post-processing and audio editing in the music and film industry does not
increase the dynamic range too much. On the contrary, most audio engineers try to decrease the DR.

So basically, we're mostly dealing with a combination of ints,
and a single coefficient, converting them to floats.

Possible design choices are listed below. The further we go, the higher the level of compression:
   1. just leaving the floats as ints
		probably would lead to saving most of them in verbatim frames with no compression
		my tests resulted in an ~80% ratio
   2. doing some basic bit manipulation
		saves ~3 bits theoretically
		my tests resulted in about ~70% ratio
   3. splitting the "exponent" (8 bit) and "sign+significand" (24 bit) parts of floats into two subframes.
		my tests resulted in about ~68% ratio, even with no bit manipulation
   4. splitting + some bit manipulation + subtracting an (automatically recognised) DC offset from the
		"exponents" channel and storing the offset in each frame header (current approach).
		my tests resulted in about ~45% ratio
*/

/*
Binary Positional Notation Transformation (1-subframe mode & DPA mode):
   In binary positional notation, each normalized float sample (+/- 1.mant * 2^(exp - 127)) has:
     - highest active bit position = exp
     - lowest active bit position = exp - 23 + ctz(explicit_mant)
   Across a frame of samples:
     - highest_pos = max(exp_i)
     - lowest_pos = min(exp_i - 23 + ctz(explicit_mant_i))
   The exact minimum integer bit depth needed to losslessly represent all active bits is:
     bps = highest_pos - lowest_pos + 2
   Setting base_exp = lowest_pos, each sample's explicit mantissa is shifted by:
     shift = (exp_i - 23) - lowest_pos >= 0
   into an exact two's complement signed integer of width up to 63 bits without losing any bit.
   When bps <= 63 (and no special NaN/Inf/-0.0f conditions exist), the frame is encoded
   losslessly in Dynamic Positional Alignment (DPA) mode (2 subframes per channel):
     - Delta subframe (8 bps): sample shifts Delta_i = max(0, (exp_i - 23) - lowest_pos)
     - Audio subframe (up to 63 bps): aligned integer x_i with normalized residuals Q_i = r_i >> Delta_i
   The per-channel base exponent (base_exp) is stored in the frame header (in exponent_zero_offsets).

Frame Mode Selection & Fallback Two's Complement Conversion:
   - Mode flag: A 1-bit fallback flag is stored in one of the reserved bits of the frame header
     extension (0 = lossless DPA mode at dynamic bps <= 63, 1 = fallback 2-subframe mode).
   - Fallback mode: When a frame has bps > 63 or contains special float conditions (NaN, Inf,
     subnormals, or -0.0f), it falls back to 2 subframes per channel (exponent + significand):
       * Exponent channel (8 bits): stored with a -128 DC offset ([-128, 127]).
       * Significand channel (24 bits): converted from sign-magnitude to two's complement,
         mapping positive samples to +mantissa, negative samples to -mantissa, and -0.0f
         to -2^23 (-8388608).
*/

#ifdef HAVE_CONFIG_H
#  include <config.h>
#endif

#include <stddef.h>
#include <stdint.h>
#include "private/macros.h"
#include "private/transform_float.h"
#include "FLAC/assert.h"
#include "private/bitmath.h"
#include "private/md5.h"
#include "share/alloc.h"
#include "share/endswap.h"

#if defined(_MSC_VER)
#  include <intrin.h>
static inline int flac_ctz32(uint32_t v) {
	unsigned long idx = 0;
	if (_BitScanForward(&idx, v))
		return (int)idx;
	return 0;
}
#else
static inline int flac_ctz32(uint32_t v) {
	return v ? (int)__builtin_ctz((unsigned int)v) : 0;
}
#endif

/*
 * Splits 32-bit IEEE 754 floating-point samples into two integer subframe signals:
 *  - Exponent (8 bits: raw exponent in bits 23-30)
 *  - Sign + Significand (24 bits: sign at bit 23, significand fraction at bits 0-22)
 */
void FLAC__split_f32_buffer_to_subframe_signals(FLAC__int32 *exp_signal, FLAC__int32 *sign_mant_signal, const uint32_t *src, size_t n)
{
	size_t i;
	for (i = 0; i < n; i++) {
		const uint32_t x = src[i];
		const uint32_t exp = (x >> 23) & 0xFF;
		const uint32_t sign = (x >> 31) & 1;
		const uint32_t mant = x & 0x7FFFFF;
		const uint32_t sign_mant = (sign << 23) | mant;
		exp_signal[i] = (FLAC__int32)exp;
		sign_mant_signal[i] = (FLAC__int32)(sign_mant << 8) >> 8;
	}
}

/*
 * Splits interleaved 32-bit IEEE 754 floating-point samples into two integer subframe signals per channel:
 *  - Exponent (8 bits: raw exponent in bits 23-30) -> integer_signals[2 * ch]
 *  - Sign + Significand (24 bits: sign at bit 23, significand fraction at bits 0-22) -> integer_signals[2 * ch + 1]
 */
void FLAC__split_f32_interleaved_to_subframe_signals(FLAC__int32 * const integer_signals[], uint32_t channels, uint32_t current_sample_number, const uint32_t *src, size_t n)
{
	size_t s, k = 0;
	uint32_t ch;
	for (s = 0; s < n; s++) {
		for (ch = 0; ch < channels; ch++) {
			const uint32_t x = src[k++];
			const uint32_t exp = (x >> 23) & 0xFF;
			const uint32_t sign = (x >> 31) & 1;
			const uint32_t mant = x & 0x7FFFFF;
			const uint32_t sign_mant = (sign << 23) | mant;
			integer_signals[2 * ch][current_sample_number + s] = (FLAC__int32)exp;
			integer_signals[2 * ch + 1][current_sample_number + s] = (FLAC__int32)(sign_mant << 8) >> 8;
		}
	}
}

/*
 * Computes base exponent per channel for 1-subframe lossless float encoding (63 bps).
 */
void FLAC__compute_channel_base_exp_float(const FLAC__int32 * const split_signals[], uint32_t channels, uint32_t blocksize, uint32_t channel_base_exp[])
{
	uint32_t ch;
	int ch_highest[FLAC__MAX_CHANNELS];
	int ch_lowest[FLAC__MAX_CHANNELS];
	for (ch = 0; ch < channels; ch++) {
		int highest_pos = -1;
		int lowest_pos = 1000;
		uint32_t s;
		for (s = 0; s < blocksize; s++) {
			uint32_t exp = (uint32_t)split_signals[2 * ch][s] & 0xFF;
			uint32_t sign_mant = (uint32_t)split_signals[2 * ch + 1][s] & 0xFFFFFF;
			if (exp != 0 || (sign_mant & 0x7FFFFF) != 0) {
				uint32_t explicit_mant = (1u << 23) | (sign_mant & 0x7FFFFF);
				int tz = flac_ctz32(explicit_mant);
				int pos_high = (int)exp;
				int pos_low = (int)exp - 23 + tz;
				if (pos_high > highest_pos)
					highest_pos = pos_high;
				if (pos_low < lowest_pos)
					lowest_pos = pos_low;
			}
		}
		ch_highest[ch] = highest_pos;
		ch_lowest[ch] = lowest_pos;
		if (highest_pos == -1 || lowest_pos < 0) {
			channel_base_exp[ch] = 127;
		} else {
			channel_base_exp[ch] = (uint32_t)lowest_pos;
		}
	}
	if (channels == 2 && ch_highest[0] != -1 && ch_highest[1] != -1) {
		int max_h = flac_max(ch_highest[0], ch_highest[1]);
		int min_l = flac_min(ch_lowest[0], ch_lowest[1]);
		if (min_l >= 0 && max_h <= 254) {
			uint32_t combined_bps = (uint32_t)(max_h - min_l + 2);
#if FLAC__HAS_INT128
			if (combined_bps <= 63) {
				channel_base_exp[0] = channel_base_exp[1] = (uint32_t)min_l;
			}
#else
			if (combined_bps <= 32) {
				channel_base_exp[0] = channel_base_exp[1] = (uint32_t)min_l;
			}
#endif
		}
	}
}

/*
 * Recombines 2 integer subframe signals (raw 8-bit exponent and 24-bit two's complement significand)
 * into standard 32-bit IEEE 754 floating-point raw words.
 */
void FLAC__combine_subframe_signals_fallback_to_f32_buffer(uint32_t *dest, const FLAC__int32 *exp_signal, const FLAC__int32 *sign_mant_signal, size_t n)
{
	size_t i;
	for (i = 0; i < n; i++) {
		const uint32_t orig_exp = (uint32_t)(exp_signal[i] + 128) & 0xFF;
		const FLAC__int32 sm = sign_mant_signal[i];
		uint32_t sign, orig_mant;
		if (sm == -8388608) { /* -2^23: preserves sign=1 when mant=0 (e.g., -0.0f, -1.0*2^k, -inf) */
			sign = 1;
			orig_mant = 0;
		} else if (sm < 0) {
			sign = 1;
			orig_mant = (uint32_t)(-sm) & 0x7FFFFF;
		} else {
			sign = 0;
			orig_mant = (uint32_t)sm & 0x7FFFFF;
		}
		dest[i] = (sign << 31) | (orig_exp << 23) | orig_mant;
	}
}

/*
 * Recombines 1 subframe preprocessed two's complement integer signal (32 bps) into standard 32-bit IEEE 754
 * floating-point raw words via base_exp = lowest_pos.
 */


/*
 * Recombines DPA mode integer subframes (8-bit delta stream and aligned audio integer signal)
 * into standard 32-bit IEEE 754 floating-point raw words.
 */
void FLAC__combine_subframe_signals_dpa_to_f32_buffer_32(uint32_t *dest, const FLAC__int32 *delta_signal, const FLAC__int32 *sig_signal, size_t n, FLAC__byte base_exp_offset)
{
	size_t i;
	(void)delta_signal;
	for (i = 0; i < n; i++) {
		FLAC__int32 val = sig_signal[i];
		if (val == 0) {
			dest[i] = 0;
		} else {
			uint32_t sign = (val < 0) ? 1 : 0;
			uint32_t uval = (val < 0) ? (uint32_t)(-val) : (uint32_t)val;
			int leading_bit_pos = (int)FLAC__bitmath_ilog2(uval);
			int shift = leading_bit_pos - 23;
			uint32_t orig_exp = (uint32_t)base_exp_offset + (uint32_t)leading_bit_pos;
			uint32_t explicit_mant = (shift >= 0) ? (uint32_t)(uval >> shift) : (uint32_t)(uval << (-shift));
			uint32_t orig_mant = explicit_mant & 0x7FFFFF;
			dest[i] = (sign << 31) | (orig_exp << 23) | orig_mant;
		}
	}
}

void FLAC__combine_subframe_signals_dpa_to_f32_buffer(uint32_t *dest, const FLAC__int32 *delta_signal, const FLAC__int64 *sig_signal, size_t n, FLAC__byte base_exp_offset)
{
	size_t i;
	(void)delta_signal;
	for (i = 0; i < n; i++) {
		FLAC__int64 val = sig_signal[i];
		if (val == 0) {
			dest[i] = 0;
		} else {
			uint32_t sign = (val < 0) ? 1 : 0;
			uint64_t uval = (val < 0) ? (uint64_t)(-val) : (uint64_t)val;
			int leading_bit_pos = (int)FLAC__bitmath_ilog2_wide(uval);
			int shift = leading_bit_pos - 23;
			uint32_t orig_exp = (uint32_t)base_exp_offset + (uint32_t)leading_bit_pos;
			uint32_t explicit_mant = (shift >= 0) ? (uint32_t)(uval >> shift) : (uint32_t)(uval << (-shift));
			uint32_t orig_mant = explicit_mant & 0x7FFFFF;
			dest[i] = (sign << 31) | (orig_exp << 23) | orig_mant;
		}
	}
}

/*
 * Analyzes split float subframes across all channels to verify if the entire frame can be
 * losslessly represented using 1 subframe per channel (63 bps).
 */
FLAC__bool FLAC__check_frame_lossless_float(const FLAC__int32 * const split_signals[], uint32_t channels, uint32_t blocksize, uint32_t channel_base_exp[])
{
	uint32_t ch;
	int ch_highest[FLAC__MAX_CHANNELS];
	int ch_lowest[FLAC__MAX_CHANNELS];
	for (ch = 0; ch < channels; ch++) {
		int highest_pos = -1;
		int lowest_pos = 1000;
		uint32_t s;
		for (s = 0; s < blocksize; s++) {
			uint32_t exp = (uint32_t)split_signals[2 * ch][s] & 0xFF;
			uint32_t sign_mant = (uint32_t)split_signals[2 * ch + 1][s] & 0xFFFFFF;
			if (exp == 0 && (sign_mant & 0x7FFFFF) == 0) {
				if ((sign_mant >> 23) != 0) {
					/* -0.0f cannot be distinguished from +0.0f in two's complement 0 */
					return false;
				}
			} else if (exp == 0 || exp == 255) {
				/* Subnormal numbers, Infinity, and NaN require 2-subframe fallback mode */
				return false;
			} else {
				uint32_t explicit_mant = (1u << 23) | (sign_mant & 0x7FFFFF);
				int tz = flac_ctz32(explicit_mant);
				int pos_high = (int)exp;
				int pos_low = (int)exp - 23 + tz;
				if (pos_high > highest_pos)
					highest_pos = pos_high;
				if (pos_low < lowest_pos)
					lowest_pos = pos_low;
			}
		}
		ch_highest[ch] = highest_pos;
		ch_lowest[ch] = lowest_pos;
		if (highest_pos == -1) {
			channel_base_exp[ch] = 127;
		} else {
			uint32_t ch_bps;
			if (lowest_pos < 0 || highest_pos > 254)
				return false;
			ch_bps = (uint32_t)(highest_pos - lowest_pos + 2);
#if FLAC__HAS_INT128
			if (ch_bps > 63)
				return false;
#else
			if (ch_bps > 32)
				return false;
#endif
			channel_base_exp[ch] = (uint32_t)lowest_pos;
		}
	}
	if (channels == 2 && ch_highest[0] != -1 && ch_highest[1] != -1) {
		int max_h = flac_max(ch_highest[0], ch_highest[1]);
		int min_l = flac_min(ch_lowest[0], ch_lowest[1]);
		if (min_l >= 0 && max_h <= 254) {
			uint32_t combined_bps = (uint32_t)(max_h - min_l + 2);
#if FLAC__HAS_INT128
			if (combined_bps <= 63) {
				channel_base_exp[0] = channel_base_exp[1] = (uint32_t)min_l;
			}
#else
			if (combined_bps <= 32) {
				channel_base_exp[0] = channel_base_exp[1] = (uint32_t)min_l;
			}
#endif
		}
	}
	return true;
}

/*
 * Transforms signals in-place for Fallback 2-subframe mode:
 *  - signals[2*ch]: 8-bit exponent with -128 DC offset ([-128, 127])
 *  - signals[2*ch+1]: 24-bit significand in two's complement (-0.0f -> -2^23)
 */
void FLAC__transform_frame_fallback_float(FLAC__int32 *signals[], uint32_t channels, uint32_t blocksize)
{
	uint32_t ch;
	for (ch = 0; ch < channels; ch++) {
		uint32_t s;
		for (s = 0; s < blocksize; s++) {
			uint32_t exp = (uint32_t)signals[2 * ch][s] & 0xFF;
			uint32_t sign_mant = (uint32_t)signals[2 * ch + 1][s] & 0xFFFFFF;
			uint32_t sign = (sign_mant >> 23) & 1;
			uint32_t mant = sign_mant & 0x7FFFFF;
			FLAC__int32 sm;
			if (sign && mant == 0) {
				sm = -8388608; /* -2^23: preserves sign=1 when mant=0 (e.g., -0.0f, -1.0*2^k, -inf) */
			} else if (sign) {
				sm = -(FLAC__int32)mant;
			} else {
				sm = (FLAC__int32)mant;
			}
			signals[2 * ch][s] = (FLAC__int32)exp - 128;
			signals[2 * ch + 1][s] = sm;
		}
	}
}

/*
 * Transforms signals into dedicated 64-bit destination for Lossless 1-subframe mode (63 bps):
 *  - converts split_signals[2*ch] / split_signals[2*ch+1] into preprocessed 64-bit two's complement integer in dest_signals[ch]
 */

/*
 * Transforms signals into dedicated DPA mode:
 *  - delta_signals[2*ch]: 8-bit delta stream Delta_i
 *  - dest_signals64[2*ch+1]: preprocessed 64-bit aligned integer x_i
 *  - dest_signals32[2*ch+1]: preprocessed 32-bit aligned integer x_i (if non-NULL)
 */
void FLAC__transform_frame_dpa_float(FLAC__int32 *delta_signals[], FLAC__int64 *dest_signals64[], FLAC__int32 *dest_signals32[], const FLAC__int32 * const split_signals[], const uint32_t channel_base_exp[], FLAC__byte exponent_zero_offsets[], uint32_t channels, uint32_t blocksize)
{
	uint32_t ch;
	for (ch = 0; ch < channels; ch++) {
		uint32_t base_exp = channel_base_exp[ch];
		FLAC__int32 *delta_out = delta_signals[2 * ch];
		FLAC__int64 *sig64 = dest_signals64 ? dest_signals64[2 * ch + 1] : NULL;
		FLAC__int32 *sig32 = dest_signals32 ? dest_signals32[2 * ch + 1] : NULL;
		const FLAC__int32 *exp_in = split_signals[2 * ch];
		const FLAC__int32 *sm_in = split_signals[2 * ch + 1];
		uint32_t prev_delta = 0;
		uint32_t s;
		for (s = 0; s < blocksize; s++) {
			uint32_t exp = (uint32_t)exp_in[s] & 0xFF;
			uint32_t sign_mant = (uint32_t)sm_in[s] & 0xFFFFFF;
			if (exp == 0 && (sign_mant & 0x7FFFFF) == 0) {
				if (sig64) sig64[s] = 0;
				if (sig32) sig32[s] = 0;
				delta_out[s] = (FLAC__int32)prev_delta;
			} else {
				int shift = ((int)exp - 23) - (int)base_exp;
				uint32_t cur_delta = (shift > 0) ? (uint32_t)shift : 0;
				uint64_t explicit_mant = (1ULL << 23) | (uint64_t)(sign_mant & 0x7FFFFF);
				uint64_t val = (shift >= 0) ? (explicit_mant << shift) : (explicit_mant >> (-shift));
				uint32_t sign = (sign_mant >> 23) & 1;
				FLAC__int64 signed_val = sign ? -(FLAC__int64)val : (FLAC__int64)val;
				if (sig64) sig64[s] = signed_val;
				if (sig32) sig32[s] = (FLAC__int32)signed_val;
				delta_out[s] = (FLAC__int32)cur_delta;
				prev_delta = cur_delta;
			}
		}
		exponent_zero_offsets[ch] = (FLAC__byte)base_exp;
	}
}

/*
 * Accumulates MD5 checksum over the unencoded 32-bit float audio samples reconstructed
 * from the split signals (exp = split_signals[2*ch], sign_mant = split_signals[2*ch+1]):
 * Accumulates 4 bytes per sample (little-endian IEEE 754 float32 bit pattern)
 */
FLAC__bool FLAC__MD5Accumulate_split_float(FLAC__MD5Context *ctx, const FLAC__int32 * const split_signals[], uint32_t channels, uint32_t samples)
{
	size_t bytes_needed = 0;
	uint32_t sample, channel;
	FLAC__byte *buf;

	if (channels > 1024)
		return false;

	bytes_needed = (size_t)samples * (size_t)channels * sizeof(uint32_t);
	if (bytes_needed == 0)
		return true;

	buf = safe_malloc_(bytes_needed);
	if (!buf)
		return false;

	{
		uint32_t *dest = (uint32_t *)buf;
		size_t k = 0;
		for (sample = 0; sample < samples; sample++) {
			for (channel = 0; channel < channels; channel++) {
				uint32_t exp = (uint32_t)split_signals[2 * channel][sample] & 0xFF;
				uint32_t sign_mant = (uint32_t)split_signals[2 * channel + 1][sample] & 0xFFFFFF;
				uint32_t sign = (sign_mant >> 23) & 1;
				uint32_t mant = sign_mant & 0x7FFFFF;
				dest[k++] = H2LE_32((sign << 31) | (exp << 23) | mant);
			}
		}
	}

	FLAC__MD5Update(ctx, buf, (uint32_t)bytes_needed);
	free(buf);
	return true;
}

FLAC__bool FLAC__MD5Accumulate_float(FLAC__MD5Context *ctx, const uint32_t * const float_signals[], uint32_t channels, uint32_t samples)
{
	size_t bytes_needed = 0;
	uint32_t sample, channel;
	FLAC__byte *buf;

	if (channels > 1024)
		return false;

	bytes_needed = (size_t)samples * (size_t)channels * sizeof(uint32_t);
	if (bytes_needed == 0)
		return true;

	buf = safe_malloc_(bytes_needed);
	if (!buf)
		return false;

	{
		uint32_t *dest = (uint32_t *)buf;
		size_t k = 0;
		for (sample = 0; sample < samples; sample++) {
			for (channel = 0; channel < channels; channel++) {
				dest[k++] = H2LE_32(float_signals[channel][sample]);
			}
		}
	}

	FLAC__MD5Update(ctx, buf, (uint32_t)bytes_needed);
	free(buf);
	return true;
}


/*
 * DPA residual normalization routines for coupled audio subframes:
 * Q_i = r_i >> delta_i
 */
void FLAC__normalize_residual_dpa_32(FLAC__int32 residual[], const FLAC__int32 delta[], uint32_t residual_samples, uint32_t order)
{
	uint32_t j;
	for(j = 0; j < residual_samples; j++) {
		uint32_t d = (uint32_t)delta[order + j];
		FLAC__int32 r = residual[j];
		FLAC__int32 q = r >> d;
		if(d > 0) {
			FLAC__int32 abs_r = (r < 0) ? -r : r;
			FLAC__int32 abs_q = (q < 0) ? -q : q;
			FLAC__ASSERT(abs_q <= abs_r);
			if(abs_r >= (1 << d))
				FLAC__ASSERT(abs_q < abs_r);
		}
		residual[j] = q;
	}
}

void FLAC__normalize_residual_dpa_64(FLAC__int64 residual[], const FLAC__int32 delta[], uint32_t residual_samples, uint32_t order)
{
	uint32_t j;
	for(j = 0; j < residual_samples; j++) {
		uint32_t d = (uint32_t)delta[order + j];
		FLAC__int64 r = residual[j];
		FLAC__int64 q = r >> d;
		if(d > 0) {
			int64_t abs_r = (r < 0) ? -r : r;
			int64_t abs_q = (q < 0) ? -q : q;
			FLAC__ASSERT(abs_q <= abs_r);
			if(d < 63 && abs_r >= ((int64_t)1 << d))
				FLAC__ASSERT(abs_q < abs_r);
		}
		residual[j] = q;
	}
}

/*
 * DPA restore signal routines for coupled audio subframes
 */

void FLAC__fixed_restore_signal_32bit_dpa(const FLAC__int32 residual[], const uint8_t delta[], uint32_t data_len, uint32_t order, FLAC__int32 data[])
{
	int i, idata_len = (int)data_len;
	switch(order) {
		case 0:
			for(i = 0; i < idata_len; i++) {
				uint32_t d = delta[order + i];
				data[i] = (FLAC__int32)((uint32_t)residual[i] << d);
			}
			break;
		case 1:
			for(i = 0; i < idata_len; i++) {
				FLAC__int32 hat_x_i = data[i-1];
				uint32_t d = delta[order + i];
				uint32_t mask = (d > 0) ? (((uint32_t)1 << d) - 1) : 0;
				uint32_t R_i = (-(uint32_t)hat_x_i) & mask;
				uint32_t r_i = ((uint32_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int32)((uint32_t)hat_x_i + r_i);
			}
			break;
		case 2:
			for(i = 0; i < idata_len; i++) {
				FLAC__int32 hat_x_i = 2*data[i-1] - data[i-2];
				uint32_t d = delta[order + i];
				uint32_t mask = (d > 0) ? (((uint32_t)1 << d) - 1) : 0;
				uint32_t R_i = (-(uint32_t)hat_x_i) & mask;
				uint32_t r_i = ((uint32_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int32)((uint32_t)hat_x_i + r_i);
			}
			break;
		case 3:
			for(i = 0; i < idata_len; i++) {
				FLAC__int32 hat_x_i = 3*data[i-1] - 3*data[i-2] + data[i-3];
				uint32_t d = delta[order + i];
				uint32_t mask = (d > 0) ? (((uint32_t)1 << d) - 1) : 0;
				uint32_t R_i = (-(uint32_t)hat_x_i) & mask;
				uint32_t r_i = ((uint32_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int32)((uint32_t)hat_x_i + r_i);
			}
			break;
		case 4:
			for(i = 0; i < idata_len; i++) {
				FLAC__int32 hat_x_i = 4*data[i-1] - 6*data[i-2] + 4*data[i-3] - data[i-4];
				uint32_t d = delta[order + i];
				uint32_t mask = (d > 0) ? (((uint32_t)1 << d) - 1) : 0;
				uint32_t R_i = (-(uint32_t)hat_x_i) & mask;
				uint32_t r_i = ((uint32_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int32)((uint32_t)hat_x_i + r_i);
			}
			break;
		default:
			FLAC__ASSERT(0);
	}
}

void FLAC__fixed_restore_signal_64bit_dpa(const FLAC__int64 residual[], const uint8_t delta[], uint32_t data_len, uint32_t order, FLAC__int64 data[])
{
	int i, idata_len = (int)data_len;
	switch(order) {
		case 0:
			for(i = 0; i < idata_len; i++) {
				uint32_t d = delta[order + i];
				data[i] = (FLAC__int64)((uint64_t)residual[i] << d);
			}
			break;
		case 1:
			for(i = 0; i < idata_len; i++) {
				FLAC__int64 hat_x_i = data[i-1];
				uint32_t d = delta[order + i];
				uint64_t mask = (d > 0) ? (((uint64_t)1 << d) - 1) : 0;
				uint64_t R_i = (-(uint64_t)hat_x_i) & mask;
				uint64_t r_i = ((uint64_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int64)((FLAC__uint64)hat_x_i + r_i);
			}
			break;
		case 2:
			for(i = 0; i < idata_len; i++) {
#if FLAC__HAS_INT128
				FLAC__int64 hat_x_i = (FLAC__int64)(2*(FLAC__int128)data[i-1] - (FLAC__int128)data[i-2]);
#else
				FLAC__int64 hat_x_i = (FLAC__int64)(2*(FLAC__uint64)data[i-1] - (FLAC__uint64)data[i-2]);
#endif
				uint32_t d = delta[order + i];
				uint64_t mask = (d > 0) ? (((uint64_t)1 << d) - 1) : 0;
				uint64_t R_i = (-(uint64_t)hat_x_i) & mask;
				uint64_t r_i = ((uint64_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int64)((FLAC__uint64)hat_x_i + r_i);
			}
			break;
		case 3:
			for(i = 0; i < idata_len; i++) {
#if FLAC__HAS_INT128
				FLAC__int64 hat_x_i = (FLAC__int64)(3*(FLAC__int128)data[i-1] - 3*(FLAC__int128)data[i-2] + (FLAC__int128)data[i-3]);
#else
				FLAC__int64 hat_x_i = (FLAC__int64)(3*(FLAC__uint64)data[i-1] - 3*(FLAC__uint64)data[i-2] + (FLAC__uint64)data[i-3]);
#endif
				uint32_t d = delta[order + i];
				uint64_t mask = (d > 0) ? (((uint64_t)1 << d) - 1) : 0;
				uint64_t R_i = (-(uint64_t)hat_x_i) & mask;
				uint64_t r_i = ((uint64_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int64)((FLAC__uint64)hat_x_i + r_i);
			}
			break;
		case 4:
			for(i = 0; i < idata_len; i++) {
#if FLAC__HAS_INT128
				FLAC__int64 hat_x_i = (FLAC__int64)(4*(FLAC__int128)data[i-1] - 6*(FLAC__int128)data[i-2] + 4*(FLAC__int128)data[i-3] - (FLAC__int128)data[i-4]);
#else
				FLAC__int64 hat_x_i = (FLAC__int64)(4*(FLAC__uint64)data[i-1] - 6*(FLAC__uint64)data[i-2] + 4*(FLAC__uint64)data[i-3] - (FLAC__uint64)data[i-4]);
#endif
				uint32_t d = delta[order + i];
				uint64_t mask = (d > 0) ? (((uint64_t)1 << d) - 1) : 0;
				uint64_t R_i = (-(uint64_t)hat_x_i) & mask;
				uint64_t r_i = ((uint64_t)residual[i] << d) | R_i;
				data[i] = (FLAC__int64)((FLAC__uint64)hat_x_i + r_i);
			}
			break;
		default:
			FLAC__ASSERT(0);
	}
}

void FLAC__lpc_restore_signal_32bit_dpa(const FLAC__int32 residual[], const uint8_t delta[], uint32_t data_len, const FLAC__int32 qlp_coeff[], uint32_t order, int lp_quantization, FLAC__int32 data[])
{
	int i;

	FLAC__ASSERT(order > 0);
	FLAC__ASSERT(order <= 32);

	for(i = 0; i < (int)data_len; i++) {
		FLAC__int64 sum = 0;
		switch(order) {
			case 32: sum += (FLAC__int64)qlp_coeff[31] * (FLAC__int64)data[i-32]; /* Falls through. */
			case 31: sum += (FLAC__int64)qlp_coeff[30] * (FLAC__int64)data[i-31]; /* Falls through. */
			case 30: sum += (FLAC__int64)qlp_coeff[29] * (FLAC__int64)data[i-30]; /* Falls through. */
			case 29: sum += (FLAC__int64)qlp_coeff[28] * (FLAC__int64)data[i-29]; /* Falls through. */
			case 28: sum += (FLAC__int64)qlp_coeff[27] * (FLAC__int64)data[i-28]; /* Falls through. */
			case 27: sum += (FLAC__int64)qlp_coeff[26] * (FLAC__int64)data[i-27]; /* Falls through. */
			case 26: sum += (FLAC__int64)qlp_coeff[25] * (FLAC__int64)data[i-26]; /* Falls through. */
			case 25: sum += (FLAC__int64)qlp_coeff[24] * (FLAC__int64)data[i-25]; /* Falls through. */
			case 24: sum += (FLAC__int64)qlp_coeff[23] * (FLAC__int64)data[i-24]; /* Falls through. */
			case 23: sum += (FLAC__int64)qlp_coeff[22] * (FLAC__int64)data[i-23]; /* Falls through. */
			case 22: sum += (FLAC__int64)qlp_coeff[21] * (FLAC__int64)data[i-22]; /* Falls through. */
			case 21: sum += (FLAC__int64)qlp_coeff[20] * (FLAC__int64)data[i-21]; /* Falls through. */
			case 20: sum += (FLAC__int64)qlp_coeff[19] * (FLAC__int64)data[i-20]; /* Falls through. */
			case 19: sum += (FLAC__int64)qlp_coeff[18] * (FLAC__int64)data[i-19]; /* Falls through. */
			case 18: sum += (FLAC__int64)qlp_coeff[17] * (FLAC__int64)data[i-18]; /* Falls through. */
			case 17: sum += (FLAC__int64)qlp_coeff[16] * (FLAC__int64)data[i-17]; /* Falls through. */
			case 16: sum += (FLAC__int64)qlp_coeff[15] * (FLAC__int64)data[i-16]; /* Falls through. */
			case 15: sum += (FLAC__int64)qlp_coeff[14] * (FLAC__int64)data[i-15]; /* Falls through. */
			case 14: sum += (FLAC__int64)qlp_coeff[13] * (FLAC__int64)data[i-14]; /* Falls through. */
			case 13: sum += (FLAC__int64)qlp_coeff[12] * (FLAC__int64)data[i-13]; /* Falls through. */
			case 12: sum += (FLAC__int64)qlp_coeff[11] * (FLAC__int64)data[i-12]; /* Falls through. */
			case 11: sum += (FLAC__int64)qlp_coeff[10] * (FLAC__int64)data[i-11]; /* Falls through. */
			case 10: sum += (FLAC__int64)qlp_coeff[ 9] * (FLAC__int64)data[i-10]; /* Falls through. */
			case  9: sum += (FLAC__int64)qlp_coeff[ 8] * (FLAC__int64)data[i- 9]; /* Falls through. */
			case  8: sum += (FLAC__int64)qlp_coeff[ 7] * (FLAC__int64)data[i- 8]; /* Falls through. */
			case  7: sum += (FLAC__int64)qlp_coeff[ 6] * (FLAC__int64)data[i- 7]; /* Falls through. */
			case  6: sum += (FLAC__int64)qlp_coeff[ 5] * (FLAC__int64)data[i- 6]; /* Falls through. */
			case  5: sum += (FLAC__int64)qlp_coeff[ 4] * (FLAC__int64)data[i- 5]; /* Falls through. */
			case  4: sum += (FLAC__int64)qlp_coeff[ 3] * (FLAC__int64)data[i- 4]; /* Falls through. */
			case  3: sum += (FLAC__int64)qlp_coeff[ 2] * (FLAC__int64)data[i- 3]; /* Falls through. */
			case  2: sum += (FLAC__int64)qlp_coeff[ 1] * (FLAC__int64)data[i- 2]; /* Falls through. */
			case  1: sum += (FLAC__int64)qlp_coeff[ 0] * (FLAC__int64)data[i- 1];
		}
		FLAC__int32 hat_x_i = (FLAC__int32)(sum >> lp_quantization);
		uint32_t d = delta[order + i];
		uint32_t mask = (d > 0) ? (((uint32_t)1 << d) - 1) : 0;
		uint32_t R_i = (-(uint32_t)hat_x_i) & mask;
		uint32_t r_i = ((uint32_t)residual[i] << d) | R_i;
		data[i] = (FLAC__int32)((uint32_t)hat_x_i + r_i);
	}
}

void FLAC__lpc_restore_signal_wide_64bit_res64_dpa(const FLAC__int64 residual[], const uint8_t delta[], uint32_t data_len, const FLAC__int32 qlp_coeff[], uint32_t order, int lp_quantization, FLAC__int64 data[])
{
	int i;

	FLAC__ASSERT(order > 0);
	FLAC__ASSERT(order <= 32);

	for(i = 0; i < (int)data_len; i++) {
#if FLAC__HAS_INT128
		FLAC__int128 sum = 0;
		switch(order) {
			case 32: sum += (FLAC__int128)qlp_coeff[31] * (FLAC__int128)data[i-32]; /* Falls through. */
			case 31: sum += (FLAC__int128)qlp_coeff[30] * (FLAC__int128)data[i-31]; /* Falls through. */
			case 30: sum += (FLAC__int128)qlp_coeff[29] * (FLAC__int128)data[i-30]; /* Falls through. */
			case 29: sum += (FLAC__int128)qlp_coeff[28] * (FLAC__int128)data[i-29]; /* Falls through. */
			case 28: sum += (FLAC__int128)qlp_coeff[27] * (FLAC__int128)data[i-28]; /* Falls through. */
			case 27: sum += (FLAC__int128)qlp_coeff[26] * (FLAC__int128)data[i-27]; /* Falls through. */
			case 26: sum += (FLAC__int128)qlp_coeff[25] * (FLAC__int128)data[i-26]; /* Falls through. */
			case 25: sum += (FLAC__int128)qlp_coeff[24] * (FLAC__int128)data[i-25]; /* Falls through. */
			case 24: sum += (FLAC__int128)qlp_coeff[23] * (FLAC__int128)data[i-24]; /* Falls through. */
			case 23: sum += (FLAC__int128)qlp_coeff[22] * (FLAC__int128)data[i-23]; /* Falls through. */
			case 22: sum += (FLAC__int128)qlp_coeff[21] * (FLAC__int128)data[i-22]; /* Falls through. */
			case 21: sum += (FLAC__int128)qlp_coeff[20] * (FLAC__int128)data[i-21]; /* Falls through. */
			case 20: sum += (FLAC__int128)qlp_coeff[19] * (FLAC__int128)data[i-20]; /* Falls through. */
			case 19: sum += (FLAC__int128)qlp_coeff[18] * (FLAC__int128)data[i-19]; /* Falls through. */
			case 18: sum += (FLAC__int128)qlp_coeff[17] * (FLAC__int128)data[i-18]; /* Falls through. */
			case 17: sum += (FLAC__int128)qlp_coeff[16] * (FLAC__int128)data[i-17]; /* Falls through. */
			case 16: sum += (FLAC__int128)qlp_coeff[15] * (FLAC__int128)data[i-16]; /* Falls through. */
			case 15: sum += (FLAC__int128)qlp_coeff[14] * (FLAC__int128)data[i-15]; /* Falls through. */
			case 14: sum += (FLAC__int128)qlp_coeff[13] * (FLAC__int128)data[i-14]; /* Falls through. */
			case 13: sum += (FLAC__int128)qlp_coeff[12] * (FLAC__int128)data[i-13]; /* Falls through. */
			case 12: sum += (FLAC__int128)qlp_coeff[11] * (FLAC__int128)data[i-12]; /* Falls through. */
			case 11: sum += (FLAC__int128)qlp_coeff[10] * (FLAC__int128)data[i-11]; /* Falls through. */
			case 10: sum += (FLAC__int128)qlp_coeff[ 9] * (FLAC__int128)data[i-10]; /* Falls through. */
			case  9: sum += (FLAC__int128)qlp_coeff[ 8] * (FLAC__int128)data[i- 9]; /* Falls through. */
			case  8: sum += (FLAC__int128)qlp_coeff[ 7] * (FLAC__int128)data[i- 8]; /* Falls through. */
			case  7: sum += (FLAC__int128)qlp_coeff[ 6] * (FLAC__int128)data[i- 7]; /* Falls through. */
			case  6: sum += (FLAC__int128)qlp_coeff[ 5] * (FLAC__int128)data[i- 6]; /* Falls through. */
			case  5: sum += (FLAC__int128)qlp_coeff[ 4] * (FLAC__int128)data[i- 5]; /* Falls through. */
			case  4: sum += (FLAC__int128)qlp_coeff[ 3] * (FLAC__int128)data[i- 4]; /* Falls through. */
			case  3: sum += (FLAC__int128)qlp_coeff[ 2] * (FLAC__int128)data[i- 3]; /* Falls through. */
			case  2: sum += (FLAC__int128)qlp_coeff[ 1] * (FLAC__int128)data[i- 2]; /* Falls through. */
			case  1: sum += (FLAC__int128)qlp_coeff[ 0] * (FLAC__int128)data[i- 1];
		}
		FLAC__int64 hat_x_i = (FLAC__int64)(sum >> lp_quantization);
#else
		FLAC__int64 sum = 0;
		switch(order) {
			case 32: sum += (FLAC__int64)qlp_coeff[31] * data[i-32]; /* Falls through. */
			case 31: sum += (FLAC__int64)qlp_coeff[30] * data[i-31]; /* Falls through. */
			case 30: sum += (FLAC__int64)qlp_coeff[29] * data[i-30]; /* Falls through. */
			case 29: sum += (FLAC__int64)qlp_coeff[28] * data[i-29]; /* Falls through. */
			case 28: sum += (FLAC__int64)qlp_coeff[27] * data[i-28]; /* Falls through. */
			case 27: sum += (FLAC__int64)qlp_coeff[26] * data[i-27]; /* Falls through. */
			case 26: sum += (FLAC__int64)qlp_coeff[25] * data[i-26]; /* Falls through. */
			case 25: sum += (FLAC__int64)qlp_coeff[24] * data[i-25]; /* Falls through. */
			case 24: sum += (FLAC__int64)qlp_coeff[23] * data[i-24]; /* Falls through. */
			case 23: sum += (FLAC__int64)qlp_coeff[22] * data[i-23]; /* Falls through. */
			case 22: sum += (FLAC__int64)qlp_coeff[21] * data[i-22]; /* Falls through. */
			case 21: sum += (FLAC__int64)qlp_coeff[20] * data[i-21]; /* Falls through. */
			case 20: sum += (FLAC__int64)qlp_coeff[19] * data[i-20]; /* Falls through. */
			case 19: sum += (FLAC__int64)qlp_coeff[18] * data[i-19]; /* Falls through. */
			case 18: sum += (FLAC__int64)qlp_coeff[17] * data[i-18]; /* Falls through. */
			case 17: sum += (FLAC__int64)qlp_coeff[16] * data[i-17]; /* Falls through. */
			case 16: sum += (FLAC__int64)qlp_coeff[15] * data[i-16]; /* Falls through. */
			case 15: sum += (FLAC__int64)qlp_coeff[14] * data[i-15]; /* Falls through. */
			case 14: sum += (FLAC__int64)qlp_coeff[13] * data[i-14]; /* Falls through. */
			case 13: sum += (FLAC__int64)qlp_coeff[12] * data[i-13]; /* Falls through. */
			case 12: sum += (FLAC__int64)qlp_coeff[11] * data[i-12]; /* Falls through. */
			case 11: sum += (FLAC__int64)qlp_coeff[10] * data[i-11]; /* Falls through. */
			case 10: sum += (FLAC__int64)qlp_coeff[ 9] * data[i-10]; /* Falls through. */
			case  9: sum += (FLAC__int64)qlp_coeff[ 8] * data[i- 9]; /* Falls through. */
			case  8: sum += (FLAC__int64)qlp_coeff[ 7] * data[i- 8]; /* Falls through. */
			case  7: sum += (FLAC__int64)qlp_coeff[ 6] * data[i- 7]; /* Falls through. */
			case  6: sum += (FLAC__int64)qlp_coeff[ 5] * data[i- 6]; /* Falls through. */
			case  5: sum += (FLAC__int64)qlp_coeff[ 4] * data[i- 5]; /* Falls through. */
			case  4: sum += (FLAC__int64)qlp_coeff[ 3] * data[i- 4]; /* Falls through. */
			case  3: sum += (FLAC__int64)qlp_coeff[ 2] * data[i- 3]; /* Falls through. */
			case  2: sum += (FLAC__int64)qlp_coeff[ 1] * data[i- 2]; /* Falls through. */
			case  1: sum += (FLAC__int64)qlp_coeff[ 0] * data[i- 1];
		}
		FLAC__int64 hat_x_i = (FLAC__int64)(sum >> lp_quantization);
#endif
		uint32_t d = delta[order + i];
		uint64_t mask = (d > 0) ? (((uint64_t)1 << d) - 1) : 0;
		uint64_t R_i = (-(uint64_t)hat_x_i) & mask;
		uint64_t r_i = ((uint64_t)residual[i] << d) | R_i;
		data[i] = (FLAC__int64)((FLAC__uint64)hat_x_i + r_i);
	}
}
