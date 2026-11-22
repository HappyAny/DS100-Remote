/* SPDX-License-Identifier: GPL-3.0-only */
#include "../draw-stream/draw_queue.h"

enum { BODY_BYTES = 4076, ROWS = 320, CACHE_BYTES = BODY_BYTES + 2 };
enum { BOT_DATA_IN = 1, BOT_SEND_CSW = 3 };

extern ds100_draw_queue *ds100_prefetch_queue(void);
extern void ds100_prefetch_original_hold_tick(void);
extern size_t ds100_prefetch_original_packet(ds100_draw_queue *, uint8_t *, size_t, ds100_read_row, void *);
extern void ds100_prefetch_read_row(void *, uint16_t, uint16_t *);
extern unsigned ds100_prefetch_timing_switch(unsigned);

#define USB_STATE (*(const volatile uint8_t *)(uintptr_t)0x20021515u)
#define TIMING_FLAGS (*(const volatile uint32_t *)(uintptr_t)0x2002ffc4u)

static uint32_t record_count(const uint8_t *data) {
    return (uint32_t)data[12] | ((uint32_t)data[13] << 8) |
           ((uint32_t)data[14] << 16) | ((uint32_t)data[15] << 24);
}

static int busy(void) {
    const uint8_t phase = USB_STATE;
    return phase == BOT_DATA_IN || phase == BOT_SEND_CSW;
}

/* Only the GUI task can change the queue. In BASELINE, no drawing records
 * occupy the ring. Reserve read_at for the cached packet length in this mode.
 * The original START resets the entire queue and discards any old packet. */
void ds100_prefetch_hold_tick(void) {
    ds100_prefetch_original_hold_tick();
    if (!busy()) return;
    ds100_draw_queue *const queue = ds100_prefetch_queue();
    if (!queue || queue->mode != DRAW_BASELINE || queue->read_at || queue->write_at ||
            queue->records || queue->used || queue->baseline_y >= ROWS ||
            !queue->queue || queue->capacity < CACHE_BYTES) return;

    ds100_draw_queue shadow;
    const volatile uint8_t *source = (const volatile uint8_t *)queue;
    volatile uint8_t *copy = (volatile uint8_t *)&shadow;
    for (unsigned i = 0; i < sizeof(shadow); ++i) copy[i] = source[i];

    const int timed = !!(TIMING_FLAGS & 32u); /* ACT2 detailed mode */
    const unsigned section = timed ? ds100_prefetch_timing_switch(3) : 0;
    const size_t length = ds100_prefetch_original_packet(&shadow, queue->queue, BODY_BYTES,
                                                          ds100_prefetch_read_row, 0);
    if (timed) ds100_prefetch_timing_switch(section);
    if (length < 20 || length > BODY_BYTES || shadow.baseline_y <= queue->baseline_y ||
            shadow.baseline_y > ROWS) return;
    queue->queue[BODY_BYTES] = (uint8_t)shadow.baseline_y;
    queue->queue[BODY_BYTES + 1] = (uint8_t)(shadow.baseline_y >> 8);
    queue->read_at = (uint32_t)length;
}

size_t ds100_prefetch_consume(ds100_draw_queue *queue, uint8_t *out, size_t capacity,
                              ds100_read_row reader, void *context) {
    const uint32_t length = queue->read_at;
    if (out && capacity == BODY_BYTES && reader == ds100_prefetch_read_row && !context &&
            length >= 20u && length <= BODY_BYTES) {
        const uint8_t *const cached = queue->queue;
        const unsigned y = cached[BODY_BYTES] | ((unsigned)cached[BODY_BYTES + 1] << 8);
        if (y > queue->baseline_y && y <= ROWS) {
            for (unsigned i = 0; i < length; ++i) out[i] = cached[i];
            queue->read_at = 0;
            queue->baseline_y = y;
            queue->next_record += record_count(cached);
            queue->reset_pending = 0;
            if (y == ROWS) queue->mode = DRAW_ACTIVE;
            return length;
        }
    }
    queue->read_at = 0;
    return ds100_prefetch_original_packet(queue, out, capacity, reader, context);
}
