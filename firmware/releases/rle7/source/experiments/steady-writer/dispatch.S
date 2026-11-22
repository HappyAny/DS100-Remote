/* SPDX-License-Identifier: GPL-3.0-only */
#include "../acquisition-segments/timing.h"

.syntax unified
.cpu cortex-m4
.thumb

/* The reviewed caller has already checked the waveform geometry. Use IP
 * as scratch and preserve the fifth argument in its original stack slot. */
.p2align 2
.global ds100_steady_writer_entry
.type ds100_steady_writer_entry,%function
.thumb_func
ds100_steady_writer_entry:
    ldr ip, =ds100_steady_capture_flags
    ldr ip, [ip]
    tst ip, #CAPTURE_RUNNING
    bne .Ldiagnostic
    cbz r0, .Loriginal
    ldr ip, [r0, #36]          /* ds100_draw_queue.mode */
    cmp ip, #2               /* DRAW_ACTIVE */
    bne .Loriginal
    ldr ip, [r0, #32]          /* ds100_draw_queue.frame_id */
    cmp ip, #STEADY_FIRST_FRAME
    blo .Loriginal
    push {r3}
    ldr r3, =STEADY_CORE_HZ
    ldr ip, =0x200203cc       /* Native core clock */
    ldr ip, [ip]
    cmp ip, r3
    bne .Lrestore_argument
    ldr ip, =0x200213d0       /* CMSIS core clock */
    ldr ip, [ip]
    cmp ip, r3
.Lrestore_argument:
    pop {r3}
    bne .Loriginal
    b.w ds100_steady_act5_writer
.Loriginal:
    b.w ds100_steady_original_writer
.Ldiagnostic:
    b.w ds100_steady_diagnostic_writer
.size ds100_steady_writer_entry, .-ds100_steady_writer_entry

/* The ring write is committed. In normal operation skip the diagnostic
 * counter altogether. In a diagnostic window replay the displaced MRS. */
.p2align 2
.global ds100_steady_writer_count
.type ds100_steady_writer_count,%function
.thumb_func
ds100_steady_writer_count:
    ldr ip, =ds100_steady_capture_flags
    ldr ip, [ip]
    tst ip, #CAPTURE_RUNNING
    beq .Lrecord_done
    mrs r0, primask
    b.w ds100_steady_count_after_mrs
.Lrecord_done:
    b.w ds100_steady_writer_success
.size ds100_steady_writer_count, .-ds100_steady_writer_count
.ltorg
