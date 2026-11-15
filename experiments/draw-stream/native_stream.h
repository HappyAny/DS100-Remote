#ifndef DS100_NATIVE_STREAM_H
#define DS100_NATIVE_STREAM_H
#include "draw_queue.h"
void ds100_stream_init(void);
void ds100_stream_tick(void);
int ds100_stream_holds_render(void);
ds100_draw_queue *ds100_stream_queue(void);
void ds100_stream_before_capture(void);
void ds100_stream_point(uint16_t, uint16_t, uint16_t);
void ds100_stream_fill(uint16_t, uint16_t, uint16_t, uint16_t, uint16_t);
void ds100_stream_span(uint16_t, uint16_t, uint16_t, const uint16_t *);
void ds100_stream_bitmap(uint16_t, uint16_t, uint16_t, uint16_t, const uint16_t *);
void ds100_stream_scan_dir(uint8_t);
void ds100_stream_register_redraw(void *);
size_t ds100_stream_handle(const uint8_t *, uint8_t *, size_t);
void ds100_stream_clear(uint16_t);
#endif
