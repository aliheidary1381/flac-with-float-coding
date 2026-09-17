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
 * and compressible 32-bit integer samples
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
   3. splitting the "exponent" (8 bit) and "sign+significand" (24 bit) parts of floats into two subframes
		(current approach). my tests resulted in about ~68% ratio, even with no bit manipulation
   4. splitting + subtracting an (automatically recognised) DC offset from the "exponents" channel
		and	storing the offset in each frame header (i.e. unsigned to signed conversion), and using that to
		shift the "significand" channel. could be better (next milestone).
		my guess would be a *consistent* ~60% ratio, at least.
*/

#ifdef HAVE_CONFIG_H
#  include <config.h>
#endif

#include <stddef.h>
#include <stdint.h>
#include "private/transform_float.h"
#include "private/md5.h"
#include "share/alloc.h"

/*
 * Splits 32-bit IEEE 754 floating-point samples into two integer subframe signals:
 *  - Exponent (8 bits, signed int8_t: bits 23-30 of the float)
 *  - Sign + Significand (24 bits: sign at bit 23, significand fraction at bits 0-22, sign-extended to 32 bits)
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
		exp_signal[i] = (FLAC__int32)(int8_t)exp;
		sign_mant_signal[i] = (FLAC__int32)(sign_mant << 8) >> 8;
	}
}

/*
 * Recombines two integer subframe signals (8-bit exponent and 24-bit sign+significand)
 * into standard 32-bit IEEE 754 floating-point raw words.
 */
void FLAC__combine_subframe_signals_to_f32_buffer(uint32_t *dest, const FLAC__int32 *exp_signal, const FLAC__int32 *sign_mant_signal, size_t n)
{
	size_t i;
	for (i = 0; i < n; i++) {
		const uint32_t exp = (uint32_t)exp_signal[i] & 0xFF;
		const uint32_t sign_mant = (uint32_t)sign_mant_signal[i] & 0xFFFFFF;
		const uint32_t sign = (sign_mant >> 23) & 1;
		const uint32_t mant = sign_mant & 0x7FFFFF;
		dest[i] = (sign << 31) | (exp << 23) | mant;
	}
}

/*
 * Accumulates MD5 checksum over the combined/split integer subframe signals:
 * 4 bytes per sample per channel:
 *   [Byte 0: 8-bit exponent, Byte 1..3: 24-bit sign+significand (little-endian)]
 */
FLAC__bool FLAC__MD5Accumulate_float_split(FLAC__MD5Context *ctx, const FLAC__int32 * const signals[], uint32_t channels, uint32_t samples)
{
	const size_t bytes_needed = (size_t)channels * (size_t)samples * 4;
	uint32_t sample, channel;
	FLAC__byte *buf;

	if (channels > 1024)
		return false;
	if ((size_t)channels * 4 > SIZE_MAX / (size_t)samples)
		return false;

	if (ctx->capacity < bytes_needed) {
		if (0 == (ctx->internal_buf.p8 = safe_realloc_(ctx->internal_buf.p8, bytes_needed))) {
			if (0 == (ctx->internal_buf.p8 = safe_malloc_(bytes_needed))) {
				ctx->capacity = 0;
				return false;
			}
		}
		ctx->capacity = bytes_needed;
	}

	buf = ctx->internal_buf.p8;
	for (sample = 0; sample < samples; sample++) {
		for (channel = 0; channel < channels; channel++) {
			const FLAC__int32 exp = signals[2 * channel][sample];
			const FLAC__int32 sign_mant = signals[2 * channel + 1][sample];
			*buf++ = (FLAC__byte)(exp & 0xFF);
			*buf++ = (FLAC__byte)(sign_mant & 0xFF);
			*buf++ = (FLAC__byte)((sign_mant >> 8) & 0xFF);
			*buf++ = (FLAC__byte)((sign_mant >> 16) & 0xFF);
		}
	}

	FLAC__MD5Update(ctx, ctx->internal_buf.p8, bytes_needed);
	return true;
}
