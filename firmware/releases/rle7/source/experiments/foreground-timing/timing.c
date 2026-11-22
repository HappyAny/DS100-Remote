/* SPDX-License-Identifier: GPL-3.0-only */
#include <stddef.h>
#include <stdint.h>
#include "timing.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define REG8(address) (*(const volatile uint8_t *)(uintptr_t)(address))
#define TRACE_CONTROL REG32(0xe000edfcu)
#define DEBUG_STATUS REG32(0xe000edf0u)
#define DWT_CONTROL REG32(0xe0001000u)
#define DWT_CYCLES REG32(0xe0001004u)
#define NATIVE_HZ REG32(0x200203ccu)
#define CMSIS_HZ REG32(0x200213d0u)
#define NATIVE_TICKS REG32(0x20027e90u)
#define CURRENT_DESCRIPTOR REG32(0x2002cf30u)
#define PROFILE_MAGIC UINT32_C(0x35505244)
#define WAVEFORM_VTABLE UINT32_C(0x08072840)
#define WAVEFORM_CALLBACK UINT32_C(0x08029d57)

enum {
    EXPIRED = 2u,
    INVALID_CLOCK = 4u,
    TRACE_OWNED = 8u,
    COUNTER_OWNED = 16u,
    DETAIL = 1u << FOREGROUND_DETAIL_BIT, /* Leave the legacy sections bit clear. */
    INVALID_SEQUENCE = 128u,
    CLOCK_SHIFT = 8,
    PHASE_SHIFT = 10,
    STAGE_SHIFT = 12,
    ADVANCE = 1u << 15,
    INSIDE_CONSUMER = 1u << 16,
    ADVANCES_SHIFT = 17,
    CLOCK_MASK = 3u << CLOCK_SHIFT,
    PHASE_MASK = 3u << PHASE_SHIFT,
    STAGE_MASK = 7u << STAGE_SHIFT,
    READY_PHASE = CAPTURE_RESULT_READY << PHASE_SHIFT,
    RESTARTED_PHASE = CAPTURE_DMA_RESTARTED << PHASE_SHIFT,
    AT_DISPATCHER = 1,
    AT_CONSUMER = 2,
    INCOMPLETE_CONSUMER = 7,
    TRACE_ENABLE = 1u << 24,
    NO_COUNTER = 1u << 25,
    WORDS = 20,
    STATUS_MASK = CAPTURE_RUNNING | EXPIRED | INVALID_CLOCK | INVALID_SEQUENCE,
};

typedef struct {
    uint32_t profile_magic, drawing_active, flags, started_or_elapsed;
    uint32_t stopped, result, cursor;
    uint32_t gap_sum, gap_peak, dispatch_sum;
    uint32_t stage_sum[5];
    uint16_t gaps, coverage;
} capture_state;

extern volatile capture_state ds100_capture_state;
extern void ds100_foreground_usb_tail(void);
extern void ds100_auto_usb(void);
extern void ds100_stream_tick(void);
extern void ds100_profile_pass(void);
extern void ds100_native_dispatcher(void);
extern int ds100_stream_holds_render(void);

#define state ds100_capture_state
_Static_assert(sizeof(capture_state) == 64, "Reuse the existing profile area.");
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

static void finish(uint32_t flags, uint32_t now, uint32_t cycles, uint32_t reason) {
    uint32_t phase = flags & PHASE_MASK;
    if (reason == INVALID_CLOCK || reason == INVALID_SEQUENCE) phase = 0;
    state.started_or_elapsed = now - state.started_or_elapsed;
    state.stopped = phase ? cycles - state.stopped : 0;
    state.result = phase == READY_PHASE ? cycles - state.result : 0;
    state.flags = (flags & (CLOCK_MASK | DETAIL | ADVANCE | (UINT32_MAX << ADVANCES_SHIFT))) |
                  phase | reason;
    restore_counter(flags);
}

/* A result IRQ can interrupt any foreground probe. Check and update the
 * timing state with IRQs masked, then return to the caller's previous mask. */
static int valid_until(uint32_t flags, uint32_t now, uint32_t cycles) {
    if (!(flags & CAPTURE_RUNNING) || state.profile_magic != PROFILE_MAGIC) return 0;
    if (!clock_valid(core_hz(flags)) || !(DWT_CONTROL & 1u) || !(TRACE_CONTROL & TRACE_ENABLE)) {
        finish(flags, now, cycles, INVALID_CLOCK);
        return 0;
    }
    if (now - state.started_or_elapsed >= CAPTURE_WINDOW_TICKS) {
        finish(flags, now, cycles, EXPIRED);
        return 0;
    }
    return 1;
}

static int add_cycles(volatile uint32_t *total, uint32_t duration) {
    const uint32_t previous = *total;
    if (duration > UINT32_MAX - previous) return 0;
    *total = previous + duration;
    return 1;
}

