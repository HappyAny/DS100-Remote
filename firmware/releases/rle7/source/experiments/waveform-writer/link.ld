/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_waveform_writer_select)
SECTIONS {
    . = 0x0807ea00;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The span writer needs no global memory.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The writer exceeds reviewed flash.")
