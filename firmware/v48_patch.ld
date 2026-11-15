ENTRY(ds100_v48_scsi_hook)
MEMORY { PATCH (rx) : ORIGIN = 0x08078800, LENGTH = 0x7800 }
SECTIONS {
    .text : {
        KEEP(*(.text.ds100_v48_scsi_hook))
        KEEP(*(.text.ds100_v48_poll_wrapper))
        KEEP(*(.text.ds100_v48_clock_hook))
        *(.text*) *(.rodata*)
    } > PATCH
    .data : { *(.data*) } > PATCH
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > PATCH
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
    ASSERT(SIZEOF(.data) == 0, "The patch requires unsupported RAM initialization.")
    ASSERT(SIZEOF(.bss) == 0, "The patch must not reserve unverified RAM.")
    ASSERT(. <= 0x08080000, "The patch exceeds the assumed 512 KiB flash size.")
}
