/* SPDX-License-Identifier: GPL-3.0-only */
#include "layout.h"
.syntax unified
.cpu cortex-m4
.thumb

/* Only IP changes before the selected destination executes. */
.macro ROUTE name, index
.section .text.\name,"ax",%progbits
.p2align 2
.global ds100_boot_\name
.type ds100_boot_\name,%function
.thumb_func
ds100_boot_\name:
    ldr.w ip, =ds100_boot_control
    ldr.w ip, [ip]
    bfc ip, #0, #DS100_BOOT_FLAG_BITS
    ldr.w pc, [ip, #(4 * \index)]
.size ds100_boot_\name, .-ds100_boot_\name
.ltorg
.endm

ROUTE point, 0
ROUTE rect, 1
ROUTE bitmap, 2
ROUTE span_a, 3
ROUTE span_b, 4
ROUTE clear, 5
ROUTE keys, 6
ROUTE wheels, 7
ROUTE scan, 8
ROUTE poll, 9

.section .text.ds100_boot_first_poll,"ax",%progbits
.p2align 2
.global ds100_boot_first_poll
.type ds100_boot_first_poll,%function
.thumb_func
ds100_boot_first_poll:
    push.w {r0-r3, ip, lr}
    bl ds100_boot_load_settings
    pop.w {r0-r3, ip, lr}
    b.w ds100_boot_poll
.size ds100_boot_first_poll, .-ds100_boot_first_poll

.macro TABLE name
.p2align DS100_BOOT_FLAG_BITS
.global ds100_boot_\name
.type ds100_boot_\name,%object
ds100_boot_\name:
.endm

.macro NATIVE_TARGETS
    .word ds100_native_point, ds100_native_rect, ds100_native_bitmap
    .word ds100_native_span_a, ds100_native_span_b, ds100_native_clear
    .word ds100_native_keys, ds100_native_wheels
.endm

.section .rodata.ds100_boot_targets,"a",%progbits
TABLE initial_targets
    NATIVE_TARGETS
    .word ds100_remote_scan, ds100_boot_first_poll
.size ds100_boot_initial_targets, .-ds100_boot_initial_targets

TABLE native_targets
    NATIVE_TARGETS
    .word ds100_native_scan, ds100_boot_native_poll
.size ds100_boot_native_targets, .-ds100_boot_native_targets

TABLE remote_targets
    .word ds100_remote_point, ds100_remote_rect, ds100_remote_bitmap
    .word ds100_remote_span_a, ds100_remote_span_b, ds100_remote_clear
    .word ds100_remote_keys, ds100_remote_wheels, ds100_remote_scan, ds100_remote_poll
.size ds100_boot_remote_targets, .-ds100_boot_remote_targets
