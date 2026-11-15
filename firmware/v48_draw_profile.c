/* Measure drawing traffic before committing to a 30 fps transport design. */
#include "ds100_remote.h"

#define PROFILE_MAGIC UINT32_C(0x35505244)
typedef struct {
    uint32_t magic, active, epoch, loop_passes;
    uint32_t nonempty_passes, points, rectangles, spans;
    uint32_t bitmap_rows, pixels, encoded_bytes, raw_bytes;
    uint32_t pass_bytes, peak_pass_bytes, invalid_rectangles, reserved;
} draw_profile_state;

/* The pinned scatter table initializes [0x20020000, 0x2002ff90).
 * Initial SP is 0x2002ff90 and grows down. This state occupies 64 of the
 * remaining 112 bytes below the end of the 192 KiB main SRAM region.
 * ds100_v48_profile_init clears it after the stock heap initialization.
 */
static draw_profile_state profile;

extern void ds100_v48_original_point(uint16_t x, uint16_t y, uint16_t color);
extern void ds100_v48_original_rect(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color);
extern void ds100_v48_original_bitmap(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, const uint16_t *pixels);
extern void ds100_v48_original_span_a(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels);
extern void ds100_v48_original_span_b(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels);

static void clear_profile(void) {
    uint32_t *p = (uint32_t *)(void *)&profile;
    unsigned i;
    for (i = 0; i < sizeof(profile) / sizeof(uint32_t); ++i) p[i] = 0;
    profile.magic = PROFILE_MAGIC;
}

void ds100_v48_profile_init(void) {
    ((void (*)(void))(uintptr_t)0x0801032bu)();
    clear_profile();
}

static int enabled(void) { return profile.magic == PROFILE_MAGIC && profile.active; }

static void record_bytes(uint32_t encoded, uint32_t raw, uint32_t pixels) {
    profile.encoded_bytes += encoded;
    profile.raw_bytes += raw;
    profile.pass_bytes += encoded;
    profile.pixels += pixels;
}

static int rectangle_valid(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    if (x0 > x1 || y0 > y1 || x1 >= 480u || y1 >= 320u) {
        ++profile.invalid_rectangles;
        return 0;
    }
    return 1;
}

static void record_span(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    uint32_t i, count, runs = 1, rle;
    if (!pixels || !rectangle_valid(x0, y, x1, y)) return;
    count = (uint32_t)x1 - x0 + 1u;
    for (i = 1; i < count; ++i) if (pixels[i] != pixels[i - 1u]) ++runs;
    rle = runs * 4u;
    record_bytes(12u + ((rle < count * 2u) ? rle : count * 2u), 12u + count * 2u, count);
}

void ds100_v48_profile_point(uint16_t x, uint16_t y, uint16_t color) {
    if (enabled()) {
        ++profile.points;
        if (rectangle_valid(x, y, x, y)) record_bytes(10u, 10u, 1u);
    }
    ds100_v48_original_point(x, y, color);
}

void ds100_v48_profile_rect(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color) {
    if (enabled()) {
        ++profile.rectangles;
        if (rectangle_valid(x0, y0, x1, y1))
            record_bytes(14u, 14u, ((uint32_t)x1 - x0 + 1u) * ((uint32_t)y1 - y0 + 1u));
    }
    ds100_v48_original_rect(x0, y0, x1, y1, color);
}

void ds100_v48_profile_bitmap(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, const uint16_t *pixels) {
    if (enabled() && pixels && rectangle_valid(x0, y0, x1, y1)) {
        uint32_t row, width = (uint32_t)x1 - x0 + 1u, height = (uint32_t)y1 - y0 + 1u;
        profile.bitmap_rows += height;
        for (row = 0; row < height; ++row) record_span(x0, x1, (uint16_t)(y0 + row), pixels + row * width);
    }
    ds100_v48_original_bitmap(x0, y0, x1, y1, pixels);
}

void ds100_v48_profile_span_a(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    if (enabled()) { ++profile.spans; record_span(x0, x1, y, pixels); }
    ds100_v48_original_span_a(x0, x1, y, pixels);
}

void ds100_v48_profile_span_b(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    if (enabled()) { ++profile.spans; record_span(x0, x1, y, pixels); }
    ds100_v48_original_span_b(x0, x1, y, pixels);
}

void ds100_v48_profile_pass(void) {
    if (!enabled()) return;
    ++profile.loop_passes;
    if (profile.pass_bytes) {
        uint32_t size = profile.pass_bytes + 8u;
        ++profile.nonempty_passes;
        profile.encoded_bytes += 8u;
        profile.raw_bytes += 8u;
        if (size > profile.peak_pass_bytes) profile.peak_pass_bytes = size;
        profile.pass_bytes = 0;
    }
}

int ds100_v48_draw_profile(void *context, uint16_t action, uint8_t *out, size_t capacity, size_t *length) {
    const uint32_t *words = (const uint32_t *)(const void *)&profile;
    unsigned i;
    (void)context;
    if (!out || !length || capacity < sizeof(profile) || action > 2u || profile.magic != PROFILE_MAGIC) return -1;
    if (action == 1u) {
        uint32_t epoch = profile.epoch + 1u;
        clear_profile(); profile.epoch = epoch; profile.active = 1u;
    } else if (!action) profile.active = 0;
    for (i = 0; i < sizeof(profile) / sizeof(uint32_t); ++i) {
        uint32_t value = words[i];
        out[i * 4u] = (uint8_t)value; out[i * 4u + 1u] = (uint8_t)(value >> 8);
        out[i * 4u + 2u] = (uint8_t)(value >> 16); out[i * 4u + 3u] = (uint8_t)(value >> 24);
    }
    *length = sizeof(profile);
    return 0;
}
