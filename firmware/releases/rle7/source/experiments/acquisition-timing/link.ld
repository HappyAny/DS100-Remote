/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_capture_command)
SECTIONS {
    . = 0x0807c780;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The timing patch uses the initialized R23 profile area.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The timing patch exceeds the verified flash range.")
