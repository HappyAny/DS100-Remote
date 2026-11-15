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
    parser.add_argument("--fast-screen", action="store_true",
                        help="Build R4 with ILI9488 burst reads and bounded RLE rows.")
    parser.add_argument("--profile-draws", action="store_true",
                        help="Build R5 to measure actual drawing traffic for a 30 fps design.")
    parser.add_argument("--controls", action="store_true",
                        help="Build R6 with key/wheel input and USB startup in the GUI loop.")
    args = parser.parse_args()
    if args.fast_screen and not args.background_usb:
        parser.error("The fast screen variant requires --background-usb.")
    if args.profile_draws and (not args.background_usb or args.fast_screen):
        parser.error("The draw profile variant requires --background-usb and excludes --fast-screen.")
    if args.controls and not args.profile_draws:
        parser.error("The controls variant requires --profile-draws and --background-usb.")
    stem = "v48-background-screen" if args.background_usb else "v48-screen"
    if args.fast_screen:
        stem = "v48-background-fast"
    if args.profile_draws:
        stem = "v48-background-profile"
    if args.controls:
        stem = "v48-background-controls"
    source = (ROOT / "atk_dso_v_4_8.bin").read_bytes()
    if hashlib.sha256(source).hexdigest() != V48_SHA256:
        raise ValueError("The input does not match the checked official V4.8 image.")
    output = ROOT / "build/arm"
    output.mkdir(parents=True, exist_ok=True)
    flags = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-ffreestanding", "-fno-builtin",
             "-fno-pic", "-fno-pie", "-ffunction-sections", "-fdata-sections", "-Os", "-g", "-Wall", "-Wextra", "-Werror"]
    if args.background_usb:
        flags.append("-DDS100_BACKGROUND_USB=1")
    if args.fast_screen:
        flags.append("-DDS100_FAST_SCREEN=1")
    if args.profile_draws:
        flags.append("-DDS100_DRAW_PROFILE=1")
    if args.controls:
        flags.append("-DDS100_CONTROLS=1")
    objects = []
    sources = ["ds100_remote.c", "ds100_screen_stream.c", "ds100_v48_screen.c", "v48_hooks.c", "v48_trampoline.S"]
    if args.profile_draws:
        sources += ["v48_draw_profile.c", "v48_profile_trampoline.S"]
    if args.controls:
        sources += ["v48_input.c"]
    for name in sources:
        target = f"build/arm/{Path(name).stem}.o"
        run_linux(["arm-none-eabi-gcc", *flags, "-c", "firmware/" + name, "-o", target])
        objects.append(target)
    linker_script = "v48_profile_patch.ld" if args.profile_draws else "v48_patch.ld"
    run_linux(["arm-none-eabi-gcc", "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-nostdlib",
               f"-Wl,--gc-sections,-T,firmware/{linker_script},-Map,build/arm/{stem}.map", *objects,
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
    if args.profile_draws:
        hook_sites.extend([
            (0x0801030E, bytes.fromhex("00 f0 0c f8"), "ds100_v48_controls_init" if args.controls else "ds100_v48_profile_init", True),
            (0x0803CCB8, bytes.fromhex("10 b5 14 46"), "ds100_v48_profile_point", False),
            (0x0803D622, bytes.fromhex("2d e9 f0 4f"), "ds100_v48_profile_rect", False),
            (0x0803D688, bytes.fromhex("2d e9 ff 4f"), "ds100_v48_profile_bitmap", False),
            (0x0804E03C, bytes.fromhex("70 b5 0e 46"), "ds100_v48_profile_span_a", False),
            (0x0804E090, bytes.fromhex("70 b5 0d 46"), "ds100_v48_profile_span_b", False),
        ])
    if args.controls:
        hook_sites.extend([
            (0x08014B90, bytes.fromhex("22 f0 19 f8"), "ds100_v48_read_keys", True),
            (0x08014CE4, bytes.fromhex("21 f0 35 ff"), "ds100_v48_read_wheels", True),
            (0x0804523C, bytes.fromhex("5e 49 10 b5"), "ds100_v48_usb_start", False),
        ])
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
    if prefix_change_count > (60 if args.controls else 48 if args.profile_draws else 24 if args.background_usb else 8):
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
    if args.fast_screen:
        filename = "DS100-V4.8-background-screen-r4-UNVERIFIED.bin"
    if args.profile_draws:
        filename = "DS100-V4.8-background-draw-profile-r5-UNVERIFIED.bin"
    if args.controls:
        filename = "DS100-V4.8-background-controls-r6-UNVERIFIED.bin"
    (candidate_dir / filename).write_bytes(encoded)
    (output / f"{stem}-decoded.bin").write_bytes(edited)
    manifest = {
        "status": "UNVERIFIED - do not treat this image as a tested firmware release",
        "variant": "background-screen" if args.background_usb else "screen",
        "revision": "r6-controls" if args.controls else "r5-profile" if args.profile_draws else "r4-fast" if args.fast_screen else "r3" if args.background_usb else "base-diag",
        "scope": "INFO and screen tiles only. Waveform and run control are not bound to device functions.",
        "source_sha256": V48_SHA256,
        "candidate_file": filename, "candidate_sha256": hashlib.sha256(encoded).hexdigest(),
        "candidate_size_bytes": len(encoded), "decoded_original_bytes_changed": prefix_change_count,
        "payload_base": hex(PATCH_BASE), "payload_size_bytes": len(payload),
        "flash_end_assumption": hex(FLASH_END), "hooks": hooks, "extra_edits": extra_edits,
        "symbols": {name: hex(value) for name, value in symbols.items() if name.startswith("ds100_")},
        "section_sizes": run_linux(["arm-none-eabi-size", f"build/arm/{stem}.elf"]),
        "mechanism": "Queue vendor CDB 0xC0 in the USB interrupt. Read LCD pixels in the existing display loop. Reuse the stock 4096-byte MSC buffer." +
                     (" R4 adds ILI9488 row reads and bounded RLE565 output with complete-row checkpoints." if args.fast_screen else ""),
        "limits": [
            "This patch targets only the pinned V4.8 image. The device originally ran V4.7; R3 is now installed.",
            "No MCU flash backup is available. The user has no SWD debugger.",
            "Bootloader 4.0 accepted R1 through R3. Full official rollback has not been tested.",
            "R3 background USB was verified. Each new image still requires physical-device validation.",
            "Screen scans can contain tearing. The LCD has no latched full-frame snapshot here.",
            "Measurement accuracy and maximum acquisition modes with USB's 168 MHz clock policy are not verified.",
        ],
        "flashed": False,
    }
    manifest["source_files_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted((ROOT / "firmware").glob("*")) if p.is_file()}
    if args.profile_draws:
        manifest["scope"] += " Adds passive draw-traffic counters and a constant-data USB throughput probe. Does not implement a 30 fps mirror."
        manifest["ram_state"] = {"address": "0x2002ff90", "maximum_bytes": 112, "used_bytes": 64,
            "initialization": "Clear after stock heap initialization at 0x0801030e.",
            "proof": "analysis/v4.8/initial-ram-report.json", "heap_allocation": False}
        manifest["limits"].append("The reviewed RAM tail and preservation of real drawing behavior require physical validation. Profiling adds CPU work and cannot alone establish final mirror frame rate.")
    if args.controls:
        manifest['scope'] = 'P/A/M/E press and release, two signed wheel deltas, physical/virtual key merge, input lease, automatic stock USB initialization, passive draw profiling, and legacy screen readback. No 30 fps stream yet.'
        manifest['ram_state']['used_bytes'] = 96
        manifest['limits'].append('USB starts without the menu and holds the 168 MHz policy while initialized, including use on battery. Acquisition performance and clock accuracy require physical validation.')
        manifest['limits'].append('A fully powered-off device cannot receive a software power-on command. Real input and automatic USB startup have not been tested.')
    manifest_name = "controls-manifest.json" if args.controls else "profile-manifest.json" if args.profile_draws else "fast-manifest.json" if args.fast_screen else "background-manifest.json" if args.background_usb else "manifest.json"
    (candidate_dir / manifest_name).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
