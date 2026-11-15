/* R7: allocate a queue from the stock allocator. No sampling RAM is repurposed. */
#include "native_stream.h"
#include "../../firmware/ds100_remote.h"

#define QUEUE_BYTES 16384u
#define REDRAW_PERIOD 25u
typedef struct {
    ds100_draw_queue draw;
    uint32_t last_touch, baseline_direction;
    volatile uint32_t *accelerated_timer;
    uint32_t saved_period;
    uint8_t queue[];
} native_state;
static native_state *state;
static uint32_t generation;
static uint32_t direction;
static volatile uint32_t *redraw_timer;
extern void ds100_stream_original_clear(uint16_t);
extern void ds100_stream_original_scan_dir(uint8_t);

static uint32_t now(void) { return ((uint32_t (*)(void))(uintptr_t)0x08015385u)(); }
static void put16(uint8_t *p, uint16_t n) { p[0] = (uint8_t)n; p[1] = (uint8_t)(n >> 8); }
static void put32(uint8_t *p, uint32_t n) { put16(p, (uint16_t)n); put16(p + 2, (uint16_t)(n >> 16)); }
static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1] << 8)); }
static uint32_t get32(const uint8_t *p) { return (uint32_t)get16(p) | ((uint32_t)get16(p + 2) << 16); }

static int valid_widget(uintptr_t p) {
    const volatile uint32_t *words = (const volatile uint32_t *)p;
    if ((p & 3u) || !((p >= 0x10000008u && p <= 0x10010000u - 0x22cu) ||
                      (p >= 0x20021648u && p <= 0x20027e40u - 0x22cu))) return 0;
    /* Pinned waveform-widget vtable and the pure dirty-flag timer callback.
     * The context and callback are set before the constructor hook executes.
     */
    return words[0] == 0x08072840u && words[0x1f0u / 4u] == p &&
           words[0x1f4u / 4u] == 0x080271f7u;
}

static int valid_timer(const volatile uint32_t *timer) {
    uintptr_t p = (uintptr_t)timer;
    return p >= 0x1d4u && valid_widget(p - 0x1d4u);
}

void ds100_stream_register_redraw(void *widget) {
    if (valid_widget((uintptr_t)widget))
        redraw_timer = (volatile uint32_t *)((uintptr_t)widget + 0x1d4u);
}

static void restore_redraw(native_state *old) {
    if (old && old->saved_period && valid_timer(old->accelerated_timer) &&
            *old->accelerated_timer == REDRAW_PERIOD)
        *old->accelerated_timer = old->saved_period;
    if (old) { old->accelerated_timer = 0; old->saved_period = 0; }
}

static void accelerate_redraw(void) {
    restore_redraw(state);
    if (valid_timer(redraw_timer) && *redraw_timer == 500u) {
        state->accelerated_timer = redraw_timer;
        state->saved_period = *redraw_timer;
        *redraw_timer = REDRAW_PERIOD;
    }
}

static void stop(void) {
    native_state *old = state;
    state = 0;
    restore_redraw(old);
    if (old) ((void (*)(void *))(uintptr_t)0x080103f7u)(old);
}

void ds100_stream_init(void) { state = 0; generation = 0; direction = 0xa0u; redraw_timer = 0; }
void ds100_stream_tick(void) {
    if (state && (uint32_t)(now() - state->last_touch) >= 1000u) stop();
}
int ds100_stream_holds_render(void) {
    return state && (state->draw.mode == DRAW_BASELINE || state->draw.mode == DRAW_FROZEN);
}
ds100_draw_queue *ds100_stream_queue(void) { return state ? &state->draw : 0; }

static int start(void) {
    uintptr_t p;
    uint32_t capacity;
    const volatile uint16_t *lcd = (const volatile uint16_t *)(uintptr_t)0x2002cef0u;
    if (lcd[0] != 480u || lcd[1] != 320u || lcd[2] != 0x9488u) return -2;
    if (!state) {
        for (capacity = QUEUE_BYTES; capacity >= 4096u; capacity >>= 1) {
            state = ((void *(*)(uint32_t))(uintptr_t)0x08010491u)(sizeof(native_state) + capacity);
            if (state) break;
        }
        if (!state) return -1;
        p = (uintptr_t)state;
        /* Only the two ranges registered by the pinned allocator are accepted. */
        if ((p & 3u) || !((p >= 0x10000008u && p <= 0x10010000u - sizeof(native_state) - capacity) ||
                         (p >= 0x20021648u && p <= 0x20027e40u - sizeof(native_state) - capacity))) {
            /* An unexpected allocator result is not safe to read or free. */
            state = 0; return -1;
        }
        ds100_draw_init(&state->draw, state->queue, capacity);
        state->accelerated_timer = 0;
        state->saved_period = 0;
    }
    ++generation;
    if (!generation) ++generation;
    ds100_draw_start(&state->draw, generation);
    state->baseline_direction = direction;
    state->last_touch = now();
    accelerate_redraw();
    return 0;
}

