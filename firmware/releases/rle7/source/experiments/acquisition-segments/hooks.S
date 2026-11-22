/* SPDX-License-Identifier: GPL-3.0-only */
#include "timing.h"
.syntax unified
.cpu cortex-m4
.thumb

/* At acquisition markers, keep the original ABI, flags and registers.
 * The old ACT1 marker points are unchanged. */
.macro SAMPLE name, event, target
.section .text.\name,"ax",%progbits
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    push {r0, r1}
    ldr r0, =ds100_capture_state
    ldr r0, [r0, #CAPTURE_FLAGS_OFFSET]
    ubfx r0, r0, #0, #1
    cbnz r0, .Lsample\@
    pop {r0, r1}
    ldr pc, =\target
.Lsample\@:
    pop {r0, r1}
    push {r0-r5, r12, lr}
    mrs r4, apsr
    movs r0, #\event
    bl ds100_capture_event
    msr apsr_nzcvqg, r4
    pop {r0-r5, r12, lr}
    ldr pc, =\target
.size \name, .-\name
.ltorg
.endm

SAMPLE ds100_capture_tick,    CAPTURE_TICK,           0x080788a9
SAMPLE ds100_capture_stopped, CAPTURE_DMA_STOPPED,    0x08013793
SAMPLE ds100_capture_result,  CAPTURE_RESULT_READY,   0x0804e01d
SAMPLE ds100_capture_restart, CAPTURE_DMA_RESTARTED,  0x08013789
SAMPLE ds100_capture_ready,   CAPTURE_TRIGGER_READY,  0x08014137

/* The baseline hold can stay in the stock poll wrapper while acquisition is
 * stopped. Check the native tick there too, with a cheap guard in the loop. */
.section .text.ds100_capture_hold_tick,"ax",%progbits
.p2align 2
.global ds100_capture_hold_tick
.type ds100_capture_hold_tick,%function
.thumb_func
ds100_capture_hold_tick:
    ldr r12, =ds100_capture_state
    ldr r0, [r12, #CAPTURE_FLAGS_OFFSET]
    tst r0, #CAPTURE_RUNNING
    beq .Lhold_tail
    ldr r1, =0x20027e90
    ldr r1, [r1]
    ldr r0, [r12, #CAPTURE_START_OFFSET]
    subs r1, r1, r0
    movw r0, #CAPTURE_WINDOW_TICKS
    cmp r1, r0
    blo .Lhold_tail
    push {r3, lr}
    movs r0, #CAPTURE_TICK
    bl ds100_capture_event
    pop {r3, lr}
.Lhold_tail:
    ldr pc, =0x0807a579
.size ds100_capture_hold_tick, .-ds100_capture_hold_tick
.ltorg

/* All section targets replace C BL calls. IP and APSR are call-clobbered.
 * Disabled/control mode tail-calls without a stack frame. In detailed mode,
 * reserve an aligned outgoing slot for the fifth argument when required. */
.macro TIMED name, target, section, stack_arg=0
.section .text.\name,"ax",%progbits
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    ldr r12, =ds100_capture_state
    ldr r12, [r12, #CAPTURE_FLAGS_OFFSET]
    tst r12, #CAPTURE_SECTIONS
    beq .Ltail\@
.if \section == CAPTURE_QUEUE_WAIT
    /* This void routine waits only for an ACTIVE queue with <1000 B free.
     * The queue is owned by the current GUI task. Only inspect that case. */
    ldr r0, =0x2002ffb8
    ldr r0, [r0]
    cbz r0, .Ltail\@
    ldr r1, [r0, #36] /* mode */
    cmp r1, #2       /* DRAW_ACTIVE */
    bne .Ltail\@
    ldr r1, [r0, #4] /* capacity */
    ldr r2, [r0, #16] /* used */
    subs r1, r1, r2
    cmp.w r1, #1000
    bhs .Ltail\@
.elseif \section == CAPTURE_USB_SERVICE
    /* This void routine serves only queued READ(10) or a pending C0. Empty
     * service checks belong to the enclosing section or the residual. */
    ldr r0, =0x20021515
    ldrb r0, [r0]
    ldr r1, =0x2002d74c
    ldrb r1, [r1]
    tst r0, #0x80
    beq .Lcheck_remote\@
    cmp r1, #0x28
    beq .Lmeasure\@
.Lcheck_remote\@:
    cmp r0, #0x7f
    bne .Ltail\@
    cmp r1, #0xc0
    bne .Ltail\@
.Lmeasure\@:
.endif
    push {r0-r4, lr}
    movs r0, #\section
    bl ds100_capture_switch
    mov r4, r0
    ldrd r0, r1, [sp]
    ldrd r2, r3, [sp, #8]
.if \stack_arg
    ldr r12, [sp, #24]
    sub sp, #8
    str r12, [sp]
.endif
    ldr r12, =\target
    blx r12
.if \stack_arg
    add sp, #8
.endif
    strd r0, r1, [sp]
    strd r2, r3, [sp, #8]
    mov r0, r4
    bl ds100_capture_switch
    pop {r0-r4, pc}
.Ltail\@:
    ldr pc, =\target
.size \name, .-\name
.ltorg
.endm

TIMED ds100_capture_point,   0x0807a9fd, CAPTURE_DRAWING
TIMED ds100_capture_fill,    0x0807aa4d, CAPTURE_DRAWING, 1
TIMED ds100_capture_span,    0x0807aad1, CAPTURE_DRAWING
TIMED ds100_capture_bitmap,  0x0807ab81, CAPTURE_DRAWING, 1
TIMED ds100_capture_clear,   0x0807a117, CAPTURE_DRAWING
TIMED ds100_capture_present, 0x0807a13b, CAPTURE_DRAWING
TIMED ds100_capture_wait,    0x0807a949, CAPTURE_QUEUE_WAIT
TIMED ds100_capture_usb,     0x0807b001, CAPTURE_USB_SERVICE
