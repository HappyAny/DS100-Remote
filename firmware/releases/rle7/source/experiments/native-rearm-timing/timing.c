/* SPDX-License-Identifier: GPL-3.0-only */
#include <stddef.h>
#include <stdint.h>
#include "../acquisition-segments/timing.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define DWT_CYCLES REG32(0xe0001004u)
#define NATIVE_TICKS REG32(0x20027e90u)
#define CURRENT_DESCRIPTOR REG32(0x2002cf30u)

enum {
    PHASE_SHIFT = 10,
    PHASE_MASK = 3u << PHASE_SHIFT,
    PROCESSING = 1u << 14,
    AT_REARM = 1u << 15,
    NATIVE_MODE = 1u << 31,
    RESPONSE_BYTES = 68,
};

/* The original markers-only diagnostic clears these three words at START.
 * It leaves them available until the next START, even after expiry. */
typedef struct {
    uint32_t profile_magic, drawing_active, flags, started_or_elapsed;
    uint32_t stopped, result, cursor;
    uint32_t gaps, gap_sum, gap_peak;
    uint32_t dispatches, dispatch_sum, dispatch_peak;
    uint32_t wait_sum, preparation_sum, mode_and_coverage;
} capture_state;

extern volatile capture_state ds100_capture_state;
extern void ds100_native_original_event(unsigned);
extern int ds100_native_original_command(void *, uint16_t, uint8_t *, size_t, size_t *);
#define state ds100_capture_state

_Static_assert(sizeof(capture_state) == 64, "Use the existing marker state.");
_Static_assert(offsetof(capture_state, flags) == CAPTURE_FLAGS_OFFSET, "Match the assembly guard.");
_Static_assert(offsetof(capture_state, mode_and_coverage) == 60, "Match the assembly guard.");

static uint32_t lock(void) {
    uint32_t mask;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(mask) :: "memory");
    return mask;
}

static void unlock(uint32_t mask) {
    __asm__ volatile ("msr primask, %0" :: "r"(mask) : "memory");
}

static int published_descriptor(uint32_t address) {
    return (address == 0x2002cf00u || address == 0x2002cf18u) &&
           address == CURRENT_DESCRIPTOR && *(const volatile uint8_t *)(uintptr_t)(address + 8u);
}

void ds100_native_mark(unsigned rearm, uint32_t descriptor) {
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    if ((flags & (CAPTURE_RUNNING | PHASE_MASK)) ==
                (CAPTURE_RUNNING | (CAPTURE_RESULT_READY << PHASE_SHIFT)) &&
            NATIVE_TICKS - state.started_or_elapsed < CAPTURE_WINDOW_TICKS) {
        if (!rearm && !(flags & PROCESSING) && published_descriptor(descriptor)) {
            const uint32_t cycles = DWT_CYCLES;
            state.wait_sum += cycles - state.result;
            state.cursor = cycles;
            state.flags = flags | PROCESSING;
        } else if (rearm && (flags & (PROCESSING | AT_REARM)) == PROCESSING) {
            state.preparation_sum += DWT_CYCLES - state.cursor;
            state.flags = flags | AT_REARM;
        }
    }
    unlock(mask);
}

void ds100_native_restart(void) {
    const uint32_t mask = lock();
    const uint32_t covered = state.flags & AT_REARM;
    const uint32_t before = state.dispatches;
    ds100_native_original_event(CAPTURE_DMA_RESTARTED);
    if (covered && state.dispatches != before) ++state.mode_and_coverage;
    state.flags &= ~(PROCESSING | AT_REARM);
    unlock(mask);
}

static void put_word(uint8_t *out, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) {
        *out++ = (uint8_t)value;
        value >>= 8;
    }
}

int ds100_native_command(void *context, uint16_t action, uint8_t *out,
                         size_t capacity, size_t *length) {
    /* Start the existing markers-only mode so drawing/USB counters stay off.
     * Action 1 adds the native probes. Action 3 is their control run. */
    const uint32_t mask = lock();
    const int result = ds100_native_original_command(context, action == 1u ? 3u : action,
                                                      out, capacity, length);
    if (!result && *length == RESPONSE_BYTES) {
        if (action == 1u) state.mode_and_coverage = NATIVE_MODE;
        put_word(out, UINT32_C(0x33544341)); /* ACT3 */
        const uint32_t summary = state.mode_and_coverage;
        put_word(out + 16, !!(summary & NATIVE_MODE));
        /* The legacy snapshot hides all counters when active or invalid. */
        if (out[4] == 0u || out[4] == 2u) put_word(out + 64, summary & ~NATIVE_MODE);
    }
    unlock(mask);
    return result;
}
