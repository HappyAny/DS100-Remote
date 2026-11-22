/* SPDX-License-Identifier: GPL-3.0-only */
#include "timing.h"
.syntax unified
.cpu cortex-m4
.thumb

/* Keep APSR and all arguments intact when falling through to stock code. */
.macro DETAIL_OR_SKIP skip
    push {r2, r3}
    ldr r2, =ds100_capture_state
    ldr r2, [r2, #CAPTURE_FLAGS_OFFSET]
    ubfx r3, r2, #FOREGROUND_DETAIL_BIT, #1
    ubfx r2, r2, #0, #1
    and.w r2, r2, r3
    cbz r2, \skip
    pop {r2, r3}
.endm

.p2align 2
.global ds100_foreground_tick
.type ds100_foreground_tick,%function
.thumb_func
ds100_foreground_tick:
    DETAIL_OR_SKIP .Llegacy_tick
    push {r3, lr}
    mov r0, r6                 /* GUI context at the reviewed BL site. */
    bl ds100_foreground_poll
    cbz r0, .Lregular_call
    pop {r3, lr}
    ldr pc, =0x08016f73        /* The same pass already ran the consumer. */
.Lregular_call:
    pop {r3, pc}
.Llegacy_tick:
    pop {r2, r3}
    ldr pc, =0x0807d55d
.size ds100_foreground_tick, .-ds100_foreground_tick
.ltorg

.p2align 2
.global ds100_foreground_consumer_call
.type ds100_foreground_consumer_call,%function
.thumb_func
ds100_foreground_consumer_call:
    ldr r1, [r1, #0x1c]       /* The displaced stock virtual method load. */
    DETAIL_OR_SKIP .Lconsumer_tail
    push {r2, r3}
    ldr r2, =0x08029d57
    eor.w r2, r2, r1
    cbnz r2, .Lconsumer_tail
    pop {r2, r3}
    mov.w r1, #0
    b ds100_foreground_consume
.Lconsumer_tail:
    pop {r2, r3}
    bx r1
.size ds100_foreground_consumer_call, .-ds100_foreground_consumer_call
.ltorg

/* Run the existing USB service and baseline hold with their original frame. */
.p2align 2
.global ds100_foreground_usb_tail
.type ds100_foreground_usb_tail,%function
.thumb_func
ds100_foreground_usb_tail:
    push {r3, lr}
    ldr pc, =0x080788bb
.size ds100_foreground_usb_tail, .-ds100_foreground_usb_tail
.ltorg

.macro MARK name, marker
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    DETAIL_OR_SKIP .Ldisabled\@
    push {r0-r5, r12, lr}
    mrs r4, apsr
    movs r0, #\marker
    bl ds100_foreground_mark
    msr apsr_nzcvqg, r4
    pop {r0-r5, r12, lr}
    b .Lnative\@
.Ldisabled\@:
    pop {r2, r3}
.Lnative\@:
.if \marker == FOREGROUND_READY
    mov r0, r1
    bx lr
.elseif \marker == FOREGROUND_PREPARE
    ldr r2, =0x20027e98
    ldr r3, [r1]
    ldr pc, =0x08020127
.else
    push.w {r4-r10, lr}
    ldr pc, =0x0803e817
.endif
.size \name, .-\name
.ltorg
.endm

MARK ds100_foreground_ready, FOREGROUND_READY
MARK ds100_foreground_prepare, FOREGROUND_PREPARE
MARK ds100_foreground_rearm, FOREGROUND_REARM
