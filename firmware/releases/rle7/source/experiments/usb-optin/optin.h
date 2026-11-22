/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef DS100_USB_OPTIN_H
#define DS100_USB_OPTIN_H
#include <stddef.h>
#include <stdint.h>
enum { OPT_LOADED=1u, OPT_ENABLED=2u, OPT_ATTEMPTED=4u, OPT_FILES=8u, OPT_OWN_SAI=16u };
int ds100_optin_pref_load(void);
int ds100_optin_pref_save(unsigned enabled);
uint32_t ds100_optin_flags(void);
int ds100_optin_enabled(void);
int ds100_optin_toggle(void);
int ds100_optin_files(void);
void ds100_optin_menu_close(void);
void ds100_stream_stop_local(void);
void *ds100_usb_menu_create(void *memory, void *parent, const int16_t *rectangle, const char *unused);
#endif
