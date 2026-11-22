/* SPDX-License-Identifier: GPL-3.0-only */
#include <stddef.h>
#include <stdint.h>
#include "peak.h"
#include "../draw-stream/draw_queue.h"
#include "../waveform-writer/writer.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define TRACE_CONTROL REG32(0xe000edfcu)
#define DEBUG_STATUS REG32(0xe000edf0u)
#define DWT_CONTROL REG32(0xe0001000u)
#define DWT_CYCLES REG32(0xe0001004u)
#define NATIVE_HZ REG32(0x200203ccu)
#define CMSIS_HZ REG32(0x200213d0u)
#define NATIVE_TICKS REG32(0x20027e90u)
#define PROFILE_MAGIC UINT32_C(0x35505244)
#define UNKNOWN UINT32_MAX

enum {
    EXPIRED = 2u, INVALID_CLOCK = 4u, TRACE_OWNED = 8u, COUNTER_OWNED = 16u,
    INVALID_SEQUENCE = 128u, CLOCK_SHIFT = 8, CLOCK_MASK = 3u << CLOCK_SHIFT,
    PHASE_SHIFT = 10, PHASE_MASK = 3u << PHASE_SHIFT,
    IN_WAIT = 1u << 12, IN_USB = 1u << 13, POLICY = 1u << PEAK_POLICY_BIT,
    WRITER = 1u << WRITER_MODE_BIT,
    TRACE_ENABLE = 1u << 24, WORDS = 17,
    STATUS_MASK = CAPTURE_RUNNING | EXPIRED | INVALID_CLOCK | INVALID_SEQUENCE,
};

typedef struct {
    uint32_t tag, drawing_active, flags, started_or_elapsed;
    uint32_t stop_cycles, ready_offset, foreground_offset, usb, lcd, wait;
    uint32_t peak, gap_sum, peak_end_ticks;
    uint16_t peak_stages[5], gaps;
} peak_state;

extern volatile peak_state ds100_peak_state;
extern int ds100_prior_timing(void *, uint16_t, uint8_t *, size_t, size_t *);
extern void ds100_prior_usb_tail(void);
extern void ds100_prior_row(void *, uint16_t, uint16_t *);
extern void ds100_prior_wait(void);
extern ds100_draw_queue *volatile ds100_peak_queue;
extern int ds100_prior_writer(ds100_draw_queue *, uint16_t, uint16_t, int, const volatile uint16_t *);
extern int ds100_original_writer(ds100_draw_queue *, uint16_t, uint16_t, int, const volatile uint16_t *);

#define state ds100_peak_state
_Static_assert(sizeof(peak_state) == 64, "Reuse the capture state area.");
_Static_assert(offsetof(peak_state, flags) == CAPTURE_FLAGS_OFFSET, "Match the timing guard.");
_Static_assert(offsetof(peak_state, started_or_elapsed) == CAPTURE_START_OFFSET, "Match the hold timer.");

static uint32_t lock(void) {
    uint32_t mask;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(mask) :: "memory");
    return mask;
}

static void unlock(uint32_t mask) {
    __asm__ volatile ("msr primask, %0" :: "r"(mask) : "memory");
}

static uint32_t core_hz(uint32_t flags) {
    static const uint32_t clocks[] = {0, 168000000u, 200000000u, 250000000u};
    return clocks[(flags & CLOCK_MASK) >> CLOCK_SHIFT];
}

static unsigned phase(uint32_t flags) {
    return (flags & PHASE_MASK) >> PHASE_SHIFT;
}

static void finish(uint32_t flags, uint32_t now, uint32_t cycles, uint32_t reason) {
    const uint32_t pending = reason & (INVALID_CLOCK | INVALID_SEQUENCE) ? 0u : flags & PHASE_MASK;
    state.stop_cycles = pending ? cycles - state.stop_cycles : 0u;
    state.started_or_elapsed = now - state.started_or_elapsed;
    state.flags = (flags & (CLOCK_MASK | POLICY | WRITER | (UINT32_MAX << WRITER_COUNT_SHIFT))) |
                  pending | reason;
    if (!(DEBUG_STATUS & 1u)) {
        if (flags & COUNTER_OWNED) DWT_CONTROL &= ~1u;
        if (flags & TRACE_OWNED) TRACE_CONTROL &= ~TRACE_ENABLE;
    }
}

static int valid(uint32_t flags, uint32_t now, uint32_t cycles) {
    if (state.tag != PEAK_MAGIC || !(flags & CAPTURE_RUNNING)) return 0;
    const uint32_t hz = core_hz(flags);
    if (NATIVE_HZ != hz || CMSIS_HZ != hz || (DEBUG_STATUS & 1u) ||
            !(DWT_CONTROL & 1u) || !(TRACE_CONTROL & TRACE_ENABLE)) {
        finish(flags, now, cycles, INVALID_CLOCK);
        return 0;
    }
    if (now - state.started_or_elapsed >= CAPTURE_WINDOW_TICKS) {
        finish(flags, now, cycles, EXPIRED);
        return 0;
    }
    return 1;
}

