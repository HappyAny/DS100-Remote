/* SPDX-License-Identifier: GPL-3.0-only */
#include "../draw-stream/draw_queue.h"

#define LCD_WIDTH 480u
#define LCD_HEIGHT 320u
#define ORIGINAL_READ ((uint16_t (*)(uint16_t, uint16_t))(uintptr_t)0x0804e0fbu)
#define ORIGINAL_CURSOR ((void (*)(uint16_t, uint16_t))(uintptr_t)0x0803c58fu)
#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define EXMC_CONTROL REG32(0xa0000000u)
#define EXMC_READ_TIMING REG32(0xa0000004u)
#define LCD_COMMAND (*(volatile uint16_t *)(uintptr_t)0x6001fffeu)
#define LCD_DATA (*(const volatile uint16_t *)(uintptr_t)0x60020000u)
#define BURST_REJECTED (UINT32_C(1) << 31)
#define LANDSCAPE_A0 0xa0u

/* The pinned R23 object embeds this queue at offset zero. The remaining
 * orientation bits are unused by the native coordinate transform. A new
 * START copies the actual direction over this field. */
typedef struct {
    ds100_draw_queue queue;
    uint32_t last_touch, baseline_direction;
} native_prefix;

_Static_assert(offsetof(native_prefix, baseline_direction) == 68u, "Match the R23 layout.");
extern ds100_draw_queue *ds100_original_queue(void);
extern const volatile uint32_t ds100_original_direction;

static void read_points(uint16_t y, uint16_t *pixels) {
    for (unsigned x = 0; x < LCD_WIDTH; ++x)
        pixels[x] = ORIGINAL_READ((uint16_t)x, y);
}

static inline void barrier(void) {
    __asm__ volatile ("dsb" ::: "memory");
}

static inline uint16_t read_word(void) {
    /* At 250 MHz these instructions add at least 100 ns between reads.
     * EXMC sets the pulse's low portion independently. */
    __asm__ volatile (".rept 25\n nop\n .endr" ::: "memory");
    const uint16_t word = LCD_DATA;
    barrier();
    return word;
}

static int geometry_supports_burst(uint16_t y) {
    const volatile uint16_t *lcd = (const volatile uint16_t *)(uintptr_t)0x2002cef0u;
    return y < LCD_HEIGHT && lcd[0] == LCD_WIDTH && lcd[1] == LCD_HEIGHT &&
           lcd[2] == 0x9488u && ds100_original_direction == LANDSCAPE_A0;
}

void ds100_read_baseline_row(void *context, uint16_t y, uint16_t *pixels) {
    (void)context;
    ds100_draw_queue *queue = ds100_original_queue();
    if (!queue || queue->mode != DRAW_BASELINE || !geometry_supports_burst(y)) {
        read_points(y, pixels);
        return;
    }
    volatile uint32_t *const orientation = (volatile uint32_t *)
        ((uintptr_t)queue + offsetof(native_prefix, baseline_direction));
    const uint32_t hz = REG32(0x200213d0u);
    const uint32_t control = EXMC_CONTROL;
    const uint32_t timing = EXMC_READ_TIMING;
    /* This is the reviewed, enabled asynchronous SRAM bank with 16 data
     * bits and separate read/write timing. Accept only mode A reads. */
    if (*orientation != LANDSCAPE_A0 ||
            (hz != 168000000u && hz != 200000000u && hz != 250000000u) ||
            hz != REG32(0x200203ccu) ||
            (control & 0xf13fu) != 0x5011u || (timing & 0x30000000u)) {
        read_points(y, pixels);
        return;
    }

    const uint16_t first_pixel = ORIGINAL_READ(0, y);
    ORIGINAL_CURSOR(0, y);
    barrier();
    const uint32_t applied = (timing & 0xff00u) >= 0x6400u ? timing :
        (timing & ~0xff00u) | 0x6400u;
    if (applied != timing) {
        EXMC_READ_TIMING = applied;
        barrier();
    }
    LCD_COMMAND = 0x2eu;
    barrier();
    __asm__ volatile (".rept 128\n nop\n .endr" ::: "memory");
    (void)read_word(); /* Dummy word at the start of RAMRD. */

    for (unsigned x = 0; x < LCD_WIDTH; x += 2) {
        const uint16_t rg = read_word();
        const uint16_t br = read_word();
        const uint16_t gb = read_word();
        pixels[x] = (uint16_t)((rg & 0xf800u) | ((rg & 0x00fcu) << 3) | (br >> 11));
        pixels[x + 1] = (uint16_t)(((br & 0x00f8u) << 8) | ((gb & 0xfc00u) >> 5) |
                                   ((gb & 0x00f8u) >> 3));
    }
    const int timing_matches = EXMC_READ_TIMING == applied;
    if (timing_matches && applied != timing) {
        EXMC_READ_TIMING = timing;
        barrier();
    }
    /* End at exactly the point read used by the original row callback. The
     * two endpoint reads catch faulty starts and wrap. A rejected row falls
     * back in full, and the rest of this baseline uses stock reads. */
    const uint16_t last_pixel = ORIGINAL_READ(LCD_WIDTH - 1u, y);
    if (!timing_matches || first_pixel != pixels[0] || last_pixel != pixels[LCD_WIDTH - 1u]) {
        *orientation |= BURST_REJECTED;
        read_points(y, pixels);
    }
}
