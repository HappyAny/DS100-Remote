/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_read_baseline_row)
SECTIONS {
    . = 0x0807cc80;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The reader adds no static RAM.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The reader exceeds the reviewed flash range.")
