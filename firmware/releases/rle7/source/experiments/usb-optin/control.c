/* SPDX-License-Identifier: GPL-3.0-only */
#define ds100_v48_auto_usb ds100_r11_auto_usb_unused
#define ds100_v48_usb_start ds100_r11_usb_start_unused
#include "../input-ack/input_ack.c"
#undef ds100_v48_auto_usb
#undef ds100_v48_usb_start
#include "optin.h"
#define USB_FLAG (*(volatile uint8_t *)(uintptr_t)0x200213f0u)
#define RCU_CTL (*(volatile uint32_t *)(uintptr_t)0x40023800u)
#define RCU_SAI (*(volatile uint32_t *)(uintptr_t)0x40023888u)
#define RCU_ADD (*(volatile uint32_t *)(uintptr_t)0x400238c0u)
#define OWN_SAI UINT32_C(0x10000000)
extern void *ds100_v48_queued_storage_core;

uint32_t ds100_optin_flags(void) { return ds100_v48_input_state.auto_started; }
int ds100_optin_enabled(void) { return (ds100_optin_flags() & OPT_ENABLED)!=0; }

static int begin_usb(void) {
    input_state *s=&ds100_v48_input_state;
    uint32_t before=RCU_CTL;
    s->auto_started|=OPT_ATTEMPTED;
    if (!USB_FLAG) {
        ds100_v48_original_usb_start();
        if (!(before & OWN_SAI) && (RCU_CTL & OWN_SAI)) s->auto_started|=OPT_OWN_SAI;
        if (USB_FLAG) ((void (*)(void))(uintptr_t)0x0802d457u)();
    }
    return USB_FLAG ? 0 : -2;
}

static void end_usb(void) {
    input_state *s=&ds100_v48_input_state;
    unsigned was_active=USB_FLAG;
    s->remote_keys=0; s->wheel1=0; s->wheel2=0;
    ds100_stream_stop_local();
    if (USB_FLAG) ((void (*)(void *))(uintptr_t)0x080452f5u)((void *)(uintptr_t)0x200213f0u);
    *(volatile uint8_t *)(uintptr_t)0x20021515u=0;
    ds100_v48_queued_storage_core=0;
    if ((s->auto_started & OPT_OWN_SAI) && !USB_FLAG && RCU_SAI==UINT32_C(0x24033000)) {
        RCU_ADD &= ~UINT32_C(3);
        RCU_CTL &= ~OWN_SAI;
    }
    s->auto_started &= ~(OPT_ATTEMPTED|OPT_OWN_SAI);
    /* A failed independent PLL can leave the USB fallback at 168 MHz.
     * Native acquisition apply selects the original 200/250 MHz now that
     * USB is inactive. Do not restart acquisition on an already-off path. */
    if (was_active) ((void (*)(void))(uintptr_t)0x0802d457u)();
}

void ds100_v48_auto_usb(void) {
    input_state *s=&ds100_v48_input_state;
    if (s->magic!=INPUT_MAGIC) return;
    if (!(s->auto_started & OPT_LOADED))
        s->auto_started=OPT_LOADED | (ds100_optin_pref_load() ? OPT_ENABLED : 0u);
    if ((s->auto_started & (OPT_ENABLED|OPT_ATTEMPTED))==OPT_ENABLED) (void)begin_usb();
}

void ds100_v48_usb_start(void) {
    if (ds100_optin_enabled()) (void)begin_usb();
}

int ds100_optin_toggle(void) {
    input_state *s=&ds100_v48_input_state;
    unsigned next=!ds100_optin_enabled();
    if (ds100_optin_pref_save(next)) return -1;
    if (next) { s->auto_started|=OPT_ENABLED; return begin_usb(); }
    s->auto_started &= ~OPT_ENABLED;
    s->remote_keys=0; s->wheel1=0; s->wheel2=0;
    ds100_stream_stop_local();
    if (!(s->auto_started & OPT_FILES)) end_usb();
    return 0;
}

int ds100_optin_files(void) {
    input_state *s=&ds100_v48_input_state;
    if (s->auto_started & OPT_FILES) {
        s->auto_started &= ~OPT_FILES;
        if (!ds100_optin_enabled()) end_usb();
        return 0;
    }
    s->auto_started|=OPT_FILES;
    return begin_usb();
}

void ds100_optin_menu_close(void) {
    ds100_v48_input_state.auto_started &= ~OPT_FILES;
    if (!ds100_optin_enabled()) end_usb();
}