/* The total and peak are exact. Store the peak's component times with a
 * shared power-of-two unit so all five fit in the reviewed scratch area. */
static unsigned stage_shift(uint32_t gap) {
    unsigned shift = 0;
    while ((gap >> shift) >= UINT16_MAX) ++shift;
    return shift;
}

static void save_peak(uint32_t gap, uint32_t flags, uint32_t ticks) {
    const unsigned shift = stage_shift(gap);
    state.peak = gap;
    state.peak_end_ticks = ticks;
    state.peak_stages[0] = (uint16_t)(state.ready_offset >> shift);
    state.peak_stages[1] = state.foreground_offset == UNKNOWN ? UINT16_MAX :
                          (uint16_t)((state.foreground_offset - state.ready_offset) >> shift);
    state.peak_stages[2] = flags & IN_USB ? UINT16_MAX : (uint16_t)(state.usb >> shift);
    state.peak_stages[3] = flags & IN_USB ? UINT16_MAX : (uint16_t)(state.lcd >> shift);
    state.peak_stages[4] = flags & IN_WAIT ? UINT16_MAX : (uint16_t)(state.wait >> shift);
}

void ds100_peak_event(unsigned event) {
    const uint32_t mask = lock();
    const uint32_t now = NATIVE_TICKS, cycles = DWT_CYCLES, flags = state.flags;
    const unsigned current = phase(flags);
    if (valid(flags, now, cycles) && event && now - state.started_or_elapsed >= CAPTURE_WARMUP_TICKS) {
        if (event == CAPTURE_DMA_STOPPED && !current) {
            state.stop_cycles = cycles;
            state.ready_offset = state.usb = state.lcd = state.wait = 0u;
            state.foreground_offset = UNKNOWN;
            state.flags = flags | (CAPTURE_DMA_STOPPED << PHASE_SHIFT);
        } else if (event == CAPTURE_RESULT_READY && current == CAPTURE_DMA_STOPPED) {
            state.ready_offset = cycles - state.stop_cycles;
            state.flags = (flags & ~PHASE_MASK) | (CAPTURE_RESULT_READY << PHASE_SHIFT);
        } else if (event == CAPTURE_DMA_RESTARTED && current == CAPTURE_RESULT_READY) {
            state.flags = (flags & ~PHASE_MASK) | (CAPTURE_DMA_RESTARTED << PHASE_SHIFT);
        } else if (event == CAPTURE_TRIGGER_READY && current == CAPTURE_DMA_RESTARTED) {
            const uint32_t gap = cycles - state.stop_cycles;
            if (!gap || gap > UINT32_MAX - state.gap_sum || state.gaps == UINT16_MAX) {
                finish(flags, now, cycles, INVALID_SEQUENCE);
            } else {
                state.gap_sum += gap;
                ++state.gaps;
                if (gap > state.peak) save_peak(gap, flags, now - state.started_or_elapsed);
                state.flags = flags & ~PHASE_MASK;
            }
        } else if (current) {
            finish(flags, now, cycles, INVALID_SEQUENCE);
        }
    }
    unlock(mask);
}

/* The current result can arrive after dispatcher entry. In that case the
 * first consumer entry provides the foreground boundary. */
void ds100_peak_mark(unsigned marker) {
    if (!marker || marker > 2u) return;
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    if (phase(flags) == CAPTURE_RESULT_READY && state.foreground_offset == UNKNOWN) {
        const uint32_t cycles = DWT_CYCLES;
        if (valid(flags, NATIVE_TICKS, cycles)) state.foreground_offset = cycles - state.stop_cycles;
    }
    unlock(mask);
}

static int begin_interval(uint32_t interval, uint32_t *start) {
    const uint32_t mask = lock();
    const uint32_t flags = state.flags;
    *start = DWT_CYCLES;
    const int started = !(flags & (IN_USB | IN_WAIT)) && valid(flags, NATIVE_TICKS, *start);
    if (started) state.flags = flags | interval;
    unlock(mask);
    return started;
}

static void record_interval(uint32_t start, uint32_t flag, uint32_t clear, volatile uint32_t *value) {
    const uint32_t mask = lock();
    const uint32_t flags = state.flags, cycles = DWT_CYCLES, now = NATIVE_TICKS;
    if ((flags & flag) && valid(flags, now, cycles)) {
        if (phase(flags)) {
            const uint32_t elapsed = cycles - start;
            const uint32_t gap_elapsed = cycles - state.stop_cycles;
            *value += elapsed < gap_elapsed ? elapsed : gap_elapsed;
        }
        state.flags = flags & ~clear;
    }
    unlock(mask);
}

