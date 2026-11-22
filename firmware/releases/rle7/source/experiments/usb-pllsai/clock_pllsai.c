/* R12: independent USB clock and native 200/250 MHz acquisition policy.
 * Pinned GD32F450 / V4.8 bindings. No new static RAM is used.
 */
/* SPDX-License-Identifier: GPL-3.0-only */
#include <stdint.h>
#include <stddef.h>

#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define CTL REG(0x40023800u)
#define PLL REG(0x40023804u)
#define CFG REG(0x40023808u)
#define SAI REG(0x40023888u)
#define ADD REG(0x400238c0u)
#define CLOCK_HZ REG(0x200213d0u)
#define BOARD_HZ REG(0x200203ccu)
#define USB_ACTIVE (*(volatile const uint8_t *)(uintptr_t)0x200213f0u)
#define HXTAL_EN (UINT32_C(1) << 16)
#define HXTAL_STB (UINT32_C(1) << 17)
#define PLL_EN (UINT32_C(1) << 24)
#define PLL_STB (UINT32_C(1) << 25)
#define SAI_EN (UINT32_C(1) << 28)
#define SAI_STB (UINT32_C(1) << 29)
#define SHARED_MASK UINT32_C(0x0040003f)
#define SHARED_VALUE UINT32_C(0x00400006)
#define SPINS UINT32_C(32768)

static uint32_t lock(void) {
    uint32_t saved;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(saved) :: "memory");
    return saved;
}

static void unlock(uint32_t saved) {
    __asm__ volatile ("msr primask, %0" :: "r"(saved) : "memory");
}

static void barrier(void) { __asm__ volatile ("dsb\n isb" ::: "memory"); }

static int wait_bits(volatile uint32_t *reg, uint32_t mask, uint32_t value) {
    uint32_t remaining = SPINS;
    while (remaining--) if ((*reg & mask) == value) return 1;
    return 0;
}

static uint32_t core_update(void) {
    /* Execute the original CMSIS clock calculation, including AHB division. */
    ((void (*)(void))(uintptr_t)0x08042da5u)();
    return CLOCK_HZ;
}

static uint32_t native_core(void) {
    uintptr_t p = REG(0x20020020u);
    if (!(p & 3u) && ((p >= 0x20020000u && p <= 0x2002ff50u) ||
                      (p >= 0x10000000u && p <= 0x1000ffc0u))) {
        uint32_t rate = REG(p + 0x1cu);
        return (rate == 125000000u || rate == 250000000u) ? 250000000u : 200000000u;
    }
    return BOARD_HZ == 250000000u ? 250000000u : 200000000u;
}

int ds100_clock_usb48_ready(void) {
    uint32_t saved = lock();
    uint32_t value, n, p;
    int ready = 0;
    /* The PLL source and /6 are shared with PLLSAI. Do not repair an unexpected
     * shared input while another PLL can be using it.
     */
    if ((PLL & SHARED_MASK) != SHARED_VALUE) goto done;
    CTL |= HXTAL_EN;
    if (!wait_bits(&CTL, HXTAL_STB, HXTAL_STB)) goto done;
    if (CTL & SAI_EN) {
        value = SAI;
        n = (value >> 6) & 511u;
        p = (((value >> 16) & 3u) + 1u) * 2u;
        /* Preserve a compatible pre-existing PLLSAI user. Do not change R or
         * reserved fields, or overwrite an enabled PLL with another frequency.
         */
        if (n < 50u || 2000000u * n / p != 48000000u) goto done;
        if (!wait_bits(&CTL, SAI_STB, SAI_STB)) goto done;
    } else {
        if (!wait_bits(&CTL, SAI_STB, 0)) goto done;
        SAI = (SAI & ~UINT32_C(0x00037fc0)) | (192u << 6) | (3u << 16);
        CTL |= SAI_EN;
        if (!wait_bits(&CTL, SAI_STB, SAI_STB)) {
            CTL &= ~SAI_EN;
            goto done;
        }
    }
    /* CK48M=PLL48M, PLL48M=PLLSAIP. The clock must be stable first. */
    ADD = (ADD & ~3u) | 2u;
    barrier();
    ready = 1;
done:
    unlock(saved);
    return ready;
}

