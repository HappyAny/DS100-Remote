/* SPDX-License-Identifier: GPL-3.0-only */
.syntax unified
.cpu cortex-m4
.thumb

/* Each site replaces an existing B.W or BL. Resume at its original target
 * with all incoming registers, stack arguments and APSR flags intact. */
.macro SAMPLE name, event, target
.section .text.\name,"ax",%progbits
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    push {r0, r1}
    ldr r0, =ds100_capture_state
    ldr r0, [r0, #8]
    ubfx r0, r0, #0, #1
    cbnz r0, .Lactive\@
    pop {r0, r1}
    ldr pc, =\target
.Lactive\@:
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

SAMPLE ds100_capture_tick,    0, ds100_original_poll
SAMPLE ds100_capture_stopped, 1, 0x08013793
SAMPLE ds100_capture_result,  2, 0x0804e01d
SAMPLE ds100_capture_restart, 3, 0x08013789
SAMPLE ds100_capture_ready,   4, 0x08014137
