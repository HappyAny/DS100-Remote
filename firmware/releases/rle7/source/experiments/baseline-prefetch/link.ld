/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_prefetch_dispatch)
SECTIONS {
    . = 0x0807dc80;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "Use the reserved baseline ring and caller stack.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The prefetch overlay exceeds the reviewed flash range.")
