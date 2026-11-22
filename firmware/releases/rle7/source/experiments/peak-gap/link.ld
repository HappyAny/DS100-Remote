/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_peak_command)
SECTIONS {
    . = 0x0807f200;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "Reuse the reviewed capture state.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The peak probe exceeds reviewed flash.")
