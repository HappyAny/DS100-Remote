"""Create a checked DS100 image from the official V4.8 base and a project patch."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

BASE_SHA = '5030073c8ffe006d3727e4c13b80ca4aa89c3898a539ae7d4e4a2bf8bb216776'
TARGET_SHA = 'f5758123281d5cf96f991417447aa41652418af10de68fea6d2a142cf39942e7'
COMMIT = '46670a349c54e7b9f07a6f7e832abee5dfeb4e13'
BASE_SIZE, TARGET_SIZE = 427752, 439552
PATCHES = {
    'r11': ('background-r11-input-ack', TARGET_SIZE, TARGET_SHA),
    'rle7': ('background-pc-rle7', 457344,
             '23fa1a0fe59254cc1acc3e6591fd0849d432f62136166651be3f5d63496ec13e'),
}
URLS = [
    f'https://raw.githubusercontent.com/alientek-tools/Digital-Oscilloscope_DS100/{COMMIT}/Firmware/atk_dso_v_4_8.bin',
    f'https://cdn.jsdelivr.net/gh/alientek-tools/Digital-Oscilloscope_DS100@{COMMIT}/Firmware/atk_dso_v_4_8.bin',
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def codec(data, encode=False):
    result = bytearray(data)
    for start in range(0, len(data), 128):
        block = data[start:start + 128]
        if len(block) <= 22:
            raise ValueError('The firmware block has no key byte.')
        add = block[22]
        index = add if add < 128 else add >> 1
        if index >= len(block):
            raise ValueError('The firmware block has no indexed key byte.')
        key = block[index]
        for offset, value in enumerate(block):
            if offset not in (22, index):
                result[start + offset] = (((value + add) & 255) ^ key) if encode else (((value ^ key) - add) & 255)
    return bytes(result)


def check_base(base):
    if len(base) != BASE_SIZE or sha(base) != BASE_SHA:
        raise ValueError('The base is not the checked official V4.8 image. No output was written.')


def apply_patch(base, patch_path, *, revision='r11'):
    check_base(base)
    build, target_size, target_sha = PATCHES[revision]
    patch_path = Path(patch_path)
    if patch_path.stat().st_size > 65536:
        raise ValueError('The patch manifest is too large.')
    manifest = json.loads(patch_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('format') != f'DS100-{revision.upper()}-PATCH-1'
            or manifest.get('base_sha256') != BASE_SHA or manifest.get('base_size') != BASE_SIZE
            or manifest.get('build') != build or manifest.get('target_sha256') != target_sha
            or manifest.get('target_size') != target_size):
        raise ValueError(f'The manifest is not the checked {revision} patch.')
    payload_name = manifest.get('payload_file')
    if payload_name != f'{revision}-payload.bin':
        raise ValueError('The payload file name is not valid.')
    payload_path = patch_path.parent / payload_name
    if payload_path.stat().st_size != target_size - BASE_SIZE:
        raise ValueError('The patch payload size differs.')
    payload = payload_path.read_bytes()
    if len(payload) != target_size - BASE_SIZE or sha(payload) != manifest.get('payload_sha256'):
        raise ValueError('The patch payload hash or size differs.')
    plain = codec(base)
    edited = bytearray(plain)
    previous_end, changed = 0, 0
    edits = manifest.get('edits')
    if not isinstance(edits, list) or not 1 <= len(edits) <= 72:
        raise ValueError('The patch edit list is not valid.')
    for edit in edits:
        offset = edit['offset']
        before, after = bytes.fromhex(edit['before']), bytes.fromhex(edit['after'])
        if (type(offset) is not int or not before or len(before) != len(after)
                or offset < previous_end or offset + len(before) > BASE_SIZE):
            raise ValueError('A patch edit overlaps or exceeds the base image.')
        if plain[offset:offset + len(before)] != before:
            raise ValueError('The original instruction differs from the patch.')
        edited[offset:offset + len(after)] = after
        previous_end = offset + len(before)
        changed += sum(a != b for a, b in zip(before, after))
    if changed != manifest.get('changed_original_bytes'):
        raise ValueError('The original-byte change count differs.')
    edited.extend(payload)
    output = codec(bytes(edited), encode=True)
    if len(output) != target_size or sha(output) != target_sha:
        raise ValueError('The generated image hash differs. No output was written.')
    return output


def download_base():
    errors = []
    for url in URLS:
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'DS100-Remote'})
            with urllib.request.urlopen(request, timeout=25) as response:
                data = response.read(BASE_SIZE + 1)
            if len(data) != BASE_SIZE or sha(data) != BASE_SHA:
                raise ValueError('The download hash differs from the official pin.')
            return data
        except (OSError, ValueError) as error:
            errors.append(type(error).__name__ + ': ' + str(error))
    raise OSError('The official download failed. Use --base with the pinned official file. ' + '; '.join(errors))


def write_image(destination, data):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != data:
            raise FileExistsError('The output already exists with different data.')
    else:
        with destination.open('xb') as stream:
            stream.write(data)
    if destination.read_bytes() != data:
        raise OSError('The output readback differs.')


def main(default_revision='r11'):
    frozen = getattr(sys, 'frozen', False)
    home = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parents[1]
    patch_root = home if frozen else home / 'firmware/releases'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, help='Use a local official V4.8 image.')
    parser.add_argument('--revision', choices=PATCHES, default=default_revision)
    parser.add_argument('--patch', type=Path, help='Use a local manifest for the selected revision.')
    parser.add_argument('--output', type=Path, default=home / 'firmware-output/atk_dso.bin')
    parser.add_argument('--no-pause', action='store_true')
    args = parser.parse_args()
    code = 0
    try:
        print('Attention: Third-party firmware can damage your device. Use it with care.')
        print('This tool creates a local file. It does not flash a device.')
        if args.base and args.base.stat().st_size != BASE_SIZE:
            raise ValueError('The official base file size differs.')
        base = args.base.read_bytes() if args.base else download_base()
        patch = args.patch or patch_root / args.revision / 'patch.json'
        data = apply_patch(base, patch, revision=args.revision)
        destination = args.output.resolve()
        write_image(destination, data)
        print('Created:', destination)
        print('Build:', PATCHES[args.revision][0])
        print('SHA256:', PATCHES[args.revision][2])
        print('Hardware 2.4 / Bootloader 4.0 tested. Other revisions are not verified.')
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Failed:', error, file=sys.stderr)
        code = 1
    if frozen and not args.no_pause and sys.stdin and sys.stdin.isatty():
        input('Press Enter to close.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
