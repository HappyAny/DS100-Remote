/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(ds100_native_command)
SECTIONS {
    . = 0x0807de00;
    .text : { *(.text*) *(.rodata*) }
    .data : { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "Reuse the marker state.")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The timing patch exceeds the reviewed flash range.")