void ds100_foreground_event(unsigned event) {
    const uint32_t mask = lock();
    const uint32_t now = NATIVE_TICKS;
    const uint32_t cycles = DWT_CYCLES;
    const uint32_t flags = state.flags;
    const uint32_t phase = flags & PHASE_MASK;
    const uint32_t cleared = flags & ~(PHASE_MASK | STAGE_MASK | INSIDE_CONSUMER);
    if (!valid_until(flags, now, cycles) || !event ||
            now - state.started_or_elapsed < CAPTURE_WARMUP_TICKS) {
        unlock(mask);
        return;
    }
    if (event == CAPTURE_DMA_STOPPED && !phase) {
        state.stopped = cycles;
        state.flags = cleared | (CAPTURE_DMA_STOPPED << PHASE_SHIFT);
    } else if (event == CAPTURE_RESULT_READY && phase == (CAPTURE_DMA_STOPPED << PHASE_SHIFT)) {
        state.result = state.cursor = cycles;
        state.flags = cleared | READY_PHASE;
    } else if (event == CAPTURE_DMA_RESTARTED && phase == READY_PHASE) {
        const int covered = (flags & STAGE_MASK) == (FOREGROUND_REARM << STAGE_SHIFT);
        if ((!covered || state.coverage != UINT16_MAX) &&
                add_cycles(&state.dispatch_sum, cycles - state.result)) {
            if (covered) ++state.coverage;
            state.flags = cleared | RESTARTED_PHASE;
        } else {
            finish(flags, now, cycles, INVALID_SEQUENCE);
        }
    } else if (event == CAPTURE_TRIGGER_READY && phase == RESTARTED_PHASE) {
        const uint32_t gap = cycles - state.stopped;
        if (state.gaps != UINT16_MAX && add_cycles(&state.gap_sum, gap)) {
            ++state.gaps;
            if (state.gap_peak < gap) state.gap_peak = gap;
            state.flags = cleared;
        } else {
            finish(flags, now, cycles, INVALID_SEQUENCE);
        }
    } else if (phase) {
        finish(flags, now, cycles, INVALID_SEQUENCE);
    }
    unlock(mask);
}

static int result_open(uint32_t flags) {
    const uint32_t required = DETAIL | CAPTURE_RUNNING | READY_PHASE;
    return (flags & (DETAIL | CAPTURE_RUNNING | PHASE_MASK)) == required;
}

static int descriptor_ready(uint32_t address) {
    return (address == 0x2002cf00u || address == 0x2002cf18u) &&
           address == CURRENT_DESCRIPTOR && REG8(address + 8u);
}

/* Must hold the lock. A result may arrive after this pass entered the
 * dispatcher. In that case the whole initial wait belongs to stage 2. */
static int advance_stage(uint32_t flags, unsigned next, uint32_t now, uint32_t cycles) {
    if (!add_cycles(&state.stage_sum[next - 1u], cycles - state.cursor)) {
        finish(flags, now, cycles, INVALID_SEQUENCE);
        return 0;
    }
    state.cursor = cycles;
    state.flags = (flags & ~STAGE_MASK) | (next << STAGE_SHIFT);
    return 1;
}

void ds100_foreground_mark(unsigned next, uint32_t descriptor) {
    if (!result_open(state.flags)) return;
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    const unsigned stage = (flags & STAGE_MASK) >> STAGE_SHIFT;
    if (result_open(flags) && (next == AT_DISPATCHER ? stage == 0u :
            (flags & INSIDE_CONSUMER) && stage + 1u == next &&
            (next != FOREGROUND_PREPARE || descriptor_ready(descriptor)))) {
        const uint32_t now = NATIVE_TICKS;
        const uint32_t cycles = DWT_CYCLES;
        if (valid_until(flags, now, cycles)) advance_stage(flags, next, now, cycles);
    }
    unlock(mask);
}

unsigned ds100_foreground_consume(uint32_t context, unsigned early) {
    unsigned tracked = 0;
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    const unsigned stage = (flags & STAGE_MASK) >> STAGE_SHIFT;
    if (result_open(flags) && stage <= AT_DISPATCHER && !(flags & INSIDE_CONSUMER) &&
            (!early || (!mask && (flags & ADVANCE) && descriptor_ready(CURRENT_DESCRIPTOR)))) {
        const uint32_t now = NATIVE_TICKS;
        const uint32_t cycles = DWT_CYCLES;
        if (valid_until(flags, now, cycles)) {
            if (early && (flags >> ADVANCES_SHIFT) == (UINT32_MAX >> ADVANCES_SHIFT)) {
                finish(flags, now, cycles, INVALID_SEQUENCE);
            } else {
                const uint32_t tracking = (flags | INSIDE_CONSUMER) + (early << ADVANCES_SHIFT);
                tracked = advance_stage(tracking, AT_CONSUMER, now, cycles);
            }
        }
    }
    unlock(mask);
    if (early && !tracked) return 0;
    ((void (*)(uint32_t))(uintptr_t)WAVEFORM_CALLBACK)(context);
    if (tracked) {
        const uint32_t previous = lock();
        const uint32_t current = state.flags;
        /* Attribute a prefix only if this invocation returns before rearm.
         * Later invocations or timer work belong to the residual. */
        if (current & INSIDE_CONSUMER)
            state.flags = (current & ~(INSIDE_CONSUMER | STAGE_MASK)) |
                          (INCOMPLETE_CONSUMER << STAGE_SHIFT);
        unlock(previous);
    }
    return 1;
}

