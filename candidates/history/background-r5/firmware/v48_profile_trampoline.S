.syntax unified
.cpu cortex-m4
.thumb

.macro TRAMP name, target
.section .text.\name,"ax",%progbits
.global \name
.type \name,%function
.thumb_func
\name:
.endm

TRAMP ds100_v48_original_point, 0
    push {r4, lr}
    mov r4, r2
    ldr.w pc, =0x0803ccbd

TRAMP ds100_v48_original_rect, 0
    push.w {r4-r11, lr}
    ldr.w pc, =0x0803d627

TRAMP ds100_v48_original_bitmap, 0
    push.w {r0-r11, lr}
    ldr.w pc, =0x0803d68d

TRAMP ds100_v48_original_span_a, 0
    push {r4, r5, r6, lr}
    mov r6, r1
    ldr.w pc, =0x0804e041

TRAMP ds100_v48_original_span_b, 0
    push {r4, r5, r6, lr}
    mov r5, r1
    ldr.w pc, =0x0804e095
