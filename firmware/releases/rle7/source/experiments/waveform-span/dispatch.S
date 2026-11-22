/* SPDX-License-Identifier: GPL-3.0-only */
#include "span.h"
.syntax unified
.cpu cortex-m4
.thumb
.section .text.ds100_waveform_dispatch,"ax",%progbits
.p2align 2
.global ds100_waveform_dispatch
.type ds100_waveform_dispatch,%function
.thumb_func
ds100_waveform_dispatch:
    movw r12, #WAVEFORM_HEIGHT
    cmp r3, r12
    bne .Loriginal
    ldr r12, [sp]
    cmp.w r12, #0
    beq .Loriginal
    push {r0-r4, lr}
    ldr r3, [sp, #28] /* reverse */
    ldr r4, [sp, #32] /* pixels */
    sub sp, #8
    str r4, [sp]
    bl ds100_waveform_try
    add sp, #8
    cmp r0, #0
    pop {r0-r4, lr}
    bne .Lhandled
.Loriginal:
    /* Replay the replaced push, with the original arguments and stack. */
    push.w {r4-r11, lr}
    ldr pc, =0x08079e0d
.Lhandled:
    bx lr
.size ds100_waveform_dispatch, .-ds100_waveform_dispatch
.ltorg
