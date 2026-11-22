/* SPDX-License-Identifier: GPL-3.0-only */
.syntax unified
.cpu cortex-m4
.thumb

/* Read the existing state through aliases. No new RAM flag and no stack frame.
 * R0-R3, stack arguments and LR reach either target without modification.
 * IP and condition flags are caller-clobbered scratch registers. */
.macro DISPATCH name, slow, original, pass=0
.section .text.\name,"ax",%progbits
.p2align 2
.global \name
.type \name,%function
.thumb_func
\name:
    ldr ip, =ds100_idle_stream_state
    ldr ip, [ip]
    cmp ip, #0
    bne .Lslow\@
    ldr ip, =ds100_idle_profile
    ldr ip, [ip, #4]
    cmp ip, #0
    bne .Lslow\@
.if \pass
    bx lr
.else
    b.w \original
.endif
.Lslow\@:
    b.w \slow
.size \name, .-\name
.ltorg
.endm

DISPATCH ds100_v48_profile_point, ds100_idle_slow_point, ds100_v48_original_point
DISPATCH ds100_v48_profile_rect, ds100_idle_slow_rect, ds100_v48_original_rect
DISPATCH ds100_v48_profile_bitmap, ds100_idle_slow_bitmap, ds100_v48_original_bitmap
DISPATCH ds100_v48_profile_span_a, ds100_idle_slow_span_a, ds100_v48_original_span_a
DISPATCH ds100_v48_profile_span_b, ds100_idle_slow_span_b, ds100_v48_original_span_b
DISPATCH ds100_stream_clear, ds100_idle_slow_clear, ds100_stream_original_clear
DISPATCH ds100_v48_profile_pass, ds100_idle_slow_pass, unused, 1
