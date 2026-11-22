/* SPDX-License-Identifier: GPL-3.0-only */
#include <stddef.h>
#include <stdint.h>
#include "timing.h"

#define PROFILE_MAGIC UINT32_C(0x35505244)
#define TIMING_MAGIC UINT32_C(0x32544341) /* ACT2 */
#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define TRACE_CONTROL REG32(0xe000edfcu)
#define DEBUG_STATUS REG32(0xe000edf0u)
#define DWT_CONTROL REG32(0xe0001000u)
#define DWT_CYCLES REG32(0xe0001004u)
#define NATIVE_HZ REG32(0x200203ccu)
#define CMSIS_HZ REG32(0x200213d0u)
#define NATIVE_TICKS REG32(0x20027e90u)

enum {
    EXPIRED = 2u,
    INVALID_CLOCK = 4u,
    TRACE_OWNED = 8u,
    COUNTER_OWNED = 16u,
    SECTION_RESULT = 64u,
    INVALID_SEQUENCE = 128u,
    TRACE_ENABLE = 1u << 24,
    NO_COUNTER = 1u << 25,
    CLOCK_SHIFT = 8,
    PHASE_SHIFT = 10,
    SECTION_SHIFT = 12,
    CLOCK_MASK = 3u << CLOCK_SHIFT,
    PHASE_MASK = 3u << PHASE_SHIFT,
    SECTION_MASK = 3u << SECTION_SHIFT,
};

/* Reuse ACT1's initialized 64-byte area. Retain the original drawing
 * profile magic and inactive word for the stock-compatible fast path. */
typedef struct {
    uint32_t profile_magic, drawing_active, flags, started_or_elapsed;
    uint32_t stopped, result, cursor;
    uint32_t gaps, gap_sum, gap_peak;
    uint32_t dispatches, dispatch_sum, dispatch_peak;
    uint32_t section_sum[3];
} capture_state;

extern volatile capture_state ds100_capture_state;
#define state ds100_capture_state
_Static_assert(sizeof(capture_state) == 64, "Match the original profile area.");
_Static_assert(offsetof(capture_state, flags) == CAPTURE_FLAGS_OFFSET, "Match the assembly guard.");
_Static_assert(offsetof(capture_state, started_or_elapsed) == CAPTURE_START_OFFSET, "Match the hold timer.");
static const uint32_t clocks[] = {0, 168000000u, 200000000u, 250000000u};

static uint32_t lock(void) {
    uint32_t mask;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(mask) :: "memory");
    return mask;
}

static void unlock(uint32_t mask) {
    __asm__ volatile ("msr primask, %0" :: "r"(mask) : "memory");
}

static unsigned phase_of(uint32_t flags) {
    return (flags & PHASE_MASK) >> PHASE_SHIFT;
}

static uint32_t core_hz(uint32_t flags) {
    return clocks[(flags & CLOCK_MASK) >> CLOCK_SHIFT];
}

static int clock_valid(uint32_t hz) {
    return NATIVE_HZ == hz && CMSIS_HZ == hz && !(DEBUG_STATUS & 1u);
}

static void restore_counter(uint32_t flags) {
    if (DEBUG_STATUS & 1u) return;
    if (flags & COUNTER_OWNED) DWT_CONTROL &= ~1u;
    if (flags & TRACE_OWNED) TRACE_CONTROL &= ~TRACE_ENABLE;
}

static int charge(uint32_t flags, uint32_t cycles) {
    const unsigned section = (flags & SECTION_MASK) >> SECTION_SHIFT;
    if ((flags & CAPTURE_SECTIONS) && section) {
        volatile uint32_t *total = &state.section_sum[section - 1u];
        const uint32_t delta = cycles - state.cursor;
        const uint32_t previous = *total;
        if (delta > UINT32_MAX - previous) return -1;
        *total = previous + delta;
    }
    state.cursor = cycles;
    return 0;
}

