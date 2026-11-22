/* SPDX-License-Identifier: GPL-3.0-only */
/* Tail dispatch leaves all stack arguments and LR in their original places. */
.syntax unified
.thumb
.section .text.ds100_draw_pixels,"ax",%progbits
.global ds100_draw_pixels
.type ds100_draw_pixels,%function
.thumb_func
ds100_draw_pixels:
    cmp r3, #2
    bhi.w ds100_minimal_stable_pixels
    b.w ds100_minimal_tiny_pixels
.size ds100_draw_pixels, .-ds100_draw_pixels
