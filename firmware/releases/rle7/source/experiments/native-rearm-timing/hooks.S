/* SPDX-License-Identifier: GPL-3.0-only */
#include "../acquisition-segments/timing.h"
.syntax unified
.cpu cortex-m4
.thumb

/* Test the two existing state words without changing the caller's flags. */
.macro NATIVE_OR_TAIL skip
    push {r0, r1}
    ldr r0, =ds100_capture_state
    ldr r1, [r0, #CAPTURE_FLAGS_OFFSET]
    ubfx r1, r1, #0, #1
    cbz r1, \skip
    ldr r1, [r0, #60]
    cbz r1, \skip
    pop {r0, r1}
.endm

.macro NATIVE_ENTRY name, marker
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    NATIVE_OR_TAIL .Ldisabled\@
    push {r0-r5, r12, lr}
    mrs r4, apsr
    movs r0, #\marker
    bl ds100_native_mark
    msr apsr_nzcvqg, r4
    pop {r0-r5, r12, lr}
    b .Loriginal\@
.Ldisabled\@:
    pop {r0, r1}
.Loriginal\@:
.if \marker == 0
    ldr r2, =0x20027e98  /* Relocate the original literal load. */
    ldr r3, [r1]
    ldr pc, =0x08020127
.else
    push.w {r4-r10, lr}
    ldr pc, =0x0803e817
.endif
.size \name, .-\name
.ltorg
.endm

NATIVE_ENTRY ds100_native_prepare_entry, 0
NATIVE_ENTRY ds100_native_rearm_entry, 1

.p2align 2
.global ds100_native_restart_marker
.type ds100_native_restart_marker,%function
.thumb_func
ds100_native_restart_marker:
    NATIVE_OR_TAIL .Llegacy_restart
    push {r0-r5, r12, lr}
    mrs r4, apsr
    bl ds100_native_restart
    msr apsr_nzcvqg, r4
    pop {r0-r5, r12, lr}
    ldr pc, =0x08013789
.Llegacy_restart:
    pop {r0, r1}
    ldr pc, =0x0807d605
.size ds100_native_restart_marker, .-ds100_native_restart_marker
.ltorg
