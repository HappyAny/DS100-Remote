/* SPDX-License-Identifier: GPL-3.0-only */
#include "layout.h"
#include "../usb-optin/optin.h"

enum { OPT_SAVED = 32u, FLAG_MASK = (1u << DS100_BOOT_FLAG_BITS) - 1u };

extern volatile uint32_t ds100_boot_control;
extern const uintptr_t ds100_boot_initial_targets[], ds100_boot_native_targets[], ds100_boot_remote_targets[];
extern void ds100_existing_controls_init(void);
extern void ds100_native_dispatcher(void);
extern int ds100_begin_usb(void);
extern void ds100_service_pending_request(void);
extern void ds100_menu_label_text(void *label, const char *value);

void ds100_boot_init(void) {
    ds100_existing_controls_init();
    ds100_boot_control = (uintptr_t)ds100_boot_initial_targets;
}

uint32_t ds100_boot_flags(void) {
    return ds100_boot_control & FLAG_MASK;
}

void ds100_boot_load_settings(void) {
    if (ds100_boot_control & OPT_LOADED) return;

    const int enabled = ds100_optin_pref_load();
    const uintptr_t targets = enabled ? (uintptr_t)ds100_boot_remote_targets : (uintptr_t)ds100_boot_native_targets;
    const uint32_t file_flags = ds100_boot_control & (OPT_FILES | OPT_ATTEMPTED | OPT_OWN_SAI);
    ds100_boot_control = targets | file_flags | OPT_LOADED | (enabled ? OPT_ENABLED | OPT_SAVED : 0u);
    if (enabled) (void)ds100_begin_usb();
}

int ds100_boot_toggle(void) {
    const unsigned enable_next_boot = (ds100_boot_control & OPT_SAVED) == 0;
    if (ds100_optin_pref_save(enable_next_boot)) return -1;
    ds100_boot_control ^= OPT_SAVED;
    return 0;
}

void ds100_boot_native_poll(void) {
    ds100_native_dispatcher();
    if (ds100_boot_control & OPT_FILES) ds100_service_pending_request();
}

typedef struct {
    uint8_t native[0x78];
    void *buttons[2], *status;
    uint32_t selected;
    int32_t error;
} usb_menu;

_Static_assert(offsetof(usb_menu, selected) == 0x84u, "The ACT4 USB menu layout differs.");

void ds100_boot_refresh_menu(usb_menu *menu) {
    const uint32_t flags = ds100_boot_flags();
    const unsigned saved = (flags & OPT_SAVED) != 0;
    const unsigned running = (flags & OPT_ENABLED) != 0;
    const unsigned files = (flags & OPT_FILES) != 0;
    const char *status;

    ds100_menu_label_text(menu->buttons[0], saved ? "PC Remote at startup: ON" : "PC Remote at startup: OFF");
    ds100_menu_label_text(menu->buttons[1], files ? "USB Files (read-only): ON" : "USB Files (read-only): OFF");
    if (menu->error == -1) status = "Save failed";
    else if (menu->error) status = "USB initialization failed";
    else if (saved != running) status = "Restart to apply PC Remote";
    else if (running) status = "PC Remote active";
    else if (files) status = "Files only";
    else status = "PC Remote inactive";
    ds100_menu_label_text(menu->status, status);

    void (*const dirty)(void *) = (void (*)(void *))(uintptr_t)0x080168adu;
    for (unsigned i = 0; i < 2u; ++i) {
        uint8_t *button = menu->buttons[i];
        *(uint16_t *)(button + 0x62u) = i == menu->selected ? 0x001fu : 0xe71cu;
        *(uint16_t *)(button + 0x68u) = i == menu->selected ? 0xffffu : 0u;
        dirty(button);
    }
}
