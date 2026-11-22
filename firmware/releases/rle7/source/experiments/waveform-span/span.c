/* SPDX-License-Identifier: GPL-3.0-only */
#include "../draw-stream/draw_queue.h"
#include "span.h"

extern int ds100_reviewed_reserve(ds100_draw_queue *queue, unsigned bytes);
extern void ds100_reviewed_q8(ds100_draw_queue *queue, uint8_t byte);
extern void ds100_reviewed_q16(ds100_draw_queue *queue, uint16_t word);

enum { COLORS = 16 };
_Static_assert(WAVEFORM_HEIGHT <= 2u * UINT8_MAX, "The snapshot has at most one long run.");

/* Return 0 for the original encoder. No queue state changes until the run
 * snapshot is complete. Accepted input words are each read exactly once. */
int ds100_waveform_try(ds100_draw_queue *queue, uint16_t x, uint16_t y,
                       int reverse, const uint16_t *pixels) {
    if (!pixels || !queue || queue->mode != DRAW_ACTIVE ||
            x >= 480u || y > 320u - WAVEFORM_HEIGHT) return 0;

    uint16_t codes[WAVEFORM_HEIGHT], palette[COLORS];
    uint8_t lengths[WAVEFORM_HEIGHT];
    unsigned count = 0, indexed = 0, colors = 0, tokens = 0, long_length = 0;
    const uint16_t *end = pixels + WAVEFORM_HEIGHT;
    const uint16_t *start = pixels;
    uint16_t color = *pixels++;

    for (;;) {
        uint16_t next = color;
        while (pixels != end) {
            next = *pixels++;
            if (next != color) break;
        }
        const uint16_t *after = next == color ? end : pixels - 1;
        const unsigned length = (unsigned)(after - start);
        uint16_t code = color;
        /* Keep palette indices up to the first color outside the palette.
         * Store the remaining colors directly for RAW and RLE. */
        if (indexed == count) {
            unsigned index = 0;
            while (index < colors && palette[index] != color) ++index;
            if (index < COLORS) {
                if (index == colors) palette[colors++] = color;
                code = (uint16_t)index;
                ++indexed;
                tokens += 1u + (length >= 16u) + (length >= 144u);
            }
        }
        if (length > UINT8_MAX) long_length = length;
        lengths[count] = length > UINT8_MAX ? 0 : (uint8_t)length;
        codes[count++] = code;
        if (after == end) break;
        start = after;
        color = next;
    }

    const unsigned raw_bytes = WAVEFORM_HEIGHT * 2u;
    const unsigned rle_bytes = 4u * count;
    const unsigned palette_bytes = 1u + 2u * colors + tokens;
    unsigned encoding = rle_bytes < raw_bytes ? 1u : 0u;
    unsigned bytes = encoding ? rle_bytes : raw_bytes;
    if (indexed == count && palette_bytes < bytes) {
        encoding = 2;
        bytes = palette_bytes;
    }
    if (!ds100_reviewed_reserve(queue, 12u + bytes)) return 1;

    ds100_reviewed_q8(queue, 5);
    ds100_reviewed_q8(queue, 0);
    ds100_reviewed_q16(queue, (uint16_t)(8u + bytes));
    ds100_reviewed_q16(queue, x);
    ds100_reviewed_q16(queue, y);
    ds100_reviewed_q16(queue, WAVEFORM_HEIGHT);
    ds100_reviewed_q8(queue, (uint8_t)encoding);
    ds100_reviewed_q8(queue, 0);

    if (encoding == 2u) {
        ds100_reviewed_q8(queue, (uint8_t)colors);
        for (unsigned i = 0; i < colors; ++i) ds100_reviewed_q16(queue, palette[i]);
    }

    unsigned raw_at = queue->write_at;
    for (unsigned i = 0; i < count; ++i) {
        const unsigned position = reverse ? count - 1u - i : i;
        const uint16_t code = codes[position];
        unsigned length = lengths[position];
        if (!length) length = long_length;
        if (encoding != 2u) {
            const uint16_t value = position < indexed ? palette[code] : code;
            if (encoding == 1u) {
                ds100_reviewed_q16(queue, (uint16_t)length);
                ds100_reviewed_q16(queue, value);
            } else {
                uint8_t *ring = queue->queue;
                const unsigned capacity = queue->capacity;
                for (unsigned pixel = 0; pixel < length; ++pixel) {
                    ring[raw_at++] = (uint8_t)value;
                    if (raw_at == capacity) raw_at = 0;
                    ring[raw_at++] = (uint8_t)(value >> 8);
                    if (raw_at == capacity) raw_at = 0;
                }
            }
        } else {
            ds100_reviewed_q8(queue, (uint8_t)((code << 4) | (length < 16u ? length - 1u : 15u)));
            if (length >= 16u) {
                length -= 16u;
                if (length >= 128u) {
                    ds100_reviewed_q8(queue, (uint8_t)((length & 127u) | 128u));
                    length >>= 7;
                }
                ds100_reviewed_q8(queue, (uint8_t)length);
            }
        }
    }
    if (!encoding) {
        queue->write_at = raw_at;
        queue->used += raw_bytes;
    }
    return 1;
}
