/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_waveform_dispatch)
SECTIONS {
    . = 0x0807d980;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The waveform snapshot is local to the call.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The encoder exceeds the reviewed flash range.")
