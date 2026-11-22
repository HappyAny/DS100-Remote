/* SPDX-License-Identifier: GPL-3.0-only */
MEMORY { PATCH (rx) : ORIGIN = 0x0807EA00, LENGTH = 0x1600 }
SECTIONS {
    .text : { *(.text*) *(.rodata*) } > PATCH
    .data : { *(.data*) } > PATCH
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > PATCH
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
    ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "The boot overlay must reuse the reviewed state.")
}