static uint32_t set_core(uint32_t hz) {
    uint32_t saved = lock();
    uint32_t previous = PLL;
    uint32_t desired = UINT32_C(0x07400006) | ((hz / 1000000u) << 6);
    uint32_t actual;
    if ((previous & SHARED_MASK) != SHARED_VALUE) goto done;
    /* USB bus resume does not imply that the MCU lost its system clock.
     * Avoid a needless IRC16M detour while background acquisition is running.
     * A real clock-loss wakeup still follows the bounded restore path below.
     */
    if (previous == desired && (CTL & UINT32_C(0x03030000)) == UINT32_C(0x03030000) &&
            (CFG & UINT32_C(0x0000fcff)) == UINT32_C(0x0000940a)) goto done;
    CTL |= 1u | HXTAL_EN;
    if (!wait_bits(&CTL, 2u | HXTAL_STB, 2u | HXTAL_STB)) goto done;

    /* Keep the stock high-drive power settings. These registers have already
     * been initialized by SystemInit; the waits also cover a wakeup path.
     */
    REG(0x40023840u) |= UINT32_C(0x10000000);
    REG(0x40007000u) |= UINT32_C(0x0000c000);
    REG(0x40007000u) |= UINT32_C(0x00010000);
    if (!wait_bits((volatile uint32_t *)(uintptr_t)0x40007004u, 0x10000u, 0x10000u)) goto done;
    REG(0x40007000u) |= UINT32_C(0x00020000);
    if (!wait_bits((volatile uint32_t *)(uintptr_t)0x40007004u, 0x20000u, 0x20000u)) goto done;

    /* AHB /1, APB1 /4, APB2 /2, exactly as the stock 168/200/250 paths.
     * Select IRC16M and wait for the actual status before disabling the PLL.
     * HXTAL, PLLSAI and their shared source/prescaler remain unchanged.
     */
    CFG = (CFG & ~UINT32_C(0x0000fcf3)) | UINT32_C(0x00009400);
    barrier();
    if (!wait_bits(&CFG, 12u, 0)) goto done;
    CTL &= ~PLL_EN;
    if (!wait_bits(&CTL, PLL_STB, 0)) goto done;
    PLL = desired;
    CTL |= PLL_EN;
    if (!wait_bits(&CTL, PLL_STB, PLL_STB)) {
        /* Attempt the previous working main PLL. USB PLLSAI is independent.
         * If recovery also fails, remain on IRC16M and report its actual rate.
         */
        CTL &= ~PLL_EN;
        if (!wait_bits(&CTL, PLL_STB, 0)) goto done;
        PLL = previous;
        CTL |= PLL_EN;
        if (!wait_bits(&CTL, PLL_STB, PLL_STB)) goto done;
    }
    CFG = (CFG & ~3u) | 2u;
    barrier();
    if (!wait_bits(&CFG, 12u, 8u)) {
        CFG &= ~3u;
        barrier();
        (void)wait_bits(&CFG, 12u, 0);
    }
done:
    actual = core_update();
    unlock(saved);
    return actual;
}

static void apply_core(uint32_t hz) {
    uint32_t actual = core_update();
    uintptr_t table;
    if (actual == hz && BOARD_HZ == hz) return;
    /* Preserve the stock dispatcher order and its board callbacks. Run the
     * callbacks with the recovered actual rate if a bounded switch fails.
     */
    ((void (*)(void))(uintptr_t)0x0803e9e5u)();
    ((void (*)(uint32_t))(uintptr_t)0x0801027du)(50u);
    actual = set_core(hz);
    BOARD_HZ = actual;
    ((void (*)(uint32_t))(uintptr_t)0x080101d5u)(actual / 1000000u);
    table = REG(0x20021388u); /* 0x200203b8 + 0xfd0 */
    ((void (*)(uint32_t))(uintptr_t)REG(table + 4u))(actual);
    ((void (*)(uint32_t))(uintptr_t)REG(table + 0x1cu))(actual);
}

void ds100_v48_clock_hook(uint32_t hz) {
    if (hz != 168000000u && hz != 200000000u && hz != 250000000u) return;
    if (USB_ACTIVE) {
        if (ds100_clock_usb48_ready()) {
            /* 168 MHz is the USB initialization request, not a sampling mode. */
            if (hz == 168000000u) hz = native_core();
        } else {
            /* Retain the R11 USB-compatible rate when an independent source
             * cannot lock or PLLSAI belongs to an incompatible peripheral.
             */
            hz = 168000000u;
        }
    }
    apply_core(hz);
    if (USB_ACTIVE && CLOCK_HZ == 168000000u &&
            (!(CTL & SAI_STB) || (ADD & 3u) != 2u)) ADD &= ~3u;
}

void ds100_clock_usb_select(uint32_t ignored) {
    (void)ignored;
    if (!ds100_clock_usb48_ready()) {
        apply_core(168000000u);
        if (CLOCK_HZ == 168000000u) ADD &= ~3u;
    }
}

void ds100_clock_usb_resume(void) {
    uint32_t desired = native_core();
    uint32_t actual;
    /* Replace only the USB resume call to SystemInit. Reset-time SystemInit
     * stays original. Resume does not stop or reinitialize acquisition tasks.
     */
    if (!ds100_clock_usb48_ready()) desired = 168000000u;
    actual = set_core(desired);
    BOARD_HZ = actual;
    ((void (*)(uint32_t))(uintptr_t)0x080101d5u)(actual / 1000000u);
    if (actual == 168000000u && (ADD & 3u) != 2u) ADD &= ~3u;
}

void ds100_clock_report(uint32_t words[8]) {
    uint32_t saved = lock();
    words[0] = CTL; words[1] = PLL; words[2] = CFG; words[3] = SAI;
    words[4] = ADD; words[5] = CLOCK_HZ; words[6] = BOARD_HZ; words[7] = native_core();
    unlock(saved);
}
