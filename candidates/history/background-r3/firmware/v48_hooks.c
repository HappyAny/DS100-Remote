/* EXPERIMENTAL. These bindings have not run on a physical DS100. */
#include "ds100_remote.h"

#if !defined(__arm__) && !defined(__thumb__)
#error "Build this file for ARM Thumb only."
#endif

/* Verified against the decoded V4.8 image, not against the connected V4.7. */
#define BOT_STATE ((volatile uint8_t *)(uintptr_t)0x20021514u)
#define CDB_DATA ((volatile uint8_t *)(uintptr_t)0x2002d74cu)
#define MSC_BUFFER ((uint8_t *)(uintptr_t)0x2002d75cu)
#define CBW_DATA ((volatile uint8_t *)(uintptr_t)0x2002e75cu)
#define PENDING_STATE 0x7fu

typedef void (*usb_reply_function)(void *core, const uint8_t *buffer, uint32_t length);
typedef void (*idle_function)(void);
extern void ds100_v48_original_scsi(void *core);
extern const ds100_backend *ds100_v48_screen_backend(uint32_t *scan_id);
extern size_t ds100_v48_make_info(uint8_t *out, size_t capacity);
extern void ds100_v48_original_clock(uint32_t frequency);

void ds100_v48_clock_hook(uint32_t frequency) {
    /* Stock USB initialization selects 168 MHz. The stock dispatcher can also
     * select 200 / 250 MHz. Keep its USB-compatible setting while USB is active.
     * Use the original dispatcher so its clock bookkeeping stays in use.
     */
    if (*(const volatile uint8_t *)(uintptr_t)0x200213f0u)
        frequency = UINT32_C(168000000);
    ds100_v48_original_clock(frequency);
}

static void barrier(void) { __asm__ volatile ("dmb" ::: "memory"); }

void ds100_v48_scsi_hook(void *core) {
    usb_reply_function reply = (usb_reply_function)(uintptr_t)0x080453fbu;
    uint8_t state;
#if defined(DS100_BACKGROUND_USB)
    /* MODE SENSE(6): set the standard write-protect bit. The image builder also
     * removes the only WRITE(10) dispatch branch. The stock error path then
     * rejects host writes before the storage writer can run.
     */
    static const uint8_t read_only_mode[8] = {3u, 0u, 0x80u, 0u, 0u, 0u, 0u, 0u};
    if (CDB_DATA[0] == 0x1au) {
        reply(core, read_only_mode, sizeof(read_only_mode));
        return;
    }
#endif
    if (CDB_DATA[0] != 0xc0u || !(CBW_DATA[12] & 0x80u)) {
        ds100_v48_original_scsi(core);
        return;
    }
    state = BOT_STATE[1];
    if (state == 0u) {
        /* Reuse the idle transfer's buffer-pointer field. The request stays open. */
        *(volatile uintptr_t *)(void *)(BOT_STATE + 8) = (uintptr_t)core;
        barrier();
        BOT_STATE[1] = PENDING_STATE;
    } else if (state == 1u || state == 3u) {
        /* Continue the stock DATA-IN / CSW completion path. No LCD access here. */
        reply(core, (const uint8_t *)0, 0);
    } else if (state != PENDING_STATE) {
        ds100_v48_original_scsi(core);
    }
}

void ds100_v48_poll_wrapper(void) {
    idle_function original_idle = (idle_function)(uintptr_t)0x0801538bu;
    usb_reply_function reply = (usb_reply_function)(uintptr_t)0x080453fbu;
    uint8_t command[16];
    void *core;
    size_t length;
    ds100_backend info_backend;
    const ds100_backend *backend;
    unsigned index;
    original_idle();
    if (BOT_STATE[1] != PENDING_STATE || CDB_DATA[0] != 0xc0u) return;
    barrier();
    core = (void *)*(volatile uintptr_t *)(void *)(BOT_STATE + 8);
    for (index = 0; index < 16; ++index) command[index] = CDB_DATA[index];
    /* The display task performs LCD reads. The USB interrupt only queues work. */
    backend = ds100_v48_screen_backend((uint32_t *)0);
    if (command[1] == 1u) {
        /* Source and destination are the same payload bytes during the handler's
         * information copy. Only the response header is filled afterward.
         */
        if (!ds100_v48_make_info(MSC_BUFFER + 20u, 4076u)) return;
        info_backend = *backend;
        info_backend.info_json = (const char *)(MSC_BUFFER + 20u);
        backend = &info_backend;
    }
    length = ds100_remote_handle(backend, command, MSC_BUFFER, 4096u);
    /* A USB reset can cancel the pending request while the display task runs. */
    if (!length || BOT_STATE[1] != PENDING_STATE || CDB_DATA[0] != 0xc0u) return;
    /* A new C0 request can arrive after reset before this scan ends. Leave the
     * new request queued instead of replying with data from the old request.
     */
    for (index = 0; index < 16; ++index) if (CDB_DATA[index] != command[index]) return;
    BOT_STATE[1] = 0;
    barrier();
    reply(core, MSC_BUFFER, (uint32_t)length);
}
