ENTRY(ds100_v48_scsi_hook)
MEMORY {
    PATCH (rx) : ORIGIN = 0x08078800, LENGTH = 0x7800
    STATE (rw) : ORIGIN = 0x2002ff90, LENGTH = 0x70
}
SECTIONS {
    .text : {
        KEEP(*(.text.ds100_v48_scsi_hook))
        KEEP(*(.text.ds100_v48_poll_wrapper))
        KEEP(*(.text.ds100_v48_clock_hook))
        KEEP(*(.text.ds100_v48_profile_init))
        KEEP(*(.text.ds100_v48_profile_point))
        KEEP(*(.text.ds100_v48_profile_rect))
        KEEP(*(.text.ds100_v48_profile_bitmap))
        KEEP(*(.text.ds100_v48_profile_span_a))
        KEEP(*(.text.ds100_v48_profile_span_b))
        *(.text*) *(.rodata*)
    } > PATCH
    .data : { *(.data*) } > STATE
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > STATE
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
    ASSERT(SIZEOF(.data) == 0, "Initialized RAM data is not supported.")
    ASSERT(SIZEOF(.bss) <= 0x70, "The profile state exceeds the reviewed RAM tail.")
    ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x08080000, "The patch exceeds the assumed flash range.")
}
