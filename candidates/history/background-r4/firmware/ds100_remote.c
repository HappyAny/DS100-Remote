#include "ds100_remote.h"

static uint16_t read16(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1] << 8)); }
static uint32_t read32(const uint8_t *p) { return (uint32_t)read16(p) | ((uint32_t)read16(p + 2) << 16); }
static void write16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void write32(uint8_t *p, uint32_t v) { write16(p, (uint16_t)v); write16(p + 2, (uint16_t)(v >> 16)); }

uint32_t ds100_crc32(const uint8_t *data, size_t length) {
    uint32_t crc = UINT32_C(0xffffffff);
    size_t i;
    for (i = 0; i < length; ++i) {
        unsigned bit;
        crc ^= data[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1u) ? UINT32_C(0xedb88320) : 0u);
    }
    return ~crc;
}

static size_t error_text(uint8_t *payload, size_t capacity, const char *message) {
    size_t n = 0;
    while (message[n] && n < capacity) { payload[n] = (uint8_t)message[n]; ++n; }
    return n;
}

size_t ds100_remote_handle(const ds100_backend *backend, const uint8_t cdb[16], uint8_t *out, size_t capacity) {
    uint8_t *payload;
    uint16_t a, b, c, d;
    uint8_t flags = 1u;
    size_t length = 0;
    const char *error = 0;
    if (!backend || !cdb || !out || capacity < 128u || capacity > DS100_MAX_PACKET) return 0;
    if (cdb[0] != 0xc0u) return 0;
    payload = out + 20;
    a = read16(cdb + 8); b = read16(cdb + 10); c = read16(cdb + 12); d = read16(cdb + 14);
    if (cdb[2] != 1u || cdb[3] != 0u) error = "The command header is invalid.";
    else switch (cdb[1]) {
    case 1:
        if (a || b || c || d) { error = "The information request has invalid arguments."; break; }
        if (!backend->info_json) { error = "Device information is not available."; break; }
        while (backend->info_json[length]) {
            if (length >= capacity - 20u) { error = "Device information exceeds the output buffer."; break; }
            payload[length] = (uint8_t)backend->info_json[length]; ++length;
        }
        break;
    case 2: {
        uint32_t count = (uint32_t)c * d, frame = 0;
        if (!c || !d || count > DS100_MAX_TILE_PIXELS || (uint32_t)a + c > 480u || (uint32_t)b + d > 320u) {
            error = "The screen rectangle is invalid."; break;
        }
        length = 16u + count * 2u;
        if (length > capacity - 20u) { error = "The screen tile exceeds the output buffer."; break; }
        if (!backend->screen || backend->screen(backend->context, a, b, c, d, payload + 16, &frame)) {
            error = "The screen provider is not available."; break;
        }
        write32(payload, frame); write16(payload + 4, a); write16(payload + 6, b);
        write16(payload + 8, c); write16(payload + 10, d);
        payload[12] = payload[13] = payload[14] = payload[15] = 0;
        break;
    }
    case 5: {
        uint16_t rows = 0;
        uint32_t frame = 0;
        size_t encoded_length = 0;
        if (!c || !d || d > 16u || (uint32_t)a + c > 480u || (uint32_t)b + d > 320u) {
            error = "The fast screen rectangle is invalid."; break;
        }
        if (!backend->screen_stream || backend->screen_stream(backend->context, a, b, c, d,
                payload + 16u, capacity - 36u, &rows, &encoded_length, &frame)) {
            error = "The fast screen provider is not available."; break;
        }
        if (!rows || rows > d || !encoded_length || encoded_length > capacity - 36u || encoded_length % 4u) {
            error = "The fast screen provider returned an invalid result."; break;
        }
        write32(payload, frame); write16(payload + 4, a); write16(payload + 6, b);
        write16(payload + 8, c); write16(payload + 10, rows);
        payload[12] = 1u; payload[13] = payload[14] = payload[15] = 0;
        length = 16u + encoded_length;
        break;
    }
    case 3:
        if (!a || a > DS100_MAX_SAMPLES || b < 1u || b > 3u) { error = "The waveform request is invalid."; break; }
        if (!backend->wave || backend->wave(backend->context, a, b, ((uint32_t)d << 16) | c,
                                            payload, capacity - 20u, &length)) {
            error = "The waveform provider is not available."; break;
        }
        if (length > capacity - 20u) { error = "The waveform provider returned an invalid length."; break; }
        break;
    case 4:
        if (a > 1u || b || c || d) { error = "The run state is invalid."; break; }
        if (!backend->set_run || backend->set_run(backend->context, (uint8_t)a)) { error = "Run control is not available."; break; }
        payload[0] = (uint8_t)a; length = 1;
        break;
    default: error = "The command is not supported."; break;
    }
    if (error) { flags |= 2u; length = error_text(payload, capacity - 20u, error); }
    out[0] = 'D'; out[1] = '1'; out[2] = 'R'; out[3] = 'M';
    out[4] = 1u; out[5] = cdb[1]; write16(out + 6, flags);
    write32(out + 8, read32(cdb + 4)); write32(out + 12, (uint32_t)length);
    write32(out + 16, ds100_crc32(payload, length));
    return length + 20u;
}