static void finish(uint32_t flags, uint32_t now, uint32_t cycles, uint32_t reason) {
    unsigned phase = phase_of(flags);
    uint32_t partial_dispatch = 0;
    if (reason == INVALID_CLOCK || reason == INVALID_SEQUENCE) {
        phase = 0;
    } else if (phase == CAPTURE_RESULT_READY) {
        partial_dispatch = cycles - state.result;
        if (partial_dispatch > UINT32_MAX - state.dispatch_sum || charge(flags, cycles)) {
            reason = INVALID_SEQUENCE;
            partial_dispatch = phase = 0;
        }
    }
    state.started_or_elapsed = now - state.started_or_elapsed;
    state.stopped = phase ? cycles - state.stopped : 0;
    state.result = partial_dispatch;
    state.flags = (flags & CLOCK_MASK) | (phase << PHASE_SHIFT) | reason |
        ((flags & CAPTURE_SECTIONS) ? SECTION_RESULT : 0);
    restore_counter(flags);
}

/* Called with IRQs masked. A timer IRQ may publish the result in the middle
 * of a foreground section, so each phase/category transition is atomic. */
static int valid_until(uint32_t flags, uint32_t now, uint32_t cycles) {
    if (!(flags & CAPTURE_RUNNING) || state.profile_magic != PROFILE_MAGIC) return 0;
    if (!clock_valid(core_hz(flags))) {
        finish(flags, now, cycles, INVALID_CLOCK);
        return 0;
    }
    if (now - state.started_or_elapsed >= CAPTURE_WINDOW_TICKS) {
        finish(flags, now, cycles, EXPIRED);
        return 0;
    }
    return 1;
}

/* Return the enclosing section. Nested foreground USB work is charged to USB
 * while it runs, even when the caller is inside the full-queue wait. */
unsigned ds100_capture_switch(unsigned next) {
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    const unsigned previous = (flags & SECTION_MASK) >> SECTION_SHIFT;
    if ((flags & CAPTURE_SECTIONS) && previous != next) {
        if (phase_of(flags) == CAPTURE_RESULT_READY && charge(flags, DWT_CYCLES)) {
            finish(flags, NATIVE_TICKS, DWT_CYCLES, INVALID_SEQUENCE);
        } else {
            state.flags = (flags & ~SECTION_MASK) | (next << SECTION_SHIFT);
        }
    }
    unlock(mask);
    return previous;
}

static int record(volatile uint32_t *count, volatile uint32_t *sum,
                  volatile uint32_t *peak, uint32_t value) {
    if (value > UINT32_MAX - *sum) return -1;
    *sum += value;
    ++*count;
    if (*peak < value) *peak = value;
    return 0;
}

void ds100_capture_event(unsigned event) {
    const uint32_t mask = lock();
    const uint32_t cycles = DWT_CYCLES;
    const uint32_t now = NATIVE_TICKS;
    const uint32_t flags = state.flags;
    const unsigned phase = phase_of(flags);
    if (valid_until(flags, now, cycles) && event &&
            now - state.started_or_elapsed >= CAPTURE_WARMUP_TICKS) {
        if (event == CAPTURE_DMA_STOPPED && !phase) {
            state.stopped = cycles;
            state.flags = flags | (CAPTURE_DMA_STOPPED << PHASE_SHIFT);
        } else if (event == CAPTURE_RESULT_READY && phase == CAPTURE_DMA_STOPPED) {
            state.result = state.cursor = cycles;
            state.flags = (flags & ~PHASE_MASK) | (CAPTURE_RESULT_READY << PHASE_SHIFT);
        } else if (event == CAPTURE_DMA_RESTARTED && phase == CAPTURE_RESULT_READY) {
            if (charge(flags, cycles) || record(&state.dispatches, &state.dispatch_sum,
                                                &state.dispatch_peak, cycles - state.result)) {
                finish(flags, now, cycles, INVALID_SEQUENCE);
            } else {
                state.flags = (flags & ~PHASE_MASK) | (CAPTURE_DMA_RESTARTED << PHASE_SHIFT);
            }
        } else if (event == CAPTURE_TRIGGER_READY && phase == CAPTURE_DMA_RESTARTED) {
            if (record(&state.gaps, &state.gap_sum, &state.gap_peak, cycles - state.stopped)) {
                finish(flags, now, cycles, INVALID_SEQUENCE);
            } else {
                state.flags = flags & ~PHASE_MASK;
            }
        } else if (phase) {
            finish(flags, now, cycles, INVALID_SEQUENCE);
        }
        /* With no open sequence, ignore markers for an acquisition that
         * straddled the warmup boundary or was already in flight at START. */
    }
    unlock(mask);
}

