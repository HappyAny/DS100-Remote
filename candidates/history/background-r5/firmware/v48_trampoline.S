.syntax unified
.cpu cortex-m4
.thumb
.section .text.ds100_v48_original_scsi,"ax",%progbits
.global ds100_v48_original_scsi
.type ds100_v48_original_scsi,%function
.thumb_func
ds100_v48_original_scsi:
    /* Reproduce the four original bytes replaced by the entry branch. */
    push.w {r0, r4-r11, lr}
    ldr.w pc, =0x0804547b
.size ds100_v48_original_scsi, .-ds100_v48_original_scsi

.section .text.ds100_v48_original_clock,"ax",%progbits
.global ds100_v48_original_clock
.type ds100_v48_original_clock,%function
.thumb_func
ds100_v48_original_clock:
    /* Preserve the original push and its PC-relative literal load. */
    push {r4, r5, r6, lr}
    ldr r4, =0x200203b8
    ldr.w pc, =0x08035ad9
.size ds100_v48_original_clock, .-ds100_v48_original_clock
