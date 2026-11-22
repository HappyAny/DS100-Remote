/* SPDX-License-Identifier: GPL-3.0-only */
#include "../draw-stream/draw_queue.h"
#include "../waveform-span/span.h"
#include "writer.h"

extern int ds100_reviewed_reserve(ds100_draw_queue *, unsigned);
extern volatile uint32_t ds100_capture_state[];

enum { COLORS = 16 };
_Static_assert(WAVEFORM_HEIGHT <= 2u * UINT8_MAX, "At most one run needs a wide length.");

/* Read halfwords in the original order. The first comparison handles short
 * runs. A group check permits eight post-indexed loads with no overread. */
static inline __attribute__((always_inline))
const volatile uint16_t *scan_run(const volatile uint16_t *pixels,
                                  const volatile uint16_t *end,
                                  uint16_t color, uint16_t *next) {
    uint32_t value;
    __asm__ volatile (
        "cmp %[p], %[end]\n"
        "beq 4f\n"
        "ldrh %[v], [%[p]], #2\n"
        "cmp %[v], %[color]\n"
        "bne 3f\n"
        "1:\n"
        "cmp %[p], %[last_group]\n"
        "bhi 2f\n"
        ".rept 8\n"
        "ldrh %[v], [%[p]], #2\n"
        "cmp %[v], %[color]\n"
        "bne 3f\n"
        ".endr\n"
        "b 1b\n"
        "2:\n"
        "cmp %[p], %[end]\n"
        "beq 3f\n"
        "ldrh %[v], [%[p]], #2\n"
        "cmp %[v], %[color]\n"
        "beq 2b\n"
        "b 3f\n"
        "4:\n"
        "mov %[v], %[color]\n"
        "3:"
        : [p] "+&r" (pixels), [v] "=&r" (value)
        : [end] "r" (end), [last_group] "r" (end - 8), [color] "r" ((uint32_t)color)
        : "cc", "memory");
    *next = (uint16_t)value;
    return pixels;
}

typedef struct {
    uint8_t *cursor, *base, *end;
} writer;

static inline __attribute__((always_inline)) void write_byte(writer *out, uint8_t value) {
    *out->cursor++ = value;
    if (out->cursor == out->end) out->cursor = out->base;
}

static inline __attribute__((always_inline)) void write_word(writer *out, uint16_t value) {
    write_byte(out, (uint8_t)value);
    write_byte(out, (uint8_t)(value >> 8));
}

static void count_record(void) {
    enum { ACTIVE_WRITER = CAPTURE_RUNNING | (1u << WRITER_MODE_BIT) };
    volatile uint32_t *flags = ds100_capture_state + CAPTURE_FLAGS_OFFSET / sizeof(uint32_t);
    uint32_t mask;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r" (mask) :: "memory");
    const uint32_t before = *flags;
    if ((before & ACTIVE_WRITER) == ACTIVE_WRITER &&
            before >> WRITER_COUNT_SHIFT != UINT32_MAX >> WRITER_COUNT_SHIFT) {
        *flags = before + (UINT32_C(1) << WRITER_COUNT_SHIFT);
    }
    __asm__ volatile ("msr primask, %0" :: "r" (mask) : "memory");
}

int ds100_waveform_writer_try(ds100_draw_queue *queue, uint16_t x, uint16_t y,
                             int reverse, const volatile uint16_t *pixels) {
    if (!pixels || !queue || queue->mode != DRAW_ACTIVE ||
            x >= 480u || y > 320u - WAVEFORM_HEIGHT) return 0;

    uint16_t codes[WAVEFORM_HEIGHT], palette[COLORS];
    uint8_t lengths[WAVEFORM_HEIGHT];
    unsigned count = 0, indexed = 0, colors = 0, tokens = 0, long_length = 0;
    const volatile uint16_t *end = pixels + WAVEFORM_HEIGHT;
    const volatile uint16_t *start = pixels;
    uint16_t color = *pixels++;

    for (;;) {
        uint16_t next;
        pixels = scan_run(pixels, end, color, &next);
        const volatile uint16_t *after = next == color ? end : pixels - 1;
        const unsigned length = (unsigned)(after - start);
        uint16_t code = color;
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
    const unsigned record_bytes = 12u + bytes;
    if (!ds100_reviewed_reserve(queue, record_bytes)) return 1;

    /* The foreground owns this ring through the entire record. USB service
     * can drain it before or after this call. Commit the cursor once. */
    writer out = {queue->queue + queue->write_at, queue->queue, queue->queue + queue->capacity};
    write_byte(&out, 5);
    write_byte(&out, 0);
    write_word(&out, (uint16_t)(8u + bytes));
    write_word(&out, x);
    write_word(&out, y);
    write_word(&out, WAVEFORM_HEIGHT);
    write_byte(&out, (uint8_t)encoding);
    write_byte(&out, 0);
    if (encoding == 2u) {
        write_byte(&out, (uint8_t)colors);
        for (unsigned i = 0; i < colors; ++i) write_word(&out, palette[i]);
    }

    unsigned position = reverse ? count - 1u : 0u;
    const unsigned stride = reverse ? UINT32_MAX : 1u;
    for (unsigned i = 0; i < count; ++i, position += stride) {
        const uint16_t code = codes[position];
        unsigned length = lengths[position];
        if (!length) length = long_length;
        if (encoding == 2u) {
            write_byte(&out, (uint8_t)((code << 4) | (length < 16u ? length - 1u : 15u)));
            if (length >= 16u) {
                length -= 16u;
                if (length >= 128u) {
                    write_byte(&out, (uint8_t)((length & 127u) | 128u));
                    length >>= 7;
                }
                write_byte(&out, (uint8_t)length);
            }
        } else {
            const uint16_t value = position < indexed ? palette[code] : code;
            if (encoding == 1u) {
                write_word(&out, (uint16_t)length);
                write_word(&out, value);
            } else {
                do {
                    write_word(&out, value);
                } while (--length);
            }
        }
    }
    queue->write_at = (uint32_t)(out.cursor - out.base);
    queue->used += record_bytes;
    count_record();
    return 1;
}
