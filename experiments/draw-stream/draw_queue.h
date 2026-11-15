#ifndef DS100_DRAW_QUEUE_H
#define DS100_DRAW_QUEUE_H
#include <stddef.h>
#include <stdint.h>

enum { DRAW_OFF, DRAW_BASELINE, DRAW_ACTIVE, DRAW_FROZEN, DRAW_FAULT };
enum { DRAW_OVERFLOW = 1, DRAW_GEOMETRY = 2 };
typedef struct {
    uint8_t *queue;
    uint32_t capacity, write_at, read_at, used, records;
    uint32_t epoch, next_record, frame_id, mode, dirty, fault;
    uint32_t baseline_y, reset_pending, clipped, peak_used;
} ds100_draw_queue;

typedef void (*ds100_read_row)(void *, uint16_t, uint16_t *);
void ds100_draw_init(ds100_draw_queue *, uint8_t *, uint32_t);
void ds100_draw_start(ds100_draw_queue *, uint32_t);
void ds100_draw_point(ds100_draw_queue *, uint16_t, uint16_t, uint16_t);
void ds100_draw_fill(ds100_draw_queue *, uint16_t, uint16_t, uint16_t, uint16_t, uint16_t);
void ds100_draw_span(ds100_draw_queue *, uint16_t, uint16_t, uint16_t, const uint16_t *);
void ds100_draw_pixels(ds100_draw_queue *, uint16_t, uint16_t, uint16_t, int, int, const uint16_t *);
void ds100_draw_bitmap(ds100_draw_queue *, uint16_t, uint16_t, uint16_t, uint16_t, const uint16_t *);
void ds100_draw_clear(ds100_draw_queue *, uint16_t);
void ds100_draw_present(ds100_draw_queue *);
size_t ds100_draw_packet(ds100_draw_queue *, uint8_t *, size_t, ds100_read_row, void *);
#endif
