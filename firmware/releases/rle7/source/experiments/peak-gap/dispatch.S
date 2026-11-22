/* SPDX-License-Identifier: GPL-3.0-only */
#include "peak.h"
.syntax unified
.cpu cortex-m4
.thumb

/* Each legacy path replays the inspected, position-independent prologue. */
.macro PEAK_TAG not_peak
    push {r2, r3}
    ldr r2, =ds100_peak_state
    ldr r2, [r2]
    ldr r3, =PEAK_MAGIC
    cmp r2, r3
    pop {r2, r3}
    bne \not_peak
.endm

.macro ENTRY name
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
.endm

ENTRY ds100_peak_event_entry
    PEAK_TAG .Levent_legacy
    b.w ds100_peak_event
.Levent_legacy:
    push.w {r3-r9, lr}
    ldr pc, =0x0807e211

ENTRY ds100_peak_mark_entry
    PEAK_TAG .Lmark_legacy
    b.w ds100_peak_mark
.Lmark_legacy:
    push.w {r4-r8, lr}
    ldr pc, =0x0807e311

ENTRY ds100_peak_consumer_entry
    PEAK_TAG .Lconsume_legacy
    push {r0-r2, lr}
    movs r0, #2
    bl ds100_peak_mark
    pop.w {r0-r2, lr}
    ldr pc, =0x08029d57
.Lconsume_legacy:
    push.w {r4-r10, lr}
    ldr pc, =0x0807e395

ENTRY ds100_peak_writer_entry
    ldr ip, =ds100_peak_state
    ldr ip, [ip, #CAPTURE_FLAGS_OFFSET]
    tst ip, #(1 << PEAK_POLICY_BIT)
    beq .Lwriter_legacy
    tst ip, #CAPTURE_RUNNING
    beq .Lwriter_legacy
    PEAK_TAG .Lwriter_legacy
    b.w ds100_peak_steady_writer
.Lwriter_legacy:
    ldr pc, =0x0807f1c1

.macro INTERVAL name, target
ENTRY ds100_peak_\name\()_entry
    ldr ip, =ds100_peak_state
    ldr ip, [ip, #CAPTURE_FLAGS_OFFSET]
    lsls.w ip, ip, #31
    bpl .Linterval_legacy\@
    PEAK_TAG .Linterval_legacy\@
    b.w ds100_peak_\name
.Linterval_legacy\@:
    ldr pc, =\target
.endm

INTERVAL usb_tail, ds100_prior_usb_tail
INTERVAL wait, ds100_prior_wait
.ltorg
