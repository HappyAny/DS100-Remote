/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_steady_writer_entry)
SECTIONS {
    . = 0x0807fa00;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The writer policy requires no RAM.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The writer policy exceeds reviewed flash.")