static int begin(uint32_t now, int detailed) {
    const uint32_t hz = NATIVE_HZ;
    unsigned clock = 1;
    while (clock < sizeof(clocks) / sizeof(*clocks) && clocks[clock] != hz) ++clock;
    if (clock == sizeof(clocks) / sizeof(*clocks) || !clock_valid(hz)) return -1;

    uint32_t flags = CAPTURE_RUNNING | (clock << CLOCK_SHIFT);
    if (!(TRACE_CONTROL & TRACE_ENABLE)) {
        TRACE_CONTROL |= TRACE_ENABLE;
        flags |= TRACE_OWNED;
    }
    if (DWT_CONTROL & NO_COUNTER) {
        restore_counter(flags);
        return -1;
    }
    if (!(DWT_CONTROL & 1u)) {
        DWT_CONTROL |= 1u;
        flags |= COUNTER_OWNED;
    }
    const uint32_t before = DWT_CYCLES;
    __asm__ volatile (".rept 32\n nop\n .endr" ::: "memory");
    if (DWT_CYCLES == before) {
        restore_counter(flags);
        return -1;
    }
    state.stopped = state.result = state.cursor = 0;
    state.gaps = state.gap_sum = state.gap_peak = 0;
    state.dispatches = state.dispatch_sum = state.dispatch_peak = 0;
    for (unsigned i = 0; i < 3; ++i) state.section_sum[i] = 0;
    state.started_or_elapsed = now;
    state.flags = flags | (detailed ? CAPTURE_SECTIONS : 0);
    return 0;
}

int ds100_capture_command(void *context, uint16_t action, uint8_t *out,
                          size_t capacity, size_t *length) {
    (void)context;
    enum { WORDS = 17, STATUS_MASK = CAPTURE_RUNNING | EXPIRED | INVALID_CLOCK | INVALID_SEQUENCE };
    if (!out || !length || capacity < WORDS * 4u || action > 3u ||
            state.profile_magic != PROFILE_MAGIC || state.drawing_active) return -1;

    const uint32_t mask = lock();
    const uint32_t now = NATIVE_TICKS;
    const uint32_t cycles = DWT_CYCLES;
    const uint32_t before = state.flags;
    const int running = valid_until(before, now, cycles);
    int error = 0;
    if (action == 1u || action == 3u) {
        error = running || begin(now, action == 1u);
    } else if (action == 0u) {
        if (running) finish(before, now, cycles, 0);
    } else {
        error = running;
    }
    const uint32_t flags = state.flags;
    unlock(mask);
    if (error || (!core_hz(flags) && action != 0u)) return -1;

    /* Only an idle/expired valid capture has readable counts. The IRQs cannot
     * modify a frozen result. Active and invalid replies carry their status. */
    const int snapshot = !(flags & (CAPTURE_RUNNING | INVALID_CLOCK | INVALID_SEQUENCE));
    const uint32_t result[WORDS] = {TIMING_MAGIC, flags & STATUS_MASK, core_hz(flags),
        (flags & CAPTURE_RUNNING) ? 0 : state.started_or_elapsed,
        !!(flags & (CAPTURE_SECTIONS | SECTION_RESULT)),
        snapshot ? state.gaps : 0, snapshot ? state.gap_sum : 0,
        snapshot ? state.gap_peak : 0, snapshot ? state.dispatches : 0,
        snapshot ? state.dispatch_sum : 0, snapshot ? state.dispatch_peak : 0,
        snapshot ? phase_of(flags) : 0, snapshot ? state.stopped : 0,
        snapshot ? state.result : 0, snapshot ? state.section_sum[0] : 0,
        snapshot ? state.section_sum[1] : 0, snapshot ? state.section_sum[2] : 0};
    for (unsigned i = 0; i < WORDS; ++i) {
        uint32_t value = result[i];
        for (unsigned j = 0; j < 4; ++j) {
            *out++ = (uint8_t)value;
            value >>= 8;
        }
    }
    *length = WORDS * 4u;
    return 0;
}
