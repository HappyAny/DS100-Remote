/* Keep R6 profiling and native drawing behavior while adding bounded records. */
#include "native_stream.h"
#define ds100_v48_profile_init ds100_r6_profile_init
#define ds100_v48_profile_point ds100_r6_profile_point
#define ds100_v48_profile_rect ds100_r6_profile_rect
#define ds100_v48_profile_bitmap ds100_r6_profile_bitmap
#define ds100_v48_profile_span_a ds100_r6_profile_span_a
#define ds100_v48_profile_span_b ds100_r6_profile_span_b
#define ds100_v48_profile_pass ds100_r6_profile_pass
#include "../../firmware/v48_draw_profile.c"
#undef ds100_v48_profile_init
#undef ds100_v48_profile_point
#undef ds100_v48_profile_rect
#undef ds100_v48_profile_bitmap
#undef ds100_v48_profile_span_a
#undef ds100_v48_profile_span_b
#undef ds100_v48_profile_pass

void ds100_v48_profile_init(void) { ds100_r6_profile_init(); ds100_stream_init(); }
void ds100_v48_profile_point(uint16_t x, uint16_t y, uint16_t color) {
    ds100_stream_point(x, y, color);
    ds100_r6_profile_point(x, y, color);
}
void ds100_v48_profile_rect(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color) {
    ds100_stream_fill(x0, y0, x1, y1, color);
    ds100_r6_profile_rect(x0, y0, x1, y1, color);
}
void ds100_v48_profile_bitmap(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, const uint16_t *pixels) {
    ds100_stream_bitmap(x0, y0, x1, y1, pixels);
    ds100_r6_profile_bitmap(x0, y0, x1, y1, pixels);
}
void ds100_v48_profile_span_a(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    ds100_stream_span(x0, x1, y, pixels);
    ds100_r6_profile_span_a(x0, x1, y, pixels);
}
void ds100_v48_profile_span_b(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    ds100_stream_span(x0, x1, y, pixels);
    ds100_r6_profile_span_b(x0, x1, y, pixels);
}
void ds100_v48_profile_pass(void) {
    ds100_draw_present(ds100_stream_queue());
    ds100_r6_profile_pass();
}
