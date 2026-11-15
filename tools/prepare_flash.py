"""Prepare checked local firmware files. Do not open or write a physical device."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.firmware import decode, V48_BASE, V48_SHA256

IMAGES = {
    "r3": ("background-manifest.json", "f8a63360842eb6ffd1052176110afd6cf1eec26713bb155bff12d760be13b1cf", 430592, 24),
    "r4": ("fast-manifest.json", "3f19055fb3dc8ed09a774ba5db94a91f116f9d401c8585af99ad92953eb6e51d", 431744, 23),
    "r5": ("profile-manifest.json", "3a3119e25740ee507ca37bfe0a436011d641b1f240e03667955743b39cb90a9e", 432256, 47),
    "r6": ("controls-manifest.json", "f6c306508b1ac21b6c89042a7758e3ae99b235f9d628af35c4d53dc475efebfb", 433152, 56),
    "r7": ("draw-stream-manifest.json", "2b9d3e0fb31a216948d743cc3418a6fc7f17e2baac8b5005489eb8100826021f", 439552, 69),
    "r8": ("follow-manifest.json", "d08cdf67a81acb9e337b585d049b9f74ca1ca4b9daf3e1d46b96970803775579", 439296, 69),
    "r9": ("bounded-manifest.json", "1d0860de6c338f9314986b517c1e4df7004924f9a1ee7e1e65a42b6a0cb5c0ce", 439296, 69),
    "r10": ("input-ready-manifest.json", "d91e29b4dbb2f2a9e8448a02426103fa91a4fa646641676aff0d205c3322d19e", 439552, 69),
    "r11": ("input-ack-manifest.json", "f5758123281d5cf96f991417447aa41652418af10de68fea6d2a142cf39942e7", 439552, 69),
}


def staging_directory(revision):
    suffix = '-' + IMAGES[revision][1][:12] if revision in ('r6', 'r7', 'r8', 'r9', 'r10', 'r11') else ''
    return ROOT / ('build/flash-stage-' + revision + suffix)


def checked_write(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"The existing local file differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", choices=IMAGES, default="r3")
    args = parser.parse_args()
    manifest_name, accepted_hash, image_size, changed_count = IMAGES[args.revision]
    manifest = json.loads((ROOT / "candidates" / manifest_name).read_text())
    candidate = (ROOT / "candidates" / manifest["candidate_file"]).read_bytes()
    official = (ROOT / "atk_dso_v_4_8.bin").read_bytes()
    if hashlib.sha256(candidate).hexdigest() != accepted_hash:
        raise ValueError("The candidate differs from the reviewed image.")
    if manifest["candidate_sha256"] != accepted_hash or len(candidate) != image_size:
        raise ValueError("The candidate manifest is invalid.")
    if hashlib.sha256(official).hexdigest() != V48_SHA256:
        raise ValueError("The official fallback image does not match the pinned V4.8 image.")
    plain, modified = decode(official), decode(candidate)
    if struct.unpack_from("<II", plain) != struct.unpack_from("<II", modified):
        raise ValueError("The candidate changed the initial stack or reset vector.")
    allowed = set()
    for item in manifest["hooks"] + manifest["extra_edits"]:
        offset = int(item["address"], 16) - V48_BASE
        before, after = bytes.fromhex(item["before"]), bytes.fromhex(item["after"])
        if plain[offset:offset + len(before)] != before or modified[offset:offset + len(after)] != after:
            raise ValueError("A checked patch does not match the candidate.")
        allowed.update(range(offset, offset + len(before)))
    changed = {i for i, (a, b) in enumerate(zip(plain, modified)) if a != b}
    if not changed.issubset(allowed) or len(allowed) != (72 if args.revision in ('r7', 'r8', 'r9', 'r10', 'r11') else 60 if args.revision == 'r6' else 48 if args.revision == 'r5' else 24) or len(changed) != changed_count:
        raise ValueError("The candidate changed bytes outside the reviewed instruction sites.")
    if args.revision in ('r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'r11'):
        _, destination, size, _ = struct.unpack_from('<4I', plain, 0x080782FC - V48_BASE)
        if destination + size != 0x2002FF90 or struct.unpack_from('<I', plain)[0] != 0x2002FF90:
            raise ValueError('The original scatter boundary and stack do not match the reviewed RAM state layout.')
        if args.revision not in ('r7', 'r8', 'r9', 'r10', 'r11') and (manifest.get('ram_state', {}).get('address') != '0x2002ff90' or manifest['ram_state']['used_bytes'] != (96 if args.revision == 'r6' else 64)):
            raise ValueError('The profile RAM state does not match the reviewed layout.')
    if args.revision in ('r7', 'r8', 'r9', 'r10', 'r11'):
        for name, expected in manifest['source_files_sha256'].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise ValueError('A checked stream source changed: ' + name)
        if manifest['ram']['static_tail_limit_bytes'] != 112 or manifest['ram']['queue_bytes'] != 16384:
            raise ValueError('The R7 memory limits differ from the reviewed candidate.')
    output = staging_directory(args.revision)
    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("The local staging path is outside the project.")
    checked_write(output / "candidate/atk_dso.bin", candidate)
    checked_write(output / "official-v48-fallback/atk_dso.bin", official)
    if args.revision in ('r7', 'r8', 'r9', 'r10', 'r11'):
        r6 = (ROOT / 'candidates/DS100-V4.8-background-controls-r6-UNVERIFIED.bin').read_bytes()
        if hashlib.sha256(r6).hexdigest() != IMAGES['r6'][1]:
            raise ValueError('The known R6 checkpoint differs from the checked image.')
        checked_write(output / 'known-working-r6/atk_dso.bin', r6)
    receipt = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "user_authorization": "太慢了，怎么优化到30fps" if args.revision == 'r7' else "所有的按键P A M E 两个拨码的控制逻辑电脑接入，我希望不需要切换usb再退出也能直接接入电脑" if args.revision == 'r6' else "接受风险刷入仅支持读屏的实验版",
        "authorization_basis": "The user requested complete key/wheel control and direct USB connection. This local preflight does not authorize an unattended physical flash." if args.revision == 'r6' else "The existing screen-only experiment authorization includes the requested screen speed optimization.",
        "revision": args.revision,
        "usb_active_core_clock_hz": 168000000,
        "physical_step_waiting_for": "用户重新进入 A+P 独立升级界面。",
        "target": {"original_software_reported": "4.7", "installed_software": "4.8 background-r3", "hardware_reported": "2.4", "bootloader_reported": "4.0"},
        "candidate": {"file": "candidate/atk_dso.bin", "sha256": accepted_hash, "size_bytes": len(candidate)},
        "official_v48_fallback": {"file": "official-v48-fallback/atk_dso.bin", "sha256": V48_SHA256,
                                  "size_bytes": len(official), "is_original_device_backup": False},
        "checked_original_bytes_changed": len(changed),
        "device_access_performed": False, "device_writes_performed": False,
        "firmware_flashed": False, "mcu_flash_backed_up": False,
        "note": "The normal USB menu is write protected. Updates use the independent A+P entry. R6 controls and automatic USB startup require physical validation. Its 168 MHz policy also applies on battery. It does not implement a 30 fps mirror.",
    }
    if args.revision == 'r7':
        receipt.update(authorization_basis='Development and local preparation are authorized. The R7 transfer needs explicit approval of this candidate and a fresh A+P screen confirmation.',
            target={'installed_software': '4.8 background-r6-controls', 'hardware_reported': '2.4', 'bootloader_reported': '4.0'},
            known_working_r6={'file': 'known-working-r6/atk_dso.bin', 'sha256': IMAGES['r6'][1], 'is_full_mcu_backup': False},
            note='R7 adds bounded drawing replay, scan-direction tracking, palette RLE, and table CRC. It allocates 4 to 16 KiB from the stock heap. A validated dirty-flag timer changes from 500 to 25 ms only during the stream. STOP or a one-second request lease restores the period and frees the queue. The initial baseline holds GUI rendering. Pixel fidelity and 30 fps require hardware tests.',
            redraw_timer=manifest['redraw_timer'])
    if args.revision == 'r8':
        receipt.update(user_authorization='跟着硬件刷新时间来吧；每次新开机之前的设置就没了，开机一直 WAIT，需要手动 AUTO。',
            authorization_basis='The user requested native refresh and fixes for settings persistence and acquisition startup. The physical transfer requires a fresh A+P screen confirmation.',
            target={'installed_software': '4.8 background-r7-draw-stream', 'hardware_reported': '2.4', 'bootloader_reported': '4.0'},
            known_working_r6={'file': 'known-working-r6/atk_dso.bin', 'sha256': IMAGES['r6'][1], 'is_full_mcu_backup': False},
            note='R8 preserves original refresh timers, keeps FatFS mounted for internal CFG saves, reapplies the current acquisition configuration once after automatic USB initialization, and permits bounded input acknowledgement while draining drawing records. Pixel consistency, cold-start acquisition and setting persistence require physical checks.',
            redraw_timer=manifest['redraw_timer'])
    if args.revision == 'r9':
        r8 = (ROOT / 'candidates/DS100-V4.8-hardware-follow-r8-UNVERIFIED.bin').read_bytes()
        if hashlib.sha256(r8).hexdigest() != IMAGES['r8'][1]:
            raise ValueError('The R8 checkpoint differs from the checked image.')
        checked_write(output / 'installed-r8-checkpoint/atk_dso.bin', r8)
        receipt.update(user_authorization='跟着硬件刷新时间来吧；修复重启设置与 WAIT；关闭自动关机。',
            authorization_basis='The user authorized native-refresh and boot/configuration repairs. A fresh A+P screen confirmation is needed for this corrective update.',
            target={'installed_software': '4.8 background-r8-hardware-follow', 'hardware_reported': '2.4', 'bootloader_reported': '4.0'},
            installed_r8_checkpoint={'file': 'installed-r8-checkpoint/atk_dso.bin', 'sha256': IMAGES['r8'][1], 'is_full_mcu_backup': False},
            note='R9 keeps R8 boot and save repairs. One missed drawing-drain deadline ends capture instead of repeating waits for every record. It preserves native refresh timers. Hardware stability and menu pixel comparison remain pending.',
            redraw_timer=manifest['redraw_timer'])
    if args.revision == 'r10':
        checkpoint = (ROOT/'candidates/DS100-V4.8-bounded-follow-r9-UNVERIFIED.bin').read_bytes()
        if hashlib.sha256(checkpoint).hexdigest() != IMAGES['r9'][1]:
            raise ValueError('The installed R9 checkpoint differs from its checked image.')
        checked_write(output/'installed-r9-checkpoint/atk_dso.bin',checkpoint)
        receipt.update(user_authorization='修复 M/E 保持、拨轮方向、换档偏移和连续平移。',
            authorization_basis='Development and local preparation are authorized. The R10 transfer requires explicit approval of this candidate and a fresh A+P screen confirmation.',
            target={'installed_software':'4.8 background-r9-bounded-follow','hardware_reported':'2.4','bootloader_reported':'4.0'},
            installed_r9_checkpoint={'file':'installed-r9-checkpoint/atk_dso.bin','sha256':IMAGES['r9'][1],'is_full_mcu_backup':False},
            note='Retain virtual wheel events until the validated native widget can accept them. Preserve physical inputs and native timers. Permit one bounded 50 ms drawing drain instead of 15 ms. No extra static RAM. Actual responsiveness and fidelity require physical tests.',
            redraw_timer=manifest['redraw_timer'])
    if args.revision == 'r11':
        checkpoint = (ROOT/'candidates/DS100-V4.8-input-ready-r10-UNVERIFIED.bin').read_bytes()
        if hashlib.sha256(checkpoint).hexdigest() != IMAGES['r10'][1]:
            raise ValueError('The installed R10 checkpoint differs from its checked image.')
        checked_write(output/'installed-r10-checkpoint/atk_dso.bin',checkpoint)
        receipt.update(user_authorization='修复 M/E 漏键、保持期间更新、快捷按钮与连续移动。',
            authorization_basis='Development and preparation are authorized. The physical R11 transfer requires exact-candidate approval and a fresh A+P confirmation.',
            target={'installed_software':'4.8 background-r10-input-ready','hardware_reported':'2.4','bootloader_reported':'4.0'},
            installed_r10_checkpoint={'file':'installed-r10-checkpoint/atk_dso.bin','sha256':IMAGES['r10'][1],'is_full_mcu_backup':False},
            note='Retain R10 wheel readiness and 50 ms bounded draw drain. INPUT acknowledgement also reports physical ownership and key edges actually read by the native controller. Requires client 0.5.5 or newer. No extra static RAM, clock or acquisition change.',
            redraw_timer=manifest['redraw_timer'])
    (output / "preflight.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **receipt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