void ds100_stream_scan_dir(uint8_t scan) {
    static const uint8_t portrait[8] = {0, 0x80, 0x40, 0xc0, 0x20, 0x60, 0xa0, 0xe0};
    static const uint8_t landscape[8] = {0xa0, 0xe0, 0x20, 0x60, 0x80, 0, 0xc0, 0x40};
    const volatile uint8_t *lcd = (const volatile uint8_t *)(uintptr_t)0x2002cef0u;
    ds100_stream_original_scan_dir(scan);
    direction = scan < 8u ? (lcd[6] ? landscape[scan] : portrait[scan]) : 0;
}

static void transform(uint16_t x, uint16_t y, uint16_t *ox, uint16_t *oy) {
    unsigned column = (direction & 0x20u) ? y : x;
    unsigned row = (direction & 0x20u) ? x : y;
    uint32_t base = state->baseline_direction;
    if (direction & 0x40u) column = 319u - column;
    if (direction & 0x80u) row = 479u - row;
    if (base & 0x40u) column = 319u - column;
    if (base & 0x80u) row = 479u - row;
    *ox = (uint16_t)((base & 0x20u) ? row : column);
    *oy = (uint16_t)((base & 0x20u) ? column : row);
}

static int visible(uint16_t x0, uint16_t y0, uint16_t *x1, uint16_t *y1) {
    unsigned width = (direction & 0x20u) ? 480u : 320u;
    unsigned height = (direction & 0x20u) ? 320u : 480u;
    if (!state || state->draw.mode != DRAW_ACTIVE) return 0;
    if (x0 > *x1 || y0 > *y1 || *x1 > width || *y1 > height) {
        state->draw.mode = DRAW_FAULT; state->draw.fault = DRAW_GEOMETRY; return 0;
    }
    if (x0 >= width || y0 >= height) { ++state->draw.clipped; return 0; }
    if (*x1 == width) { *x1 = (uint16_t)(width - 1u); ++state->draw.clipped; }
    if (*y1 == height) { *y1 = (uint16_t)(height - 1u); ++state->draw.clipped; }
    return 1;
}

void ds100_stream_point(uint16_t x, uint16_t y, uint16_t color) {
    uint16_t x1 = x, y1 = y, ox, oy;
    if (!visible(x, y, &x1, &y1)) return;
    ds100_stream_before_capture();
    transform(x, y, &ox, &oy);
    ds100_draw_point(&state->draw, ox, oy, color);
}

void ds100_stream_fill(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color) {
    uint16_t ax, ay, bx, by;
    if (!visible(x0, y0, &x1, &y1)) return;
    ds100_stream_before_capture();
    transform(x0, y0, &ax, &ay); transform(x1, y1, &bx, &by);
    ds100_draw_fill(&state->draw, ax < bx ? ax : bx, ay < by ? ay : by,
                    ax < bx ? bx : ax, ay < by ? by : ay, color);
}

void ds100_stream_span(uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    uint16_t y1 = y, ax, ay, bx, by;
    if (!pixels || !visible(x0, y, &x1, &y1)) return;
    ds100_stream_before_capture();
    transform(x0, y, &ax, &ay); transform(x1, y, &bx, &by);
    ds100_draw_pixels(&state->draw, ax < bx ? ax : bx, ay < by ? ay : by,
                      (uint16_t)(x1 - x0 + 1u), ax == bx && ay != by, ax > bx || ay > by, pixels);
}

