/* SPDX-License-Identifier: GPL-3.0-only */
#include <stddef.h>
#include <stdint.h>

#define PROFILE_MAGIC UINT32_C(0x35505244)
#define TIMING_MAGIC UINT32_C(0x31544341) /* ACT1 */
#define WARMUP_MS 1500u
#define WINDOW_MS 6500u

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define TRACE_CONTROL REG32(0xe000edfcu)
#define DEBUG_STATUS REG32(0xe000edf0u)
#define DWT_CONTROL REG32(0xe0001000u)
#define DWT_CYCLES REG32(0xe0001004u)
#define NATIVE_HZ REG32(0x200203ccu)
#define CMSIS_HZ REG32(0x200213d0u)

enum {
    RUNNING = 1u,
    EXPIRED = 2u,
    INVALID_CLOCK = 4u,
    TRACE_OWNED = 8u,
    COUNTER_OWNED = 16u,
    TRACE_ENABLE = 1u << 24,
    NO_COUNTER = 1u << 25,
};
enum { TICK, STOPPED, RESULT, RESTART, READY };

/* Overlay the initialized R23 draw profile in this diagnostic build. Its
 * first two words retain the R23 layout. The drawing active word stays zero. */
typedef struct {
    uint32_t profile_magic, drawing_active;
    uint32_t flags, core_hz, started_or_elapsed_ms;
    uint32_t stopped, result, restart, phase;
    uint32_t windows, sum, peak, result_peak, dispatch_peak, pretrigger_peak;
    uint32_t dma_stops;
} capture_state;

extern volatile capture_state ds100_capture_state;
#define state ds100_capture_state
_Static_assert(sizeof(capture_state) == 64, "Match the original profile area.");
_Static_assert(offsetof(capture_state, flags) == 8, "Match the assembly guard.");

static uint32_t tick_ms(void) {
    return ((uint32_t (*)(void))(uintptr_t)0x08015385u)();
}

static void restore_counter(uint32_t flags) {
    if (DEBUG_STATUS & 1u) return;
    if (flags & COUNTER_OWNED) DWT_CONTROL &= ~1u;
    if (flags & TRACE_OWNED) TRACE_CONTROL &= ~TRACE_ENABLE;
}

static void finish(uint32_t now, uint32_t reason) {
    uint32_t irq_state, flags;
    /* A timer ISR can also expire a measurement. Publish the final state
     * once so a simultaneous STOP cannot discard the reason for stopping. */
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(irq_state) :: "memory");
    flags = state.flags;
    if (flags & RUNNING) {
        state.started_or_elapsed_ms = now - state.started_or_elapsed_ms;
        if (state.phase && reason != INVALID_CLOCK)
            state.stopped = DWT_CYCLES - state.stopped;
        state.flags = reason;
    }
    __asm__ volatile ("msr primask, %0" :: "r"(irq_state) : "memory");
    if (flags & RUNNING) restore_counter(flags);
}

static int clock_valid(uint32_t expected) {
    return NATIVE_HZ == expected && CMSIS_HZ == expected && !(DEBUG_STATUS & 1u);
}

static int active(uint32_t now) {
    if (!(state.flags & RUNNING) || state.profile_magic != PROFILE_MAGIC) return 0;
    if (!clock_valid(state.core_hz)) {
        finish(now, INVALID_CLOCK);
        return 0;
    }
    if (now - state.started_or_elapsed_ms >= WINDOW_MS) {
        finish(now, EXPIRED);
        return 0;
    }
    return 1;
}

static void peak(volatile uint32_t *max, uint32_t value) {
    if (*max < value) *max = value;
}

void ds100_capture_event(unsigned event) {
    const uint32_t cycles = DWT_CYCLES;
    const uint32_t now = tick_ms();
    if (!active(now) || event == TICK ||
            now - state.started_or_elapsed_ms < WARMUP_MS) return;
    if (event == STOPPED) {
        state.stopped = cycles;
        ++state.dma_stops;
        state.phase = STOPPED;
    } else if (event == RESULT && state.phase == STOPPED) {
        state.result = cycles;
        state.phase = RESULT;
    } else if (event == RESTART && state.phase == RESULT) {
        state.restart = cycles;
        state.phase = RESTART;
    } else if (event == READY && state.phase == RESTART) {
        const uint32_t gap = cycles - state.stopped;
        if (gap < (state.core_hz / 1000u) * (WINDOW_MS - WARMUP_MS)) {
            ++state.windows;
            state.sum += gap;
            peak(&state.peak, gap);
            peak(&state.result_peak, state.result - state.stopped);
            peak(&state.dispatch_peak, state.restart - state.result);
            peak(&state.pretrigger_peak, cycles - state.restart);
        }
        state.phase = 0;
    } else {
        state.phase = 0;
    }
}

static int begin(uint32_t now) {
    const uint32_t hz = NATIVE_HZ;
    uint32_t flags = RUNNING;
    if ((hz != 168000000u && hz != 200000000u && hz != 250000000u) ||
            !clock_valid(hz)) return -1;
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
    state.phase = state.dma_stops = 0;
    state.windows = state.sum = state.peak = 0;
    state.result_peak = state.dispatch_peak = state.pretrigger_peak = 0;
    state.core_hz = hz;
    state.started_or_elapsed_ms = now;
    state.flags = flags;
    return 0;
}

int ds100_capture_command(void *context, uint16_t action, uint8_t *out,
                          size_t capacity, size_t *length) {
    (void)context;
    enum { WORDS = 13 };
    if (!out || !length || capacity < WORDS * 4u || action > 2u ||
            state.profile_magic != PROFILE_MAGIC || state.drawing_active) return -1;

    const uint32_t now = tick_ms();
    const int running = active(now);
    if (action == 1u) {
        if (running || begin(now)) return -1;
    } else if (action == 0u) {
        if (running) finish(now, 0);
    } else if (running) {
        return -1;
    }

    const uint32_t flags = state.flags & (RUNNING | EXPIRED | INVALID_CLOCK);
    const uint32_t pending = flags & RUNNING ? 0 : state.phase != 0;
    const uint32_t result[WORDS] = {TIMING_MAGIC, flags, state.core_hz,
        flags & RUNNING ? 0 : state.started_or_elapsed_ms,
        state.dma_stops, state.windows, state.sum, state.peak,
        state.result_peak, state.dispatch_peak, state.pretrigger_peak, pending,
        pending && !(flags & INVALID_CLOCK) ? state.stopped : 0};
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
