"""Build an unverified V4.8 screen experiment. Never copy it to a device automatically."""

from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.firmware import decode, encode, V48_SHA256, V48_BASE
from ds100.thumb import branch
from build_arm import run_linux

PATCH_BASE = 0x08078800
FLASH_END = 0x08080000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background-usb", action="store_true",
                        help="Keep USB active after menu exit and reject USB storage writes.")
    args = parser.parse_args()
    stem = "v48-background-screen" if args.background_usb else "v48-screen"
    source = (ROOT / "atk_dso_v_4_8.bin").read_bytes()
    if hashlib.sha256(source).hexdigest() != V48_SHA256:
        raise ValueError("The input does not match the checked official V4.8 image.")
    output = ROOT / "build/arm"
    output.mkdir(parents=True, exist_ok=True)
    flags = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-ffreestanding", "-fno-builtin",
             "-fno-pic", "-fno-pie", "-ffunction-sections", "-fdata-sections", "-Os", "-g", "-Wall", "-Wextra", "-Werror"]
    if args.background_usb:
        flags.append("-DDS100_BACKGROUND_USB=1")
    objects = []
    for name in ["ds100_remote.c", "ds100_v48_screen.c", "v48_hooks.c", "v48_trampoline.S"]:
        target = f"build/arm/{Path(name).stem}.o"
        run_linux(["arm-none-eabi-gcc", *flags, "-c", "firmware/" + name, "-o", target])
        objects.append(target)
    run_linux(["arm-none-eabi-gcc", "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-nostdlib",
               f"-Wl,--gc-sections,-T,firmware/v48_patch.ld,-Map,build/arm/{stem}.map", *objects,
               "-o", f"build/arm/{stem}.elf"])
    run_linux(["arm-none-eabi-objcopy", "-O", "binary", f"build/arm/{stem}.elf", f"build/arm/{stem}-payload.bin"])
    text = run_linux(["arm-none-eabi-nm", "--defined-only", f"build/arm/{stem}.elf"])
    symbols = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16) & ~1
    payload = (output / f"{stem}-payload.bin").read_bytes()
    plain = decode(source)
    edited = bytearray(plain)
    hooks = []
    hook_sites = [
        (0x08045476, bytes.fromhex("2d e9 f1 4f"), "ds100_v48_scsi_hook", False),
        (0x08016F5C, bytes.fromhex("fe f7 15 fa"), "ds100_v48_poll_wrapper", True),
    ]
    if args.background_usb:
        hook_sites.append((0x08035AD4, bytes.fromhex("70 b5 40 4c"), "ds100_v48_clock_hook", False))
    for address, expected, name, link in hook_sites:
        offset = address - V48_BASE
        if edited[offset:offset + 4] != expected:
            raise ValueError(f"The original instruction at {address:#x} does not match the checked image.")
        replacement = branch(address, symbols[name], link)
        edited[offset:offset + 4] = replacement
        hooks.append({"address": hex(address), "before": expected.hex(), "after": replacement.hex(), "target": hex(symbols[name]), "symbol": name})
    extra_edits = []
    if args.background_usb:
        for address, expected, replacement, purpose in [
            (0x080306D2, bytes.fromhex("14 f0 0a fe"), bytes.fromhex("00 bf 00 bf"),
             "Keep USB active in the USB dialog exit handler."),
            (0x080148D0, bytes.fromhex("30 f0 0b fd"), bytes.fromhex("00 bf 00 bf"),
             "Keep USB active in the shared UI callback referenced at 0x08014ca8."),
            (0x080452EA, bytes.fromhex("33 48"), bytes.fromhex("70 47"),
             "Return from the USB stop wrapper so indirect calls also retain the connection."),
            (0x080454DE, bytes.fromhex("6b d0"), bytes.fromhex("00 bf"),
             "Reject WRITE(10) through the stock unsupported-command path."),
        ]:
            offset = address - V48_BASE
            if edited[offset:offset + len(expected)] != expected:
                raise ValueError(f"The original instruction at {address:#x} does not match the checked image.")
            edited[offset:offset + len(expected)] = replacement
            extra_edits.append({"address": hex(address), "before": expected.hex(),
                                "after": replacement.hex(), "purpose": purpose})
    prefix_change_count = sum(a != b for a, b in zip(plain, edited))
    if prefix_change_count > (24 if args.background_usb else 8):
        raise ValueError("The patch changed bytes outside the checked instruction sites.")
    edited.extend(b"\xff" * (PATCH_BASE - V48_BASE - len(edited)))
    edited.extend(payload)
    edited.extend(b"\xff" * ((-len(edited)) % 128))
    if V48_BASE + len(edited) > FLASH_END:
        raise ValueError("The candidate exceeds the assumed flash range.")
    encoded = encode(bytes(edited))
    if decode(encoded) != edited:
        raise ValueError("The candidate failed its encoding check.")
    candidate_dir = ROOT / "candidates"
    candidate_dir.mkdir(exist_ok=True)
    filename = "DS100-V4.8-" + ("background-screen-r3" if args.background_usb else "screen") + "-UNVERIFIED.bin"
    (candidate_dir / filename).write_bytes(encoded)
    (output / f"{stem}-decoded.bin").write_bytes(edited)
    manifest = {
        "status": "UNVERIFIED - do not treat this image as a tested firmware release",
        "variant": "background-screen" if args.background_usb else "screen",
        "revision": "r3" if args.background_usb else "base-diag",
        "scope": "INFO and screen tiles only. Waveform and run control are not bound to device functions.",
        "source_sha256": V48_SHA256,
        "candidate_file": filename, "candidate_sha256": hashlib.sha256(encoded).hexdigest(),
        "candidate_size_bytes": len(encoded), "decoded_original_bytes_changed": prefix_change_count,
        "payload_base": hex(PATCH_BASE), "payload_size_bytes": len(payload),
        "flash_end_assumption": hex(FLASH_END), "hooks": hooks, "extra_edits": extra_edits,
        "symbols": {name: hex(value) for name, value in symbols.items() if name.startswith("ds100_")},
        "section_sizes": run_linux(["arm-none-eabi-size", f"build/arm/{stem}.elf"]),
        "mechanism": "Queue vendor CDB 0xC0 in the USB interrupt. Read LCD pixels in the existing display loop. Reuse the stock 4096-byte MSC buffer.",
        "limits": [
            "The connected device reports V4.7. This candidate targets the downloaded V4.8 image only.",
            "No MCU flash backup is available. The user has no SWD debugger.",
            "Bootloader 4.0 update validation and recovery are not verified.",
            "The USB-state reuse and display-loop hook require physical-device validation.",
            "Screen scans can contain tearing. The LCD has no latched full-frame snapshot here.",
            "Background USB operation after leaving the stock USB menu is not verified on hardware.",
        ],
        "flashed": False,
    }
    manifest["source_files_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted((ROOT / "firmware").glob("*")) if p.is_file()}
    manifest_name = "background-manifest.json" if args.background_usb else "manifest.json"
    (candidate_dir / manifest_name).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