static int pending_mirror_service(void) {
    const unsigned status = REG8(0x20021515u);
    const uint32_t cdb = 0x2002d74cu;
    if (status & 0x80u) return REG8(cdb) == 0x28u; /* Queued READ(10). */
    if (status != 0x7fu || REG8(cdb) != 0xc0u || REG8(cdb + 1u) != 9u || REG8(cdb + 9u))
        return 0;
    const unsigned action = REG8(cdb + 8u);
    return action == 1u || action == 2u; /* DRW1 START or POLL. */
}

static uint32_t eligible_context(uint32_t gui) {
    const uint32_t flags = state.flags;
    if (!(flags & ADVANCE) || !result_open(flags) || REG8(gui + 0x48u) == 1u ||
            !pending_mirror_service() || ds100_stream_holds_render()) return 0;
    const uint32_t context = REG32(gui + 0x54u);
    if (!context || REG32(context) != WAVEFORM_VTABLE || REG8(context + 0xe8u) ||
            REG8(context + 0x229u) || REG8(context + 0x22au)) return 0;
    const uint32_t config = REG32(0x20020020u);
    const uint32_t panel = REG32(0x200200acu);
    if (!config || !panel || REG8(panel + 0x5du) == 4u ||
            REG8(config + 0x14u) > 1u || REG8(config + 0x13cu) > 1u) return 0;
    return context;
}

unsigned ds100_foreground_poll(uint32_t gui) {
    ds100_foreground_event(CAPTURE_TICK);
    ds100_auto_usb();
    ds100_stream_tick();
    ds100_profile_pass();
    ds100_foreground_mark(AT_DISPATCHER, 0);
    ds100_native_dispatcher();
    const uint32_t context = eligible_context(gui);
    const unsigned ran = context && ds100_foreground_consume(context, 1);
    ds100_foreground_usb_tail();
    return ran && REG32(gui + 0x54u) == context && REG32(context) == WAVEFORM_VTABLE;
}

static int begin(uint32_t now, uint16_t action) {
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
    state.gap_sum = state.gap_peak = state.dispatch_sum = 0;
    state.gaps = state.coverage = 0;
    for (unsigned i = 0; i < 5; ++i) state.stage_sum[i] = 0;
    state.started_or_elapsed = now;
    state.flags = flags | (action != 3u ? DETAIL : 0) | (action == 4u ? ADVANCE : 0);
    return 0;
}

static void put_word(uint8_t *out, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) {
        *out++ = (uint8_t)value;
        value >>= 8;
    }
}

int ds100_foreground_command(void *context, uint16_t action, uint8_t *out,
                             size_t capacity, size_t *length) {
    (void)context;
    if (!out || !length || capacity < WORDS * 4u || action > 4u ||
            state.profile_magic != PROFILE_MAGIC || state.drawing_active) return -1;
    const uint32_t mask = lock();
    const uint32_t now = NATIVE_TICKS;
    const uint32_t cycles = DWT_CYCLES;
    const uint32_t before = state.flags;
    const int running = valid_until(before, now, cycles);
    int error = 0;
    if (action == 1u || action >= 3u) {
        error = running || begin(now, action);
    } else if (action == 0u) {
        if (running) finish(before, now, cycles, 0);
    } else {
        error = running;
    }
    const uint32_t flags = state.flags;
    const uint32_t hz = core_hz(flags);
    unlock(mask);
    if (error || (!hz && action != 0u)) return -1;

    const unsigned snapshot = !(flags & (CAPTURE_RUNNING | INVALID_CLOCK | INVALID_SEQUENCE));
    const unsigned mode = (flags & ADVANCE) ? 2u : !!(flags & DETAIL);
    const unsigned phase = (flags & PHASE_MASK) >> PHASE_SHIFT;
    const uint32_t header[] = {UINT32_C(0x34544341), flags & STATUS_MASK, hz,
        (flags & CAPTURE_RUNNING) ? 0u : state.started_or_elapsed, mode,
        snapshot ? state.gaps : 0u, snapshot ? state.gap_sum : 0,
        snapshot ? state.gap_peak : 0,
        snapshot ? (unsigned)state.gaps + (phase == CAPTURE_DMA_RESTARTED) : 0u,
        snapshot ? state.dispatch_sum : 0, snapshot ? phase : 0,
        snapshot ? state.stopped : 0, snapshot ? state.result : 0,
        snapshot ? state.coverage : 0, snapshot ? flags >> ADVANCES_SHIFT : 0};
    for (unsigned i = 0; i < sizeof(header) / sizeof(*header); ++i) put_word(out + 4u * i, header[i]);
    for (unsigned i = 0; i < 5; ++i) put_word(out + 4u * (15u + i), snapshot ? state.stage_sum[i] : 0);
    *length = WORDS * 4u;
    return 0;
}
