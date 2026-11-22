/* SPDX-License-Identifier: GPL-3.0-only */
.syntax unified
.cpu cortex-m4
.thumb
.section .text.ds100_prefetch_dispatch,"ax",%progbits
.p2align 2
.global ds100_prefetch_dispatch
.type ds100_prefetch_dispatch,%function
.thumb_func
ds100_prefetch_dispatch:
    cbz r0, .Loriginal
    ldr r12, [r0, #36] /* mode */
    cmp.w r12, #1      /* DRAW_BASELINE */
    bne .Loriginal
    ldr r12, [r0, #12] /* read_at */
    cmp.w r12, #0
    bne.w ds100_prefetch_consume
.Loriginal:
    ldr pc, =0x0807a175
.size ds100_prefetch_dispatch, .-ds100_prefetch_dispatch
.ltorg
