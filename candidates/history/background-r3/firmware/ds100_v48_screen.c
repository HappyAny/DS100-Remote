/* Experimental V4.8 binding. v48_hooks.c calls it in the display loop. */
#include "ds100_remote.h"

#if defined(__arm__) || defined(__thumb__)
typedef uint16_t (*lcd_read_point)(uint16_t x, uint16_t y);

static void append_text(uint8_t **cursor, const char *text) {
    while (*text) *(*cursor)++ = (uint8_t)*text++;
}

static void append_hex(uint8_t **cursor, uint32_t value, unsigned digits) {
    static const char hex[] = "0123456789abcdef";
    while (digits) { --digits; *(*cursor)++ = (uint8_t)hex[(value >> (digits * 4u)) & 15u]; }
}

static uint32_t rom_word(uintptr_t address) {
    const volatile uint8_t *p = (const volatile uint8_t *)address;
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Read only these fixed code and LCD-descriptor locations. No arbitrary-memory
 * command is exposed. The caller supplies the existing MSC payload buffer.
 */
size_t ds100_v48_make_info(uint8_t *out, size_t capacity) {
    uint8_t *cursor = out;
    if (!out || capacity < 512u) return 0;
    append_text(&cursor, "{\"protocol\":1,\"model\":\"DS100 V4.8 screen candidate\",\"software\":\"4.8\",\"source\":\"experimental-v48\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\"],\"build\":\"");
#if defined(DS100_BACKGROUND_USB)
    append_text(&cursor, "background-r3");
#else
    append_text(&cursor, "screen-base-diag");
#endif
    append_text(&cursor, "\",\"diag\":{\"common\":\"");
    append_hex(&cursor, rom_word(0x080148d0u), 8u);
    append_text(&cursor, "\",\"dialog\":\"");
    append_hex(&cursor, rom_word(0x080306d2u), 8u);
    append_text(&cursor, "\",\"usb_stop\":\"");
    append_hex(&cursor, rom_word(0x080452eau) & 0xffffu, 4u);
    append_text(&cursor, "\",\"lcd_id\":\"");
    append_hex(&cursor, *(const volatile uint16_t *)(uintptr_t)0x2002cef4u, 4u);
    append_text(&cursor, "\",\"core_hz_hex\":\"");
    append_hex(&cursor, *(const volatile uint32_t *)(uintptr_t)0x200203ccu, 8u);
    append_text(&cursor, "\",\"clock_hook\":\"");
    append_hex(&cursor, rom_word(0x08035ad4u), 8u);
    append_text(&cursor, "\"}}");
    *cursor = 0;
    return (size_t)(cursor - out);
}

/* The stock BMP writer calls this wrapper at 0x08015168 and 0x08014f7e.
 * The wrapper branches to the bounded RGB565 reader at 0x0803c8a6.
 * Call this provider from the display task. It is not interrupt-reentrant.
 */
static int read_screen(void *context, uint16_t x, uint16_t y, uint16_t width,
                       uint16_t height, uint8_t *pixels, uint32_t *frame_id) {
    lcd_read_point read_point = (lcd_read_point)(uintptr_t)0x0804e0fbu;
    uint16_t row, column;
    (void)context;
    for (row = 0; row < height; ++row) for (column = 0; column < width; ++column) {
        uint16_t value = read_point((uint16_t)(x + column), (uint16_t)(y + row));
        *pixels++ = (uint8_t)value;
        *pixels++ = (uint8_t)(value >> 8);
    }
    /* The LCD scan is not an atomic frame snapshot. No extra RAM is reserved. */
    *frame_id = 0;
    return 0;
}

const ds100_backend *ds100_v48_screen_backend(uint32_t *scan_id) {
    static const ds100_backend backend = {
        "{\"protocol\":1,\"model\":\"DS100 V4.8 screen candidate\",\"software\":\"4.8\",\"source\":\"experimental-v48\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\"]}",
        read_screen, 0, 0, 0
    };
    (void)scan_id;
    return &backend;
}
#else
/* Do not call device addresses in a host process. */
const ds100_backend *ds100_v48_screen_backend(uint32_t *scan_id) {
    (void)scan_id;
    return 0;
}
#endif
