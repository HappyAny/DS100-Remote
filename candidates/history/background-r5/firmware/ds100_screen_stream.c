#include "ds100_remote.h"

static void write16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
}

/* Keep a checkpoint at each row boundary. If a row exceeds the output buffer,
 * remove that entire row, including an extension of the preceding RLE run.
 * The caller requests the remaining rows in its next transaction.
 */
int ds100_screen_rle(const ds100_screen_reader *reader, uint16_t x, uint16_t y,
                     uint16_t width, uint16_t height, uint8_t *out, size_t capacity,
                     uint16_t *rows, size_t *length, uint32_t *frame_id) {
    size_t used = 0;
    uint16_t count = 0, value = 0, row, column;
    if (!reader || !reader->begin_row || !reader->next_pixel || !out || !rows ||
        !length || !frame_id || !width || !height || width > 480u || height > 16u ||
        (uint32_t)x + width > 480u || (uint32_t)y + height > 320u ||
        capacity < (size_t)width * 4u) return -1;
    *rows = 0; *length = 0; *frame_id = 0;
    for (row = 0; row < height; ++row) {
        size_t checkpoint = used;
        uint16_t checkpoint_count = count;
        reader->begin_row(reader->context, x, (uint16_t)(y + row));
        for (column = 0; column < width; ++column) {
            uint16_t pixel = reader->next_pixel(reader->context);
            if (used && pixel == value && count < 65535u) {
                ++count;
                write16(out + used - 4u, count);
            } else {
                if (used + 4u > capacity) {
                    used = checkpoint;
                    if (used) write16(out + used - 4u, checkpoint_count);
                    *length = used;
                    return *rows ? 0 : -1;
                }
                count = 1; value = pixel;
                write16(out + used, count); write16(out + used + 2u, value);
                used += 4u;
            }
        }
        *rows = (uint16_t)(row + 1u);
    }
    *length = used;
    return 0;
}