void ds100_peak_usb_tail(void) {
    uint32_t start;
    const int measured = begin_interval(IN_USB, &start);
    ds100_prior_usb_tail();
    if (measured) record_interval(start, IN_USB, IN_USB, &state.usb);
}

void ds100_peak_row(void *context, uint16_t y, uint16_t *pixels) {
    if (state.tag != PEAK_MAGIC || !(state.flags & IN_USB)) {
        ds100_prior_row(context, y, pixels);
        return;
    }
    const uint32_t start = DWT_CYCLES;
    ds100_prior_row(context, y, pixels);
    record_interval(start, IN_USB, 0, &state.lcd);
}

void ds100_peak_wait(void) {
    const ds100_draw_queue *queue = ds100_peak_queue;
    uint32_t start;
    const int measured = queue && queue->mode == DRAW_ACTIVE &&
                         queue->capacity - queue->used < 1000u && begin_interval(IN_WAIT, &start);
    ds100_prior_wait();
    if (measured) record_interval(start, IN_WAIT, IN_WAIT, &state.wait);
}

int ds100_peak_steady_writer(ds100_draw_queue *queue, uint16_t x, uint16_t y, int reverse,
                             const volatile uint16_t *pixels) {
    const uint32_t flags = state.flags;
    if ((flags & (CAPTURE_RUNNING | CLOCK_MASK)) == (CAPTURE_RUNNING | (3u << CLOCK_SHIFT)) &&
            NATIVE_HZ == 250000000u && CMSIS_HZ == 250000000u &&
            NATIVE_TICKS - state.started_or_elapsed < CAPTURE_WINDOW_TICKS &&
            queue && queue->mode == DRAW_ACTIVE && queue->frame_id >= 3u)
        return ds100_prior_writer(queue, x, y, reverse, pixels);
    return ds100_original_writer(queue, x, y, reverse, pixels);
}

static void write_snapshot(uint8_t *out, size_t *length) {
    const uint32_t flags = state.flags;
    const uint32_t status = flags & STATUS_MASK;
    const int finished = !(status & (CAPTURE_RUNNING | INVALID_CLOCK | INVALID_SEQUENCE));
    uint32_t words[WORDS] = {PEAK_MAGIC, status, core_hz(flags),
        finished ? state.started_or_elapsed : 0u,
        (flags & POLICY) ? 2u : (flags & WRITER) ? 1u : 0u,
        finished ? state.gaps : 0u, finished ? state.gap_sum : 0u, finished ? state.peak : 0u,
        finished ? state.peak_end_ticks : 0u, 0u, 0u, 0u, 0u, 0u,
        finished ? phase(flags) : 0u, finished ? state.stop_cycles : 0u,
        finished ? flags >> WRITER_COUNT_SHIFT : 0u};
    const unsigned shift = stage_shift(words[7]);
    if (words[7]) {
        for (unsigned i = 0; i < 5; ++i) {
            const uint32_t value = state.peak_stages[i];
            words[9u + i] = value == UINT16_MAX ? UNKNOWN : value << shift;
        }
    }
    for (unsigned i = 0; i < WORDS; ++i) {
        uint32_t value = words[i];
        for (unsigned j = 0; j < sizeof(value); ++j) {
            *out++ = (uint8_t)value;
            value >>= 8;
        }
    }
    *length = sizeof(words);
}

int ds100_peak_command(void *context, uint16_t action, uint8_t *out, size_t capacity, size_t *length) {
    if (action > 7u || !out || !length || capacity < WORDS * 4u) return -1;
    if (state.tag == PEAK_MAGIC) {
        const uint32_t mask = lock();
        const uint32_t flags = state.flags, now = NATIVE_TICKS, cycles = DWT_CYCLES;
        const int active = valid(flags, now, cycles);
        if (action < 3u && action != 1u) {
            if (active && action == 0u) finish(flags, now, cycles, 0);
            unlock(mask);
            if (active && action == 2u) return -1;
            write_snapshot(out, length);
            return 0;
        }
        if (active) {
            unlock(mask);
            return -1;
        }
        state.flags = 0;
        state.tag = PROFILE_MAGIC;
        unlock(mask);
    }
    if (action < 5u) return ds100_prior_timing(context, action, out, capacity, length);
    if (ds100_prior_timing(context, action == 5u ? 1u : 4u, out, capacity, length)) return -1;
    const uint32_t mask = lock();
    if (action == 7u) state.flags |= POLICY;
    state.gaps = 0u;
    state.peak = state.gap_sum = state.peak_end_ticks = 0u;
    state.tag = PEAK_MAGIC;
    unlock(mask);
    write_snapshot(out, length);
    return 0;
}
