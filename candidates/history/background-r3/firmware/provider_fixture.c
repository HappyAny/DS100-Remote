#include "ds100_remote.h"
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

static int fixture_screen(void *context, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t *out, uint32_t *frame) {
    unsigned row, col;
    (void)context;
    for (row = 0; row < h; ++row) for (col = 0; col < w; ++col) {
        uint16_t value = (uint16_t)((((x + col) & 31u) << 11) | (((y + row) & 63u) << 5) | ((x + y + col + row) & 31u));
        *out++ = (uint8_t)value; *out++ = (uint8_t)(value >> 8);
    }
    *frame = 7;
    return 0;
}

static int fixture_wave(void *context, uint16_t count, uint16_t channels, uint32_t offset, uint8_t *out, size_t capacity, size_t *length) {
    uint32_t n = count, mask = channels;
    double dt = 0.000001, t0 = offset * dt;
    float scale = 0.02f, zero = -2.56f;
    size_t pos = 40;
    unsigned channel, index;
    (void)context;
    if (capacity < 40u + count * ((channels == 3u) ? 2u : 1u)) return -1;
    memcpy(out, &n, 4); memcpy(out + 4, &mask, 4); memcpy(out + 8, &dt, 8); memcpy(out + 16, &t0, 8);
    memcpy(out + 24, &scale, 4); memcpy(out + 28, &zero, 4); memcpy(out + 32, &scale, 4); memcpy(out + 36, &zero, 4);
    for (channel = 0; channel < 2; ++channel) if (channels & (1u << channel))
        for (index = 0; index < count; ++index) out[pos++] = (uint8_t)(index + offset + channel);
    *length = pos;
    return 0;
}

static int fixture_run(void *context, uint8_t running) { (void)context; (void)running; return 0; }

EXPORT size_t ds100_fixture_exchange(const uint8_t cdb[16], uint8_t *out, size_t capacity) {
    static const ds100_backend backend = {
        "{\"protocol\":1,\"model\":\"C provider fixture\",\"source\":\"native-test\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\",\"waveform\",\"run\"]}",
        fixture_screen, fixture_wave, fixture_run, 0
    };
    return ds100_remote_handle(&backend, cdb, out, capacity);
}