void ds100_stream_bitmap(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, const uint16_t *pixels) {
    unsigned row, stride = (unsigned)x1 - x0 + 1u;
    uint16_t original_end = x1;
    if (!pixels || !visible(x0, y0, &x1, &y1)) return;
    for (row = y0; row <= y1 && state->draw.mode == DRAW_ACTIVE; ++row)
        ds100_stream_span(x0, original_end, (uint16_t)row, pixels + (row - y0) * stride);
}

static void read_row(void *context, uint16_t y, uint16_t *pixels) {
    uint16_t x;
    (void)context;
    for (x = 0; x < 480u; ++x)
        pixels[x] = ((uint16_t (*)(uint16_t, uint16_t))(uintptr_t)0x0804e0fbu)(x, y);
}

size_t ds100_stream_handle(const uint8_t *cdb, uint8_t *out, size_t capacity) {
    uint16_t action = get16(cdb + 8);
    const char *error = 0;
    size_t n = 0, i;
    if (capacity < 4096u) return 0;
    if (cdb[2] != 1u || cdb[3] || get16(cdb + 10) || get16(cdb + 12) || get16(cdb + 14) || action > 6u)
        error = "The drawing request is invalid.";
    else if (!action) stop();
    else if (action == 1u && start()) error = "The stock heap has no room for the drawing queue.";
    else if (!state && action != 6u) error = "The drawing stream is inactive.";
    if (!error && action == 6u) {
        const uint32_t values[3] = {(uint32_t)(uintptr_t)redraw_timer,
            valid_timer(redraw_timer) ? *redraw_timer : 0u, state ? state->saved_period : 0u};
        for (i = 0; i < 3u; ++i) put32(out + 20u + i * 4u, values[i]);
        n = 12u;
        if (state) state->last_touch = now();
    } else if (!error && state) {
        state->last_touch = now();
        if (action == 3u) {
            if (state->draw.mode != DRAW_ACTIVE) error = "A drawing baseline is required before freezing.";
            else { ds100_draw_present(&state->draw); state->draw.mode = DRAW_FROZEN; }
        } else if (action == 4u) {
            if (state->draw.mode != DRAW_FROZEN) error = "The drawing stream is not frozen.";
            else state->draw.mode = DRAW_ACTIVE;
        }
        if (!error && action == 5u) {
            const uint32_t values[8] = {state->draw.epoch, state->draw.mode, state->draw.frame_id,
                state->draw.used, state->draw.peak_used, state->draw.clipped, state->draw.fault, state->draw.capacity};
            for (i = 0; i < 8u; ++i) put32(out + 20u + i * 4u, values[i]);
            n = 32u;
        } else if (!error) {
            n = ds100_draw_packet(&state->draw, out + 20u, 4076u, read_row, 0);
            if (!n) error = "The drawing stream requires a new baseline.";
        }
    }
    if (error) {
        for (n = 0; error[n] && n < 4076u; ++n) out[20u + n] = (uint8_t)error[n];
    }
    out[0] = 'D'; out[1] = '1'; out[2] = 'R'; out[3] = 'M';
    out[4] = 1; out[5] = 9; put16(out + 6, error ? 3 : 1);
    put32(out + 8, get32(cdb + 4)); put32(out + 12, (uint32_t)n);
    put32(out + 16, ds100_crc32(out + 20, n));
    n += 20u;
    /* The host requests 4096 bytes. Short replies that are a multiple of the
     * 64-byte endpoint packet size need one ignored padding byte. A full 4096
     * reply already completes the exact host request.
     */
    if (!(n & 63u) && n < 4096u) out[n++] = 0;
    return n;
}

void ds100_stream_clear(uint16_t color) {
    ds100_stream_before_capture();
    ds100_draw_clear(ds100_stream_queue(), color);
    ds100_stream_original_clear(color);
}

void ds100_stream_before_capture(void) {
    extern int ds100_stream_service_poll(void);
    uint32_t start_ms, spins;
    if (!state || state->draw.mode != DRAW_ACTIVE || state->draw.capacity - state->draw.used >= 1000u) return;
    start_ms = now();
    /* Only a queued drawing-poll command can run here. It drains RAM without
     * reading the LCD or re-entering the stock GUI. Both time and iteration
     * bounds release this wait if the PC or the timer stops responding.
     */
    for (spins = 0; spins < 500000u && (uint32_t)(now() - start_ms) < 15u; ++spins) {
        if (ds100_stream_service_poll() && state->draw.capacity - state->draw.used >= 1000u) return;
    }
}
