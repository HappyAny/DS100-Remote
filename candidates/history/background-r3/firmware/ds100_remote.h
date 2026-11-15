#ifndef DS100_REMOTE_H
#define DS100_REMOTE_H
#include <stddef.h>
#include <stdint.h>

#define DS100_MAX_PACKET 16404u
#define DS100_MAX_TILE_PIXELS 512u
#define DS100_MAX_SAMPLES 4096u

typedef struct {
    const char *info_json;
    /* Return zero on success. Store native little-endian RGB565 pixels. */
    int (*screen)(void *context, uint16_t x, uint16_t y, uint16_t width, uint16_t height,
                  uint8_t *pixels, uint32_t *frame_id);
    /* Fill a 40-byte waveform header, then channel-major unsigned 8-bit samples. */
    int (*wave)(void *context, uint16_t count, uint16_t channels, uint32_t offset,
                uint8_t *payload, size_t capacity, size_t *length);
    int (*set_run)(void *context, uint8_t running);
    void *context;
} ds100_backend;

/* This function only handles vendor opcode 0xC0. It does not modify flash. */
size_t ds100_remote_handle(const ds100_backend *backend, const uint8_t cdb[16],
                           uint8_t *response, size_t capacity);
uint32_t ds100_crc32(const uint8_t *data, size_t length);
#endif
