/* Experimental V4.8 binding. v48_hooks.c calls it in the display loop. */
#include "../../firmware/ds100_remote.h"

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
    if (!out || capacity < 1024u) return 0;
#if defined(DS100_CONTROLS)
    append_text(&cursor, "{\"protocol\":1,\"model\":\"DS100 V4.8 controls candidate\",\"software\":\"4.8\",\"source\":\"experimental-v48\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\",\"input\"],\"build\":\"");
#else
    append_text(&cursor, "{\"protocol\":1,\"model\":\"DS100 V4.8 screen candidate\",\"software\":\"4.8\",\"source\":\"experimental-v48\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\"],\"build\":\"");
#endif
#if defined(DS100_BACKGROUND_USB)
#if defined(DS100_CONTROLS)
    append_text(&cursor, "background-r10-input-ready");
#elif defined(DS100_DRAW_PROFILE)
    append_text(&cursor, "background-r5-profile");
#elif defined(DS100_FAST_SCREEN)
    append_text(&cursor, "background-r4-fast");
#else
    append_text(&cursor, "background-r3");
#endif
#else
    append_text(&cursor, "screen-base-diag");
#endif
    append_text(&cursor, "\"");
#if defined(DS100_CONTROLS)
    append_text(&cursor, ",\"input\":{\"command\":8,\"keys\":[\"P\",\"A\",\"M\",\"E\"],\"wheels\":2,\"lease_ms\":600,\"auto_usb\":true}");
#endif
#if defined(DS100_FAST_SCREEN)
    if (*(const volatile uint16_t *)(uintptr_t)0x2002cef4u == 0x9488u)
        append_text(&cursor, ",\"screen_stream\":{\"command\":5,\"max_rows\":16,\"encoding\":\"rle565\"}");
#endif
    
#if defined(DS100_DRAW_PROFILE)
    append_text(&cursor, ",\"draw_profile\":{\"command\":6,\"layout\":\"draw-profile-v1\"},\"link_probe\":{\"command\":7,\"max_bytes\":4076}");
#endif
    append_text(&cursor, ",\"draw_stream\":{\"command\":9,\"packet_bytes\":4096,\"format\":\"DRW1\",\"queue_bytes\":16384}");
    append_text(&cursor, ",\"diag\":{\"common\":\"");
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
    append_text(&cursor, "\",\"fs_unmount_hook\":\"");
    append_hex(&cursor, rom_word(0x08045244u), 8u);
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

#if defined(DS100_FAST_SCREEN)
typedef struct { uint16_t saved; uint8_t second; } lcd_stream;

static uint16_t lcd_word(void) {
    /* Keep the stock FMC configuration. At 168 MHz these 16 NOPs add at least
     * 95 ns between read strobes. The LCD data sheet requires 90 ns high time.
     * Read-low timing remains controlled by the stock FMC configuration.
     */
    __asm__ volatile (".rept 16\n nop\n .endr" ::: "memory");
    return *(const volatile uint16_t *)(uintptr_t)0x60020000u;
}

static void stream_begin(void *context, uint16_t x, uint16_t y) {
    typedef void (*set_cursor)(uint16_t, uint16_t);
    lcd_stream *stream = context;
    ((set_cursor)(uintptr_t)0x0803c58fu)(x, y);
    *(volatile uint16_t *)(uintptr_t)0x6001fffeu = 0x2eu;
    (void)lcd_word();
    stream->second = 0;
}

static uint16_t stream_next(void *context) {
    lcd_stream *stream = context;
    uint16_t first, second;
    if (!stream->second) {
        /* RGB666 over the 16-bit bus: RG, BR, GB for two pixels. */
        first = lcd_word(); second = lcd_word();
        stream->saved = second; stream->second = 1;
        return (uint16_t)((first & 0xf800u) | ((first & 0x00fcu) << 3) | (second >> 11));
    }
    first = lcd_word(); second = stream->saved; stream->second = 0;
    return (uint16_t)(((second & 0x00f8u) << 8) | ((first & 0xfc00u) >> 5) | ((first & 0x00f8u) >> 3));
}

static int stream_screen(void *context, uint16_t x, uint16_t y, uint16_t width,
                         uint16_t height, uint8_t *out, size_t capacity,
                         uint16_t *rows, size_t *length, uint32_t *frame_id) {
    lcd_stream state;
    const ds100_screen_reader reader = {&state, stream_begin, stream_next};
    (void)context;
    if (*(const volatile uint16_t *)(uintptr_t)0x2002cef4u != 0x9488u) return -1;
    return ds100_screen_rle(&reader, x, y, width, height, out, capacity, rows, length, frame_id);
}
#endif

const ds100_backend *ds100_v48_screen_backend(uint32_t *scan_id) {
#if defined(DS100_DRAW_PROFILE)
    extern int ds100_v48_draw_profile(void *, uint16_t, uint8_t *, size_t, size_t *);
#endif
#if defined(DS100_CONTROLS)
    extern int ds100_v48_input(void *, uint16_t, int16_t, int16_t, uint8_t *, size_t, size_t *);
#endif
    static const ds100_backend backend = {
        "{\"protocol\":1,\"model\":\"DS100 V4.8 screen candidate\",\"software\":\"4.8\",\"source\":\"experimental-v48\",\"screen_width\":480,\"screen_height\":320,\"capabilities\":[\"screen\"]}",
        read_screen, 0, 0, 0,
#if defined(DS100_FAST_SCREEN)
        stream_screen
#else
        0
#endif
        ,
#if defined(DS100_DRAW_PROFILE)
        ds100_v48_draw_profile
#else
        0
#endif
        ,
#if defined(DS100_CONTROLS)
        ds100_v48_input
#else
        0
#endif
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
