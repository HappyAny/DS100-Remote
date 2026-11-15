.syntax unified
.cpu cortex-m4
.thumb
.section .text.ds100_stream_original_clear,"ax",%progbits
.global ds100_stream_original_clear
.type ds100_stream_original_clear,%function
.thumb_func
ds100_stream_original_clear:
    push {r4, r5, r6, lr}
    ldr r6, =0x2002cef0
    ldr.w pc, =0x0803d2cd
.size ds100_stream_original_clear, .-ds100_stream_original_clear

.section .text.ds100_stream_original_scan_dir,"ax",%progbits
.global ds100_stream_original_scan_dir
.type ds100_stream_original_scan_dir,%function
.thumb_func
ds100_stream_original_scan_dir:
    push {r3, r4, r5, r6, r7}
    ldr r2, =0x2002cef0
    ldr.w pc, =0x0803c9e7
.size ds100_stream_original_scan_dir, .-ds100_stream_original_scan_dir

.section .text.ds100_stream_redraw_constructor,"ax",%progbits
.global ds100_stream_redraw_constructor
.type ds100_stream_redraw_constructor,%function
.thumb_func
ds100_stream_redraw_constructor:
    /* Replace only MOV.W r0,#500. Preserve flags and caller registers. */
    mrs r0, APSR
    push {r0, r1, r2, r3, ip, lr}
    mov r0, r4
    bl ds100_stream_register_redraw
    pop {r0, r1, r2, r3, ip, lr}
    msr APSR_nzcvq, r0
    mov.w r0, #500
    bx lr
.size ds100_stream_redraw_constructor, .-ds100_stream_redraw_constructor
