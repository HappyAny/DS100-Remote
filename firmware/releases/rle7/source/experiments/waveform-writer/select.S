/* SPDX-License-Identifier: GPL-3.0-only */
#include "writer.h"
.syntax unified
.cpu cortex-m4
.thumb
.p2align 2
.global ds100_waveform_writer_select
.type ds100_waveform_writer_select,%function
.thumb_func
ds100_waveform_writer_select:
    ldr r12, =ds100_capture_state
    ldr r12, [r12, #CAPTURE_FLAGS_OFFSET]
    tst r12, #(1 << WRITER_MODE_BIT)   /* Diagnostic action 4. */
    beq .Lprevious
    lsls.w r12, r12, #31   /* CAPTURE_RUNNING. */
    bpl .Lprevious
    b.w ds100_waveform_writer_try
.Lprevious:
    ldr pc, =0x0807d981
.size ds100_waveform_writer_select, .-ds100_waveform_writer_select
.ltorg
